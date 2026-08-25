from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal
from pyprojroot import here

from hydrofabric_builds.hydrofabric.waterbodies import crosswalk_waterbodies


@pytest.fixture
def rfcda_path() -> Path:
    return here() / "tests/data/waterbodies/sample_rfcda.gpkg"


@pytest.fixture
def rfcda_built_path() -> Path:
    """Builds a sample RFCDA file with 3 dams. Two are on flowpaths and one is on virtual flowpath. We are only testing crosswalk so all other data is null"""
    res_path = here() / "tests/data/waterbodies/sample_rfcda_built.gpkg"
    res = gpd.GeoDataFrame(
        data={
            "dam_id": ["dam1", "dam2", "dam3"],
            "ref_fab_fp": [21, 22, 23],
            "nidid": [None, None, None],
            "osm_ww_poly": [None, None, None],
            "ref_fab_wb": [1, 2, 3],
            "x_x": [None, None, None],
            "y_x": [None, None, None],
            "dam_name": [None, None, None],
            "dam_type": [None, None, None],
            "spillway_type": [None, None, None],
            "spillway_width": [None, None, None],
            "dam_height": [None, None, None],
            "structural_height": [None, None, None],
            "hydraulic_height": [None, None, None],
            "nid_height": [None, None, None],
            "surface_area": [None, None, None],
            "nid_storage": [None, None, None],
            "normal_storage": [None, None, None],
            "max_storage": [None, None, None],
            "hazard": [None, None, None],
            "purposes": [None, None, None],
            "osm_id": [None, None, None],
            "osm_wb_elev": [None, None, None],
            "osm_area_sqkm": [None, None, None],
            "comid": [None, None, None],
            "ref_area_sqkm": [None, None, None],
            "ref_elev": [None, None, None],
            "dam_elev": [None, None, None],
            "x_y": [None, None, None],
            "y_y": [None, None, None],
            "H_m": [None, None, None],
            "LkArea": [None, None, None],
            "LkMxE": [None, None, None],
            "WeirC": [None, None, None],
            "WeirL": [None, None, None],
            "WeirE": [None, None, None],
            "OrficeC": [None, None, None],
            "OrficeA": [None, None, None],
            "OrficeE": [None, None, None],
            "Dam_Length": [None, None, None],
            "ifd": [None, None, None],
        },
        crs=5070,
        geometry=gpd.GeoSeries.from_wkt(
            [
                "POINT (1936871.830521 2367258.374469)",
                "POINT (1937077.427549 2365546.466289)",
                "POINT (1935465.234601 2366870.654129)",
            ]
        ),
    )
    res.to_file(res_path, driver="GPKG")

    return res_path


@pytest.fixture
def hf_path() -> Path:
    """Builds a hydrofabric with flowpaths, virtual flowpaths, and reference table. There are 3 flowpaths and 1 virtual flowpath"""
    path = here() / "tests/data/sample_hf_wb.gpkg"
    fp = gpd.GeoDataFrame(
        data={"fp_id": [1, 2, 3], "dn_nex_id": [2, 2, 1]},
        geometry=gpd.GeoSeries.from_wkt(
            [
                "LINESTRING (1935369.489274 2367408.643409, 1936890.005642 2366237.931903)",
                "LINESTRING (1937135.14989 2368103.743124, 1936890.005642 2366237.931903)",
                "LINESTRING (1936890.005642 2366237.931903, 1937225.41165 2364891.6428)",
            ]
        ),
        crs=5070,
    )
    fp.to_file(path, layer="flowpaths", driver="GPKG", overwrite=True)

    vfp = gpd.GeoDataFrame(
        data={"virtual_fp_id": [1], "dn_virtual_fp_id": [1], "dn_virtual_nex_id": [1]},
        crs=5070,
        geometry=gpd.GeoSeries.from_wkt(
            [
                "LINESTRING (1935466.313336 2366883.599677, 1935836.046674 2367016.601028, 1935836.046674 2367016.601028)"
            ]
        ),
    )
    vfp.to_file(path, layer="virtual_flowpaths", driver="GPKG", overwrite=True)

    ref = gpd.GeoDataFrame(
        data={
            "ref_fp_id": [20, 21, 22, 23],
            "fp_id": [1, 2, 3, None],
            "virtual_fp_id": [None, None, None, 1],
            "div_id": [1, 2, 3, 1],
        }
    )
    ref.to_file(path, layer="reference_flowpaths", driver="GPKG", overwrite=True)

    return path


@pytest.fixture
def expected_wb() -> dict[str, list[int]]:
    return pd.DataFrame(
        data={
            "wb_id": [1, 2, 3],
            "fp_id": [2, 3, None],
            "virtual_fp_id": [None, None, 1],
            "ref_fp_id": [21, 22, 23],
        }
    )


def test_crosswalk_wb(rfcda_built_path: Path, hf_path: Path, expected_wb: dict[str, list[int]]) -> None:
    """Reservoir crosswalk

    This test includes an RFC-DA dataset with 3 points. 2 points are matched to flowpaths and one to virtual flowpath.
    Only test the crosswalked ID columsn

    Delete the temp files created at the end"""
    try:
        output = crosswalk_waterbodies(hf_path, rfcda_built_path)
        crosswalked = output[["wb_id", "fp_id", "virtual_fp_id", "ref_fp_id"]].copy()
        assert_frame_equal(crosswalked, expected_wb)

    finally:
        hf_path.unlink(missing_ok=True)
        rfcda_built_path.unlink(missing_ok=True)


def test_crosswalk_dedup_by_segment_order(rfcda_path: Path, tmp_path: Path) -> None:
    """When one ref_fp_id maps to two VFPs (different segment_order), crosswalk keeps only the most downstream."""
    path = tmp_path / "hf_dedup.gpkg"

    # Create flowpaths layer with two fp_ids
    fp_id = [1332679, 1333152]
    ref_fp_id = [7047421, 7048917]
    dn_nex_id = [1, 2]
    gdf_fp = gpd.GeoDataFrame(
        data={"fp_id": fp_id, "ref_fp_id": ref_fp_id, "dn_nex_id": dn_nex_id},
        geometry=gpd.GeoSeries.from_wkt(
            [
                "MULTILINESTRING ((-2214.182448 2816190.84186, -2216.797243 2816158.134189))",
                "MULTILINESTRING ((4898.716701 2831686.255371, 4842.711924 2831742.24908, 4758.716004 2831756.251647, 4688.717753 2831812.251735, 4409.708943 2831824.180368, 4379.057025 2831829.187824, 4248.089975 2831891.839528, 4158.249155 2831919.347251, 4037.572909 2831979.469783, 3886.137903 2832009.404255, 3739.929596 2831986.541781, 3624.543109 2831943.621679, 3511.67941 2831930.863148))",
            ]
        ),
        crs=5070,
    )
    gdf_fp.to_file(path, layer="flowpaths")

    # Create reference_flowpaths with one ref_fp_id mapped to two VFPs (different segment_order)
    ref_data = gpd.GeoDataFrame(
        {
            "fp_id": pd.array([1332679, 1332679, 1333152], dtype="Int64"),
            "ref_fp_id": pd.array([7047421, 7047421, 7048917], dtype="Int64"),
            "segment_order": pd.array([0, 2, 0], dtype="Int64"),
            "virtual_fp_id": pd.array([None, None, None], dtype="Float32"),
            "div_id": pd.array([1, 1, 1], dtype="Float32"),
        }
    )
    ref_data.to_file(path, layer="reference_flowpaths")

    # dummy virtual flowpaths
    gpd.GeoDataFrame(data={"virtual_fp_id": [1], "dn_virtual_nex_id": [1], "up_virtual_nex_id": [2]}).to_file(
        path, layer="virtual_flowpaths"
    )

    output = crosswalk_waterbodies(path, rfcda_path)

    # ref_fp_id 7047421 appeared twice; dedup should keep segment_order=2
    matched_7047421 = output[output["ref_fp_id"] == 7047421]
    assert len(matched_7047421) == 1, "Expected dedup to collapse ref_fp_id 7047421 to one row"
