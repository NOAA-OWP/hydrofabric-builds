from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString
from shapely.geometry.base import BaseGeometry

from hydrofabric_builds.crosswalk.fp.fp_crosswalk import (
    _percent_in_buffer,
    build_crosswalk,
    build_crosswalk_from_files,
)


@pytest.fixture
def simple_gdfs() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """
    Simple synthetic data:

    reference_flowpaths:
        ref_id = 1, geometry: (0,0) -> (10,0)

    nwm_flows:
        nwm_id = 100, geometry: (0,0) -> (5,0)
        nwm_id = 101, geometry: (5,0) -> (10,0)
    """
    ref_geom: LineString = LineString([(0.0, 0.0), (10.0, 0.0)])
    reference_flowpaths = gpd.GeoDataFrame(
        {"ref_id": [1]},
        geometry=[ref_geom],
        crs="EPSG:5070",
    )

    seg1: LineString = LineString([(0.0, 0.0), (5.0, 0.0)])
    seg2: LineString = LineString([(5.0, 0.0), (10.0, 0.0)])
    nwm_flows = gpd.GeoDataFrame(
        {"nhd_feature_id": [100, 101]},
        geometry=[seg1, seg2],
        crs="EPSG:5070",
    )

    return reference_flowpaths, nwm_flows


# ----------------------------------------------------------------------
# Utility function tests
# ----------------------------------------------------------------------
def test_percent_in_buffer_full_overlap() -> None:
    """_percent_in_buffer should be 1.0 when the line is fully inside its own buffer."""
    line = LineString([(0.0, 0.0), (10.0, 0.0)])
    buf: BaseGeometry = line.buffer(1.0)

    pct = _percent_in_buffer(line, buf)
    assert np.isclose(pct, 1.0)


def test_percent_in_buffer_zero_length() -> None:
    """_percent_in_buffer should return 0.0 for a zero-length geometry."""
    line = LineString([(0.0, 0.0), (0.0, 0.0)])  # length = 0
    buf: BaseGeometry = line.buffer(1.0)

    pct = _percent_in_buffer(line, buf)
    assert pct == 0.0


# ----------------------------------------------------------------------
# build_crosswalk (in-memory) tests
# ----------------------------------------------------------------------
def test_build_crosswalk_simple_best_segment(simple_gdfs: tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]) -> None:
    """
    One reference line; two NWM segments; both lie entirely inside the buffer.
    We expect exactly one row, with a non-null nwm_id and percent_inside ~ 1.0.
    """
    ref, nwm = simple_gdfs

    crosswalk: pd.DataFrame = build_crosswalk(
        reference_flowpaths=ref,
        nwm_flows=nwm,
        ref_id_col="ref_id",
        nwm_id_col="nhd_feature_id",
        search_radius_m=2.0,  # small buffer is still enough to cover both segments
        percent_inside_min=0.5,  # threshold below 1.0
    )

    assert len(crosswalk) == 1

    row = crosswalk.iloc[0]
    # ref_id should be 1
    assert row["ref_id"] == 1
    # one of the NWM IDs selected (our code picks the first best candidate)
    assert row["nhd_feature_id"] in (100, 101)
    # percent_inside should be 1.0 for a segment perfectly inside the buffer
    assert np.isclose(row["percent_inside"], 1.0, rtol=1e-6)


def test_build_crosswalk_no_candidates() -> None:
    """
    If the reference line has no intersecting NWM segments,

    the row should have NaN for nwm_id and percent_inside.
    """
    ref_geom: LineString = LineString([(0.0, 0.0), (10.0, 0.0)])
    reference_flowpaths = gpd.GeoDataFrame(
        {"ref_id": [1]},
        geometry=[ref_geom],
        crs="EPSG:5070",
    )

    # NWM segment is far away, so no intersection with a small buffer
    nwm_geom: LineString = LineString([(1000.0, 0.0), (1010.0, 0.0)])
    nwm_flows = gpd.GeoDataFrame(
        {"nhd_feature_id": [999]},
        geometry=[nwm_geom],
        crs="EPSG:5070",
    )

    crosswalk: pd.DataFrame = build_crosswalk(
        reference_flowpaths=reference_flowpaths,
        nwm_flows=nwm_flows,
        ref_id_col="ref_id",
        nwm_id_col="nhd_feature_id",
        search_radius_m=2.0,  # small buffer => no intersection
        percent_inside_min=0.1,
    )

    assert len(crosswalk) == 1
    row = crosswalk.iloc[0]
    assert row["ref_id"] == 1
    assert pd.isna(row["nhd_feature_id"])
    assert pd.isna(row["percent_inside"])


# ----------------------------------------------------------------------
# build_crosswalk_from_files tests
# ----------------------------------------------------------------------
def test_build_crosswalk_from_files_parquet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    End-to-end check for build_crosswalk_from_files using parquet files.

    We monkeypatch _validate_and_fix_geometries to be the identity function,
    so the test does not depend on its internal logic.
    """
    # Create simple reference and NWM GeoDataFrames
    ref_geom: LineString = LineString([(0.0, 0.0), (10.0, 0.0)])
    reference_flowpaths = gpd.GeoDataFrame(
        {"ref_id": [1]},
        geometry=[ref_geom],
        crs="EPSG:5070",
    )

    nwm_geom: LineString = LineString([(0.0, 0.0), (10.0, 0.0)])
    nwm_flows = gpd.GeoDataFrame(
        {"nhd_feature_id": [42]},
        geometry=[nwm_geom],
        crs="EPSG:5070",
    )

    # Write them as parquet files
    ref_path = tmp_path / "ref.parquet"
    nwm_path = tmp_path / "nwm.parquet"
    reference_flowpaths.to_parquet(ref_path)
    nwm_flows.to_parquet(nwm_path)

    # Monkeypatch _validate_and_fix_geometries to just return the input GeoDataFrame
    def fake_validate_and_fix(gdf: gpd.GeoDataFrame, geom_type: str = "flowpaths") -> gpd.GeoDataFrame:
        """fake_validate_and_fix should raise an AssertionError"""
        return gdf

    monkeypatch.setattr(
        "hydrofabric_builds.crosswalk.fp.fp_crosswalk._validate_and_fix_geometries",
        fake_validate_and_fix,
    )

    crosswalk: pd.DataFrame = build_crosswalk_from_files(
        reference_path=ref_path,
        nwm_path=nwm_path,
        ref_id_col="ref_id",
        nwm_id_col="nhd_feature_id",
        reference_layer=None,
        nwm_layer=None,
        work_crs="EPSG:5070",
        search_radius_m=5.0,
        percent_inside_min=0.5,
    )

    assert len(crosswalk) == 1
    row = crosswalk.iloc[0]
    assert row["ref_id"] == 1
    assert row["nhd_feature_id"] == 42
    assert row["percent_inside"] <= 1.0
    assert row["percent_inside"] > 0.0
