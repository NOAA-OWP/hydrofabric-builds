import shutil
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import rasterio
import rustworkx as rx
from geopandas.testing import assert_geodataframe_equal
from pyprojroot import here
from rasterio.transform import from_bounds
from shapely import Point, box

from hydrofabric_builds.config import HFConfig, TaskSelection
from hydrofabric_builds.helpers.flowpath_association import (
    associate_flowpaths_polygon_graph,
    make_vfp_graph,
)
from hydrofabric_builds.hydrofabric.lakes import lakes_pipeline
from hydrofabric_builds.lakes.lakes import _dedup_lake_id, _join_nid
from hydrofabric_builds.schemas.hydrofabric import BuildHydrofabricConfig

FT_TO_M = 0.3048


@pytest.fixture
def lakes_root() -> Path:
    return here() / "tests/data/lakes"


@pytest.fixture
def main_lakes_nhf() -> str:
    return "nhf_lakes_test.gpkg"


@pytest.fixture
def dummy_dem(lakes_root: Path) -> Path:
    dem = lakes_root / "dummy_dem.tif"

    bbox = (-1728875.0003, 1342405.00029999, -1701024.9997, 1372475.0003)
    width, height = 1000, 1000
    transform = from_bounds(*bbox, width, height)
    data = np.full((height, width), 10, dtype=np.float32)

    with rasterio.open(
        dem,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype=data.dtype,
        crs=5070,
        transform=transform,
        nodata=-999,
    ) as dst:
        dst.write(data, 1)

    return dem


@pytest.fixture
def graph_vfp(lakes_root: Path, main_lakes_nhf: str) -> tuple[rx.PyDiGraph, dict[str, int], gpd.GeoDataFrame]:
    """Return the lakes graph, ID mapping, and virtual flowpaths GDF for graph testing"""
    vfp = gpd.read_file(lakes_root / main_lakes_nhf, layer="virtual_flowpaths")
    vn = gpd.read_file(lakes_root / main_lakes_nhf, layer="virtual_nexus")
    graph, id_to_idx = make_vfp_graph(vfp=vfp, vn=vn)
    del vn
    return graph, id_to_idx, vfp


@pytest.fixture
def nid(lakes_root: Path) -> Path:
    nid = lakes_root / "nid.csv"
    df = pd.DataFrame(
        data={
            "latitude": [33.746400, 33.748557],
            "longitude": [-114.701015, -114.703772],
            "nidid": ["nid1", "nid2"],
            "dam_name": ["d1", "d2"],
            "dam_type": ["RE", "RE"],
            "spillway_type": ["Orifice", "Orifice"],
            "spillway_width": [50, 50],
            "dam_length": [10, 10],
            "dam_height": [10, 10],
            "structural_height": [10, 10],
            "hydraulic_height": [5, 5],
            "nid_height": [10, 10],
            "surface_area": [100, 100],
            "wb_areasqkm": [100, 100],
            "nid_storage": [100, 100],
            "normal_storage": [100, 100],
            "max_storage": [150, 150],
            "hazard": ["H", "L"],
            "purposes": ["IS", "IS"],
            "DAM_DESIGNER": ["designer1", "designer2_NRCS"],
            "NIDID": ["nid1", "nid2"],
        }
    )
    df.to_csv(nid, index=False)

    return nid


@pytest.fixture
def tmp_nwm_attr(lakes_root: Path) -> Path:
    """Return the path of NWM attributes"""
    tmp_nwm_attr = lakes_root / "tmp_nwm_attr.gpkg"
    gdf_nwm_attr = gpd.GeoDataFrame(
        geometry=[Point(-1718569.5, 1363475.7)],
        crs=5070,
        data={
            "lake_id": [1],
            "res_id": [None],
            "LkArea": [1.0],
            "LkMxE": [2.0],
            "WeirC": [0.4],
            "WeirL": [10.0],
            "OrificeC": [0.1],
            "OrificeA": [1.0],
            "OrificeE": [2.0],
            "WeirE": [2.0],
            "ifd": [0.8999999761581421],
            "Dam_Length": [10.0],
            "reservoir_index_AnA": [np.nan],
            "reservoir_index_Extended_AnA": [np.nan],
            "reservoir_index_GDL_AK": [np.nan],
            "reservoir_index_Medium_Range": [np.nan],
            "reservoir_index_Short_Range": [np.nan],
            "run_of_river": [False],
            "source": ["NWM"],
        },
    )

    gdf_nwm_attr.to_file(tmp_nwm_attr, layer="lakes_attr")
    return tmp_nwm_attr


@pytest.fixture()
def tmp_nwm_geom() -> list[box]:
    """The geometry to be used in fixture tmp_nwm and test test__run_nwm"""
    return [box(-1718569.5, 1363475.7, -1717437.7, 1363977.3)]


@pytest.fixture
def tmp_nwm(lakes_root: Path, tmp_nwm_geom: list[box]) -> Path:
    """A temporary NWM file to go with NWM attributes"""
    tmp_nwm = lakes_root / "tmp_nwm_lakes.gpkg"
    geom = tmp_nwm_geom
    gdf_nwm_lk = gpd.GeoDataFrame(crs=5070, geometry=geom, data={"newID": [1]})
    gdf_nwm_lk.to_file(tmp_nwm, layer="lakes")
    return tmp_nwm


@pytest.fixture
def main_cfg(lakes_root: Path, main_lakes_nhf: str) -> HFConfig:
    return HFConfig(
        output_dir=lakes_root,
        output_name=Path(main_lakes_nhf),
        build=BuildHydrofabricConfig(
            reference_flowpaths_path=str(lakes_root / "reference_flowpaths.parquet")
        ),
        tasks=TaskSelection(
            build_hydrofabric=False,
            divide_attributes=False,
            flowpath_attributes=False,
            gages=False,
            fp_crosswalk=False,
            validate_hf=False,
            hydrolocations=False,
            lakes=True,
        ),
    )


def test__use_cached(main_cfg: HFConfig, lakes_root: Path) -> None:
    """Use a cached lakes layer"""
    try:
        tmp_nhf = lakes_root / "nhf_tmp.gpkg"
        shutil.copy(main_cfg.output_file_path, tmp_nhf)

        tmp_lakes = lakes_root / "tmp_lakes.gpkg"
        expected_lakes = gpd.GeoDataFrame(
            geometry=[Point(-1717880.0, 1363176.0)], data={"lake_id": [1]}, crs=5070
        )
        expected_lakes.to_file(tmp_lakes, layer="lakes", driver="GPKG")

        cfg = main_cfg.model_copy()
        cfg.output_name = Path(tmp_nhf.name)
        cfg.output_file_path = tmp_nhf
        cfg.lakes.use_cached_lakes = True
        cfg.lakes.lakes_path = tmp_lakes

        lakes_pipeline(cfg)

        gdf = gpd.read_file(tmp_nhf, layer="lakes")
        assert_geodataframe_equal(gdf, expected_lakes)

    finally:
        tmp_lakes.unlink(missing_ok=True)
        tmp_nhf.unlink(missing_ok=True)


def test__no_layers(main_cfg: HFConfig, lakes_root: Path) -> None:
    """No files are requested to run and a blank layer is written"""
    try:
        tmp_nhf = lakes_root / "nhf_tmp.gpkg"
        shutil.copy(main_cfg.output_file_path, tmp_nhf)

        cfg = main_cfg.model_copy()
        cfg.output_name = Path(tmp_nhf.name)
        cfg.output_file_path = tmp_nhf
        cfg.lakes.adhoc.run = False
        cfg.lakes.ref_res.run = False
        cfg.lakes.nwm.run = False
        cfg.lakes.ref_wb.run = False
        cfg.lakes.run_of_river.run = False
        cfg.lakes.low_head_dams.run = False

        expected_lakes = gpd.GeoDataFrame(columns=cfg.lakes.fields + ["geometry"], crs=cfg.crs)
        expected_lakes.to_file(tmp_nhf, layer="lakes", driver="GPKG")

        lakes_pipeline(cfg)

        gdf = gpd.read_file(tmp_nhf, layer="lakes")
        assert_geodataframe_equal(gdf, expected_lakes)

    finally:
        tmp_nhf.unlink(missing_ok=True)


def test__run_nwm(
    main_cfg: HFConfig,
    lakes_root: Path,
    dummy_dem: Path,
    nid: Path,
    tmp_nwm_attr: Path,
    tmp_nwm: Path,
    tmp_nwm_geom: list[box],
) -> None:
    """Only NWM lakes are run."""
    try:
        tmp_nhf = lakes_root / "nhf_tmp.gpkg"
        shutil.copy(main_cfg.output_file_path, tmp_nhf)

        cfg = main_cfg.model_copy()
        cfg.output_name = Path(tmp_nhf.name)
        cfg.output_file_path = tmp_nhf
        cfg.lakes.dem.path = dummy_dem
        cfg.lakes.nid.path = nid
        cfg.lakes.nwm.fp_associated_path = lakes_root / "tmp_fp.gpkg"
        cfg.lakes.nwm.path = tmp_nwm

        cfg.lakes.nwm.attrib_src_path = tmp_nwm_attr
        cfg.lakes.nwm.attrib_src_layer = "lakes_attr"
        cfg.lakes.nwm.attrib_src_key = "lake_id"

        cfg.lakes.nwm.run = True
        cfg.lakes.adhoc.run = False
        cfg.lakes.ref_res.run = False
        cfg.lakes.ref_wb.run = False
        cfg.lakes.run_of_river.run = False
        cfg.lakes.low_head_dams.run = False

        lakes_pipeline(cfg)

        expected_lakes = gpd.GeoDataFrame(
            geometry=[tmp_nwm_geom[0].centroid],
            crs=5070,
            data={
                "nhf_lake_id": [1261204677721496],
                "ref_fp_id": [9999572],
                "fp_id": [np.nan],
                "virtual_fp_id": [1261203455606765],
                "dn_nex_id": [np.nan],
                "dn_virtual_nex_id": [1261203489061613.0],
                "div_id": [1261204749791466.0],
                "dam_id": [None],
                "nidid": [None],
                "lake_id": ["1"],
                "res_id": [None],
                "LkArea": [np.float32(1.0)],
                "LkMxE": [np.float32(2.0)],
                "WeirC": [np.float32(0.4)],
                "WeirL": [np.float32(10.0)],
                "WeirE": [np.float32(2.0)],
                "OrificeC": [np.float32(0.1)],
                "OrificeA": [np.float32(1.0)],
                "OrificeE": [np.float32(2.0)],
                "Dam_Length": [np.float32(10.0)],
                "ifd": [np.float32(0.8999999761581421)],
                "dam_crest_length_m": [None],  # dams are not joined for NWM
                "spillway_width_m": [None],  # dams are not joined for NWM
                "reservoir_index_AnA": [np.nan],
                "reservoir_index_Extended_AnA": [np.nan],
                "reservoir_index_GDL_AK": [np.nan],
                "reservoir_index_Medium_Range": [np.nan],
                "reservoir_index_Short_Range": [np.nan],
                "run_of_river": [False],
                "source": ["NWM"],
            },
        )

        gdf = gpd.read_file(tmp_nhf, layer="lakes")

        assert_geodataframe_equal(gdf, expected_lakes)

    finally:
        tmp_nhf.unlink(missing_ok=True)
        tmp_nwm_attr.unlink(missing_ok=True)
        tmp_nwm.unlink(missing_ok=True)
        dummy_dem.unlink(missing_ok=True)
        nid.unlink(missing_ok=True)
        (lakes_root / "tmp_fp.gpkg").unlink(missing_ok=True)


def test_dedup_lake_id__no_dupes(main_cfg: HFConfig) -> None:
    """No duplicated lake_ids returns the dataframe unchanged."""
    gdf = gpd.GeoDataFrame(
        crs=5070,
        geometry=[Point(-1718569.5, 1363475.7), Point(-1717881.0, 1363177.0)],
        data={
            "lake_id": [1, 2],
            "attrib_src": ["nwm_lakes.gpkg", None],
            "dam_id": ["ls-1", "ls-2"],
            "nid": ["A1", "A2"],
        },
    )
    expected = gdf.copy()
    result = _dedup_lake_id(main_cfg, gdf)
    assert_geodataframe_equal(result, expected, check_like=True)


def test_join_nid__nearest(main_cfg: HFConfig) -> None:
    """When there are lakes with smae NID, keep the nearest"""
    nid_df = pd.DataFrame(
        data={
            "nidid": ["A1"],
            "dam_name": ["dam_1"],
            "latitude": [33.79988],
            "longitude": [-114.80959],
        }
    )
    res_df = gpd.GeoDataFrame(
        crs=5070,
        geometry=[Point(-1717881.0, 1363177.0), Point(-1717882.109, 1363178.697)],
        data={
            "lake_id": [1, 2],
            "attrib_src": [None, None],
            "dam_id": ["ls-1", "ls-1"],
            "nid": ["A1", "A1"],
        },
    )
    expected = gpd.GeoDataFrame(
        crs=5070,
        geometry=[Point(-1717882.109, 1363178.697)],
        data={
            "lake_id": ["2"],
            "attrib_src": [None],
            "dam_id": [
                "ls-1",
            ],
            "nidid": ["A1"],
            "latitude": [33.79988],
            "longitude": [-114.80959],
        },
    )

    gdf = _join_nid(main_cfg, res_df, nid_df)

    # test relevant columns
    gdf = gdf[
        [
            "lake_id",
            "attrib_src",
            "dam_id",
            "nidid",
            "latitude",
            "longitude",
            "geometry",
        ]
    ].copy()

    assert_geodataframe_equal(gdf, expected)


def test_dedup_lake_id__hydroseq(main_cfg: HFConfig) -> None:
    """Same lake_id, both NWM: lower hydroseq (more downstream) is kept."""
    gdf = gpd.GeoDataFrame(
        crs=5070,
        geometry=[Point(-1712832, 1357087), Point(-1712330, 1358180)],
        data={
            "lake_id": ["7", "7"],
            "_hydroseq": [500, 900],
            "source": ["NWM", "NWM"],
            "dam_id": ["ls-7", "ls-8"],
            "nid": ["A5", "A6"],
        },
    )
    expected = gpd.GeoDataFrame(
        crs=5070,
        geometry=[Point(-1712832, 1357087)],
        data={
            "lake_id": ["7"],
            "_hydroseq": [500],
            "source": ["NWM"],
            "dam_id": ["ls-7"],
            "nid": ["A5"],
        },
    )
    result = _dedup_lake_id(main_cfg, gdf)
    result = result[["lake_id", "_hydroseq", "source", "dam_id", "nid", "geometry"]].copy()
    assert_geodataframe_equal(result, expected, check_like=True)


def test_dedup_lake_id__mixed_sources(main_cfg: HFConfig) -> None:
    """Duplicates are determined by source priority and hydroseq.
    ["run_of_river", "adhoc", "low_head_dam", "USBR", "NWM", "ref_res"]
    """
    gdf = gpd.GeoDataFrame(
        crs=5070,
        geometry=[
            Point(-1718569.5, 1363475.7),  # lake 1, NWM remove
            Point(-1718569.5, 1363475.7),  # lake 1, run of river keep
            Point(-1717881.0, 1363177.0),  # lake 3, Adhoc kept
            Point(-1717881.0, 1363177.0),  # lake 3, NWM remove
            Point(-1717880.0, 1363176.0),  # lake 5, low_head_dam kept
            Point(-1717880.0, 1363176.5),  # lake 6, NWM, hydroseq=200
            Point(-1717880.0, 1363176.5),  # lake 6, USBR kept
            Point(-1712832, 1357087),  # lake 7, NWM, hydroseq=50
            Point(-1712330, 1358180),  # lake 7, ref_res remove
        ],
        data={
            "lake_id": [1, 1, 3, 3, 5, 6, 6, 7, 7],
            "_hydroseq": [50, 50, 100, 150, 200, 200, 50, 100, 50],
            "source": [
                "NWM",
                "run_of_river",
                "adhoc",
                "NWM",
                "low_head_dam",
                "NWM",
                "USBR",
                "NWM",
                "ref_res",
            ],
            "dam_id": [
                "ls-1",
                "ls-1",
                "ls-2",
                "ls-4",
                None,
                "ls-6",
                "ls-7",
                "ls-8",
                "ls-9",
            ],
            "nid": ["A1", "A1", "A2", "A3", None, "A4", "A5", "A6", "A7"],
        },
    )
    expected = gpd.GeoDataFrame(
        crs=5070,
        geometry=[
            Point(-1718569.5, 1363475.7),  # lake 1, run of river keep
            Point(-1717881.0, 1363177.0),  # lake 3, Adhoc kept
            Point(-1717880.0, 1363176.0),  # lake 5, low_head_dam kept
            Point(-1717880.0, 1363176.5),  # lake 6, USBR kept
            Point(-1712832, 1357087),  # lake 7, NWM, hydroseq=50
        ],
        data={
            "lake_id": [1, 3, 5, 6, 7],
            "_hydroseq": [50, 100, 200, 50, 100],
            "source": [
                "run_of_river",
                "adhoc",
                "low_head_dam",
                "USBR",
                "NWM",
            ],
            "dam_id": ["ls-1", "ls-2", None, "ls-7", "ls-8"],
            "nid": ["A1", "A2", None, "A5", "A6"],
        },
    )
    result = _dedup_lake_id(main_cfg, gdf)
    result = result[["lake_id", "_hydroseq", "source", "dam_id", "nid", "geometry"]].copy()
    assert_geodataframe_equal(result, expected, check_like=False)


def test_join_nid__nwm_exclude(main_cfg: HFConfig) -> None:
    """Non-NWM rows sharing dam_id with an NWM lake are excluded. NID not retained for NWM."""
    nid_df = pd.DataFrame(
        data={
            "nidid": ["A1"],
            "dam_name": ["dam_1"],
            "latitude": [33.79988],
            "longitude": [-114.80959],
        }
    )
    # NWM lake (lake_id=1) has dam_id="ls-1" (nid would be "A1" but set to null)
    # Non-NWM lake (lake_id=2) has same dam_id/nid but different lake_id
    # The non-NWM row should be excluded
    res_df = gpd.GeoDataFrame(
        crs=5070,
        geometry=[
            Point(-1718569.5, 1363475.7),  # NWM lake
            Point(-1717881.0, 1363177.0),  # non-NWM lake, same dam
        ],
        data={
            "lake_id": [1, 2],
            "attrib_src": ["nwm_lakes.gpkg", None],
            "dam_id": ["ls-1", "ls-1"],
            "nid": [None, "A1"],
        },
    )
    expected = gpd.GeoDataFrame(
        crs=5070,
        geometry=[Point(-1718569.5, 1363475.7)],
        data={
            "lake_id": ["1"],
            "attrib_src": ["nwm_lakes.gpkg"],
            "dam_id": ["ls-1"],
            "nidid": [None],
        },
    )

    gdf = _join_nid(main_cfg, res_df, nid_df)
    gdf = gdf[["lake_id", "attrib_src", "dam_id", "nidid", "geometry"]].copy()
    assert_geodataframe_equal(gdf, expected, check_like=True)


def test_join_nid__nwm_skip(main_cfg: HFConfig) -> None:
    """NWM lakes skip NID merge and are preserved as-is."""
    nid_df = pd.DataFrame(
        data={
            "nidid": ["A1"],
            "dam_name": ["dam_1"],
            "latitude": [33.79988],
            "longitude": [-114.80959],
        }
    )
    # One NWM lake and one non-NWM lake with different lake_ids (dedup_lake_id already ran)
    res_df = gpd.GeoDataFrame(
        crs=5070,
        geometry=[
            Point(-1718569.5, 1363475.7),  # NWM lake
            Point(-1717881.0, 1363177.0),  # non-NWM lake
        ],
        data={
            "lake_id": [1, 2],
            "attrib_src": ["nwm_lakes.gpkg", None],
            "dam_id": ["ls-1", "ls-2"],
            "nid": [None, "A2"],
        },
    )
    expected = gpd.GeoDataFrame(
        crs=5070,
        geometry=[
            Point(-1717881.0, 1363177.0),  # non-NWM lake (res_df first)
            Point(-1718569.5, 1363475.7),  # NWM lake preserved
        ],
        data={
            "lake_id": ["2", "1"],
            "attrib_src": [None, "nwm_lakes.gpkg"],
            "dam_id": ["ls-2", "ls-1"],
            "nidid": ["A2", None],  # non-NWM nid=A2 survives rename; NWM has null NID
        },
    )

    gdf = _join_nid(main_cfg, res_df, nid_df)
    gdf = gdf[["lake_id", "attrib_src", "dam_id", "nidid", "geometry"]].copy()
    assert_geodataframe_equal(gdf, expected, check_like=True)


def test_associate_flowpaths_polygon_graph(
    graph_vfp: tuple[rx.PyDiGraph, dict[str, int], gpd.GeoDataFrame],
) -> None:
    """Associate one flowpath using subgraph method"""
    graph, id_to_idx, vfp = graph_vfp

    geom = [box(-1718569.5, 1363475.7, -1717437.7, 1363977.3)]
    gdf_nwm_lk = gpd.GeoDataFrame(crs=5070, geometry=geom, data={"lake_id": [1]})

    gdf_expected = gdf_nwm_lk.copy()
    gdf_expected["geometry"] = gdf_expected.centroid
    gdf_expected["virtual_fp_id"] = pd.Series([1261203455606765], dtype=pd.Int64Dtype())
    gdf_expected["lake_id"] = gdf_expected["lake_id"].astype(pd.Int64Dtype()).astype(str)

    gdf = associate_flowpaths_polygon_graph(
        gdf_poly=gdf_nwm_lk,
        gdf_vfp=vfp,
        graph=graph,
        id_to_idx=id_to_idx,
        poly_id="lake_id",
        vfp_id="virtual_fp_id",
    )

    assert_geodataframe_equal(gdf, gdf_expected)


def test_join_nid__heights_converted_to_meters(main_cfg: HFConfig) -> None:
    """NID publishes lengths in feet and hydraulics.py reads them as meters.

    An unconverted height puts every derived elevation 3.281 times too far above the
    dam base.
    """
    nid_df = pd.DataFrame(
        data={
            "nidid": ["A1"],
            "dam_name": ["dam_1"],
            "latitude": [33.79988],
            "longitude": [-114.80959],
            "nid_height": [230.0],
            "structural_height": [230.0],
            "hydraulic_height": [205.0],
            "dam_length": [1000.0],
            "spillway_width": [500.0],
        }
    )
    res_df = gpd.GeoDataFrame(
        crs=5070,
        geometry=[Point(-1717881.0, 1363177.0)],
        data={"lake_id": [1], "attrib_src": [None], "dam_id": ["ls-1"], "nid": ["A1"]},
    )

    row = _join_nid(main_cfg, res_df, nid_df).iloc[0]

    assert row["nid_height"] == pytest.approx(230.0 * 0.3048)
    assert row["structural_height"] == pytest.approx(230.0 * 0.3048)
    assert row["hydraulic_height"] == pytest.approx(205.0 * 0.3048)
    assert row["dam_length"] == pytest.approx(1000.0 * 0.3048)
    assert row["spillway_width"] == pytest.approx(500.0 * 0.3048)


def test_join_nid__a_row_carrying_its_own_meters_is_not_converted(
    main_cfg: HFConfig,
) -> None:
    """The run-of-river source supplies meters. The coalesce prefers the row's own
    value, so the NID conversion must not also scale it."""
    nid_df = pd.DataFrame(
        data={
            "nidid": ["A1"],
            "dam_name": ["dam_1"],
            "latitude": [33.79988],
            "longitude": [-114.80959],
            "nid_height": [230.0],
        }
    )
    res_df = gpd.GeoDataFrame(
        crs=5070,
        geometry=[Point(-1717881.0, 1363177.0)],
        data={
            "lake_id": [1],
            "attrib_src": [None],
            "dam_id": ["ls-1"],
            "nid": ["A1"],
            # meters, as run_of_river_dams.gpkg stores them
            "nid_height": [70.104],
        },
    )

    row = _join_nid(main_cfg, res_df, nid_df).iloc[0]

    assert row["nid_height"] == pytest.approx(70.104)


def test__run_run_of_river(
    main_cfg: HFConfig, lakes_root: Path, dummy_dem: Path, nid: Path, tmp_nwm_attr: Path, tmp_nwm: Path
) -> None:
    """Test running run of river only"""
    try:
        temp_nhf = lakes_root / "nhf_ror_temp.gpkg"
        shutil.copy(main_cfg.output_file_path, temp_nhf)

        cfg = main_cfg.model_copy()
        cfg.output_name = Path(temp_nhf.name)
        cfg.output_file_path = temp_nhf
        cfg.lakes.dem.path = dummy_dem
        cfg.lakes.nid.path = nid
        cfg.lakes.run_of_river.fp_associated_path = lakes_root / "tmp_fp.gpkg"

        tmp_ror = lakes_root / "tmp_ror_lakes.gpkg"

        data = {
            "nwps_id": ["WELW1", "RISW1"],
            "nidid": ["WA00098", "WA00084"],
            "lake_id": ["23062422", "ror-WA00084"],
            "dam_name": ["Wells", "Rock Island"],
            "dam_type": ["Gravity", "Gravity"],
            "spillway_type": ["Controlled", "Controlled"],
            "spillway_width": [154.2288, 154.2288],
            "dam_length": [1310.64, 947.3184],
            "dam_height": [48.768, 21.640800000000002],
            "structural_height": [48.768, 18.5928],
            "hydraulic_height": [43.891200000000005, 16.764],
            "nid_height": [48.768, 21.640800000000002],
            "surface_area": [9700.0, 3120.0],
            "wb_areasqkm": [220927.0, 231546.0],
            "nid_storage": [500000.0, 131000.0],
            "normal_storage": [331000.0, 113700.0],
            "max_storage": [500000.0, 131000.0],
            "hazard": ["High", "High"],
            "purpose": ["Hydroelectric", "Hydroelectric"],
        }
        geom = [
            box(-1718569.5, 1363475.7, -1717437.7, 1363977.3),
            box(-1718580.5, 1363480.7, -1717480.7, 1363980.3),
        ]
        gdf_ror_lk = gpd.GeoDataFrame(crs=5070, geometry=geom, data=data)
        gdf_ror_lk.to_file(tmp_ror, layer="run_of_river_dams")

        geom1 = [geom[0].centroid, geom[1].centroid]
        gdf_ror_pt = gpd.GeoDataFrame(crs=5070, geometry=geom1, data=data)
        gdf_ror_pt.to_file(tmp_ror, layer="run_of_river_dams_points")
        cfg.lakes.run_of_river.path = tmp_ror

        cfg.lakes.run_of_river.layer_polygon = "run_of_river_dams"
        cfg.lakes.run_of_river.layer_points = "run_of_river_dams_points"

        cfg.lakes.nwm.path = tmp_nwm
        cfg.lakes.nwm.fp_associated_path = lakes_root / "tmp_fp.gpkg"
        cfg.lakes.nwm.attrib_src_path = tmp_nwm_attr
        cfg.lakes.nwm.attrib_src_layer = "lakes_attr"
        cfg.lakes.nwm.attrib_src_key = "lake_id"

        cfg.lakes.nwm.run = False
        cfg.lakes.adhoc.run = False
        cfg.lakes.ref_res.run = False
        cfg.lakes.ref_wb.run = False
        cfg.lakes.run_of_river.run = True
        cfg.lakes.low_head_dams.run = False

        cfg.lakes.run_of_river.flowpath_association_method = "polygon_outlet"

        lakes_pipeline(cfg)

        expected_lakes = gpd.GeoDataFrame(
            geometry=[geom[1].centroid],
            crs=5070,
            data={
                "nhf_lake_id": [1261204677720650],
                "ref_fp_id": [9999572],
                "fp_id": [np.nan],
                "virtual_fp_id": [1261203455606765],
                "dn_nex_id": [np.nan],
                "dn_virtual_nex_id": [1261203489061613.0],
                "div_id": [1261204749791466.0],
                "dam_id": [None],
                "nidid": [None],
                "lake_id": ["ror-WA00084"],
                "res_id": [None],
                "LkArea": [np.float32(12.626204)],
                "LkMxE": [np.float32(10.0)],
                "WeirC": [np.float32(0.4)],
                "WeirL": [np.float32(10.0)],
                "WeirE": [np.float32(10.0)],
                "OrificeC": [np.float32(0.1)],
                "OrificeA": [np.float32(1.0)],
                "OrificeE": [np.float32(10.0)],
                "Dam_Length": [np.float32(10.0)],
                "ifd": [np.float32(0.899)],
                "dam_crest_length_m": [
                    None
                ],  # these attributes are not retained if _join_nid is not called (ref res not run)
                "spillway_width_m": [
                    None
                ],  # these attributes are not retained if _join_nid is not called (ref res not run)
                "reservoir_index_AnA": [None],
                "reservoir_index_Extended_AnA": [None],
                "reservoir_index_GDL_AK": [None],
                "reservoir_index_Medium_Range": [None],
                "reservoir_index_Short_Range": [None],
                "run_of_river": [True],
                "source": ["run_of_river"],
            },
        )
        gdf = gpd.read_file(temp_nhf, layer="lakes")
        assert_geodataframe_equal(gdf, expected_lakes)

    finally:
        temp_nhf.unlink(missing_ok=True)
        tmp_nwm_attr.unlink(missing_ok=True)
        tmp_nwm.unlink(missing_ok=True)
        tmp_ror.unlink(missing_ok=True)
        dummy_dem.unlink(missing_ok=True)
        nid.unlink(missing_ok=True)
        (lakes_root / "tmp_fp.gpkg").unlink(missing_ok=True)


def test__run_low_head_dam(
    main_cfg: HFConfig, lakes_root: Path, dummy_dem: Path, nid: Path, tmp_nwm_attr: Path, tmp_nwm: Path
) -> None:
    """Test running low head dams only"""
    try:
        temp_nhf = lakes_root / "nhf_lhd_temp.gpkg"
        shutil.copy(main_cfg.output_file_path, temp_nhf)

        cfg = main_cfg.model_copy()
        cfg.output_name = Path(temp_nhf.name)
        cfg.output_file_path = temp_nhf
        cfg.lakes.dem.path = dummy_dem
        cfg.lakes.nid.path = nid
        cfg.lakes.run_of_river.fp_associated_path = lakes_root / "tmp_fp.gpkg"

        tmp_lhd = lakes_root / "tmp_lhd_lakes.gpkg"

        data = {
            "nwps_id": ["WELW1", "RISW1"],
            "nidid": ["WA00098", "WA00084"],
            "lake_id": ["23062422", "nid-WA00084"],
            "dam_name": ["Wells", "Rock Island"],
            "dam_type": ["Gravity", "Gravity"],
            "spillway_type": ["Controlled", "Controlled"],
            "spillway_width": [154.2288, 154.2288],
            "dam_length": [1310.64, 947.3184],
            "dam_height": [48.768, 21.640800000000002],
            "structural_height": [48.768, 18.5928],
            "hydraulic_height": [43.891200000000005, 16.764],
            "nid_height": [48.768, 21.640800000000002],
            "surface_area": [9700.0, 3120.0],
            "wb_areasqkm": [220927.0, 231546.0],
            "nid_storage": [500000.0, 131000.0],
            "normal_storage": [331000.0, 113700.0],
            "max_storage": [500000.0, 131000.0],
            "hazard": ["High", "High"],
            "purpose": ["Hydroelectric", "Hydroelectric"],
        }
        geom = [
            box(-1718569.5, 1363475.7, -1717437.7, 1363977.3),
            box(-1718580.5, 1363480.7, -1717480.7, 1363980.3),
        ]
        gdf_lhd_lk = gpd.GeoDataFrame(crs=5070, geometry=geom, data=data)
        gdf_lhd_lk.to_file(tmp_lhd, layer="low_head_dam")

        geom1 = [geom[0].centroid, geom[1].centroid]
        gdf_lhd_pt = gpd.GeoDataFrame(crs=5070, geometry=geom1, data=data)
        gdf_lhd_pt.to_file(tmp_lhd, layer="low_head_dam_points")
        cfg.lakes.low_head_dams.path = tmp_lhd

        cfg.lakes.low_head_dams.layer_poly = "low_head_dam"
        cfg.lakes.low_head_dams.layer_point = "low_head_dam_points"

        cfg.lakes.nwm.path = tmp_nwm
        cfg.lakes.nwm.fp_associated_path = lakes_root / "tmp_fp.gpkg"
        cfg.lakes.nwm.attrib_src_path = tmp_nwm_attr
        cfg.lakes.nwm.attrib_src_layer = "lakes_attr"
        cfg.lakes.nwm.attrib_src_key = "lake_id"

        cfg.lakes.nwm.run = False
        cfg.lakes.adhoc.run = False
        cfg.lakes.ref_res.run = False
        cfg.lakes.ref_wb.run = False
        cfg.lakes.run_of_river.run = False
        cfg.lakes.low_head_dams.run = True

        cfg.lakes.run_of_river.flowpath_association_method = "polygon_outlet"

        lakes_pipeline(cfg)

        expected_lakes = gpd.GeoDataFrame(
            geometry=[geom[1].centroid],
            crs=5070,
            data={
                "nhf_lake_id": [1261204677720650],
                "ref_fp_id": [9999572],
                "fp_id": [np.nan],
                "virtual_fp_id": [1261203455606765],
                "dn_nex_id": [np.nan],
                "dn_virtual_nex_id": [1261203489061613.0],
                "div_id": [1261204749791466.0],
                "dam_id": [None],
                "nidid": [None],
                "lake_id": ["nid-WA00084"],
                "res_id": [None],
                "LkArea": [np.float32(0.54946)],
                "LkMxE": [np.float32(10.0)],
                "WeirC": [np.float32(0.4)],
                "WeirL": [np.float32(10.0)],
                "WeirE": [np.float32(10.0)],
                "OrificeC": [np.float32(0.1)],
                "OrificeA": [np.float32(1.0)],
                "OrificeE": [np.float32(10.0)],
                "Dam_Length": [np.float32(10.0)],
                "ifd": [np.float32(0.899)],
                "dam_crest_length_m": [
                    None
                ],  # these attributes are not retained if _join_nid is not called (ref res not run)
                "spillway_width_m": [
                    None
                ],  # these attributes are not retained if _join_nid is not called (ref res not run)
                "reservoir_index_AnA": [None],
                "reservoir_index_Extended_AnA": [None],
                "reservoir_index_GDL_AK": [None],
                "reservoir_index_Medium_Range": [None],
                "reservoir_index_Short_Range": [None],
                "run_of_river": [True],
                "source": ["low_head_dam"],
            },
        )
        gdf = gpd.read_file(temp_nhf, layer="lakes")
        assert_geodataframe_equal(gdf, expected_lakes)

    finally:
        temp_nhf.unlink(missing_ok=True)
        tmp_lhd.unlink(missing_ok=True)
        tmp_nwm.unlink(missing_ok=True)
        tmp_nwm_attr.unlink(missing_ok=True)
        dummy_dem.unlink(missing_ok=True)
        nid.unlink(missing_ok=True)
        (lakes_root / "tmp_fp.gpkg").unlink(missing_ok=True)


def test_join_nid__handle_nulls(main_cfg: HFConfig) -> None:
    """
    If there are null NID and dam_id in non-NWM lakes, they should be kept.
    Makes sure string nulls are handled appropriately
    """
    nid_df = pd.DataFrame(
        data={
            "nidid": ["A1"],
            "dam_name": ["dam_1"],
            "latitude": [33.79988],
            "longitude": [-114.80959],
        }
    )
    # One NWM lake and one non-NWM lake with different lake_ids. Dam and NID are null for other dam
    res_df = gpd.GeoDataFrame(
        crs=5070,
        geometry=[
            Point(-1718569.5, 1363475.7),  # NWM lake
            Point(-1717881.0, 1363177.0),  # non-NWM lake
        ],
        data={
            "lake_id": [1, 2],
            "attrib_src": ["nwm_lakes.gpkg", None],
            "dam_id": ["ls-1", None],  # non-NWM has null dam and NID
            "nid": ["A1", None],
        },
    )
    expected = gpd.GeoDataFrame(
        crs=5070,
        geometry=[
            Point(-1717881.0, 1363177.0),  # non-NWM lake (res_df first)
            Point(-1718569.5, 1363475.7),  # NWM lake preserved
        ],
        data={
            "lake_id": ["2", "1"],
            "attrib_src": [None, "nwm_lakes.gpkg"],
            "dam_id": [None, "ls-1"],
            "nidid": [None, None],
        },
    )

    gdf = _join_nid(main_cfg, res_df, nid_df)
    gdf = gdf[["lake_id", "attrib_src", "dam_id", "nidid", "geometry"]].copy()
    assert_geodataframe_equal(gdf, expected, check_like=True)


def test_join_nid__spillway_width_dam_length(main_cfg: HFConfig) -> None:
    """
    Spillway width/dam width should be set to null if zero.
    It should be retained unconverted if it is present in res_df (e.g. ROR, LHD)
    It should be converted from ft_to_meters if it is joined from nid_df
    """
    nid_df = pd.DataFrame(
        data={
            "nidid": ["A1", "A2"],
            "dam_name": ["dam_1", "dam_2"],
            "latitude": [33.79988, 33.9],
            "longitude": [-114.80959, -114.9],
            "dam_type": ["Gravity", "Gravity"],
            "spillway_type": ["Controlled", "Controlled"],
            "spillway_width": [154.2288, 150.0],
            "dam_length": [1310.64, 1310.0],
            "dam_height": [48.768, 30.0],
            "structural_height": [48.768, 30.0],
            "hydraulic_height": [43.891200000000005, 39.0],
            "nid_height": [48.768, 30.0],
            "surface_area": [9700.0, 9700.0],
            "wb_areasqkm": [
                220927.0,
                220927.0,
            ],
            "nid_storage": [500000.0, 500000.0],
            "normal_storage": [331000.0, 331000.0],
            "max_storage": [500000.0, 500000.0],
            "hazard": ["High", "High"],
            "purpose": ["Hydroelectric", "Hydroelectric"],
        }
    )
    # One NWM lake and one non-NWM lake with different lake_ids. Dam and NID are null for other dam
    res_df = gpd.GeoDataFrame(
        crs=5070,
        geometry=[
            Point(-1718569.5, 1363475.7),  # NWM lake
            Point(-1717881.0, 1363177.0),  # non-NWM lake with spillway/dam length (meters) (e.g. ROR/LHD)
            Point(-1723820.15674909, 1375783.4392533072),  # non-NWM lake (ref res) that will be joined to NID
        ],
        data={
            "lake_id": [1, 2, 3],
            "attrib_src": ["nwm_lakes.gpkg", None, None],
            "dam_id": ["ls-1", None, "ls-2"],  # non-NWM has null dam and NID
            "nid": [None, None, "A2"],  # NWM lake has null NID
            "spillway_width": [None, 1.0, None],  # non-NWM lakes has length pre-filled out in meters
            "dam_length": [None, 1310.64, None],  # non-NWM lakes has length pre-filled out in meters
        },
    )
    expected = gpd.GeoDataFrame(
        crs=5070,
        geometry=[
            Point(-1717881.0, 1363177.0),  # non-NWM lake (res_df first)
            Point(-1723820.15674909, 1375783.4392533072),  # non-NWM lake (ref res) that will be joined to NID
            Point(-1718569.5, 1363475.7),  # NWM lake preserved
        ],
        data={
            "lake_id": ["2", "3", "1"],
            "attrib_src": [None, None, "nwm_lakes.gpkg"],
            "dam_id": [None, "ls-2", "ls-1"],
            "nidid": [None, "A2", None],
            "spillway_width_m": [
                1.0,
                (150.0 * FT_TO_M),
                np.nan,
            ],  # first is retained as meters, secton is converted ft to m
            "dam_crest_length_m": [
                1310.64,
                (1310.0 * FT_TO_M),
                np.nan,
            ],  # first is retained as meters, secton is converted ft to m
        },
    )

    gdf = _join_nid(main_cfg, res_df, nid_df)
    gdf = gdf[
        ["lake_id", "attrib_src", "dam_id", "nidid", "spillway_width_m", "dam_crest_length_m", "geometry"]
    ].copy()
    assert_geodataframe_equal(gdf, expected)


def test_join_nid__spillway_width_zero_to_null(main_cfg: HFConfig) -> None:
    """Spillway width/dam width should be set to null if zero."""
    nid_df = pd.DataFrame(
        data={
            "nidid": ["A1", "A2"],
            "dam_name": ["dam_1", "dam_2"],
            "latitude": [33.79988, 33.9],
            "longitude": [-114.80959, -114.9],
            "dam_type": ["Gravity", "Gravity"],
            "spillway_type": ["Controlled", "Controlled"],
            "spillway_width": [0, 0],  # zero to nan
            "dam_length": [0, 0],  # zero to nan
            "dam_height": [48.768, 30.0],
            "structural_height": [48.768, 30.0],
            "hydraulic_height": [43.891200000000005, 39.0],
            "nid_height": [48.768, 30.0],
            "surface_area": [9700.0, 9700.0],
            "wb_areasqkm": [
                220927.0,
                220927.0,
            ],
            "nid_storage": [500000.0, 500000.0],
            "normal_storage": [331000.0, 331000.0],
            "max_storage": [500000.0, 500000.0],
            "hazard": ["High", "High"],
            "purpose": ["Hydroelectric", "Hydroelectric"],
        }
    )
    # One NWM lake and one non-NWM lake with different lake_ids. Dam and NID are null for other dam
    res_df = gpd.GeoDataFrame(
        crs=5070,
        geometry=[
            Point(-1723820.15674909, 1375783.4392533072),  # non-NWM lake (ref res) that will be joined to NID
        ],
        data={
            "lake_id": [3],
            "attrib_src": [None],
            "dam_id": ["ls-2"],
            "nid": ["A2"],
            "spillway_width": [0.0],  # 0 will be converted to nan
            "dam_length": [0.0],  # 0 will be converted to nan
        },
    )
    expected = gpd.GeoDataFrame(
        crs=5070,
        geometry=[
            Point(-1723820.15674909, 1375783.4392533072),  # non-NWM lake (ref res) that will be joined to NID
        ],
        data={
            "lake_id": ["3"],
            "attrib_src": [None],
            "dam_id": ["ls-2"],
            "nidid": ["A2"],
            "spillway_width_m": [np.nan],
            "dam_crest_length_m": [np.nan],
        },
    )

    gdf = _join_nid(main_cfg, res_df, nid_df)
    gdf = gdf[
        ["lake_id", "attrib_src", "dam_id", "nidid", "spillway_width_m", "dam_crest_length_m", "geometry"]
    ].copy()
    assert_geodataframe_equal(gdf, expected)
