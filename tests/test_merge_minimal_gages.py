from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from hydrofabric_builds.streamflow_gauges.usgs_gages_builder import merge_minimal_gages


@pytest.fixture
def base_crs() -> str:
    return "EPSG:4326"


@pytest.fixture
def gages_empty(base_crs: str) -> gpd.GeoDataFrame:
    cols = ["geometry", "state", "site_no", "name_plain", "name_raw", "description"]
    gdf = gpd.GeoDataFrame({c: [] for c in cols}, geometry="geometry", crs=base_crs)
    return gdf


@pytest.fixture
def gages_seed(base_crs: str) -> gpd.GeoDataFrame:
    gdf = gpd.GeoDataFrame(
        {
            "site_no": ["00000001"],
            "state": ["-"],
            "name_plain": ["-"],
            "name_raw": ["seed-name"],
            "description": ["-"],
            "status": ["TXDOT"],
            "geometry": [Point(-100.0, 40.0)],
        },
        geometry="geometry",
        crs=base_crs,
    )
    return gdf


def test_merge_minimal_appends_and_fills() -> None:
    gages_empty = gpd.GeoDataFrame(
        {
            "geometry": gpd.GeoSeries([], crs="EPSG:4326"),
            "state": pd.Series(dtype=str),
            "site_no": pd.Series(dtype=str),
            "name_plain": pd.Series(dtype=str),
            "name_raw": pd.Series(dtype=str),
            "description": pd.Series(dtype=str),
            "status": pd.Series(dtype=str),  # <- required by merge_minimal_gages
        },
        geometry="geometry",
        crs="EPSG:4326",
    )

    src = gpd.GeoDataFrame(
        {
            "geometry": [Point(-90, 30)],
            "site_no": ["12345678"],
            "station_nm": ["TXDOT gage @ I-10"],
            "status": ["TXDOT"],  # this is ignored by merge_minimal_gages for new rows
        },
        geometry="geometry",
        crs="EPSG:4326",
    )

    from hydrofabric_builds.streamflow_gauges.usgs_gages_builder import merge_minimal_gages

    out = merge_minimal_gages(gages_empty, src, update_existing=False)
    assert len(out) == 1
    r = out.iloc[0]
    assert r.site_no == "12345678"
    assert r.name_plain == "TXDOT gage @ I-10"
    # merge_minimal_gages fills non-mapped columns (including status) with '-'
    assert r.state == r.name_raw == r.description == "-"
    assert r.status == "-"  # <-- important: NOT 'TXDOT' with current implementation


def test_merge_minimal_updates_existing(gages_seed: gpd.GeoDataFrame) -> None:
    src = gpd.GeoDataFrame(
        {
            "geometry": [Point(-101, 41)],
            "site_no": ["00000001"],
            "station_nm": ["updated-name"],
            "status": ["TXDOT"],
        },
        geometry="geometry",
        crs=gages_seed.crs,
    )
    out = merge_minimal_gages(gages_seed, src, update_existing=True)
    r = out.iloc[0]
    assert r.name_plain == "updated-name"
    assert (r.geometry.x, r.geometry.y) == (-101, 41)
