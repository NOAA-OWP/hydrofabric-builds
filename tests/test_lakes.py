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
from hydrofabric_builds.helpers.flowpath_association import associate_flowpaths_polygon_graph, make_vfp_graph
from hydrofabric_builds.hydrofabric.lakes import lakes_pipeline
from hydrofabric_builds.lakes.lakes import _dedup_lake_id, _join_nid
from hydrofabric_builds.schemas.hydrofabric import BuildHydrofabricConfig


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
        }
    )
    df.to_csv(nid, index=False)

    return nid


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
        expected_lakes.to_file(tmp_lakes, driver="GPKG")

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

        expected_lakes = gpd.GeoDataFrame(columns=cfg.lakes.fields + ["geometry"], crs=cfg.crs)
        expected_lakes.to_file(tmp_nhf, layer="lakes", driver="GPKG")

        lakes_pipeline(cfg)

        gdf = gpd.read_file(tmp_nhf, layer="lakes")
        assert_geodataframe_equal(gdf, expected_lakes)

    finally:
        tmp_nhf.unlink(missing_ok=True)


def test__run_nwm(main_cfg: HFConfig, lakes_root: Path, dummy_dem: Path, nid: Path) -> None:
    """No files are requested to run and a blank layer is written"""
    try:
        tmp_nhf = lakes_root / "nhf_tmp.gpkg"
        shutil.copy(main_cfg.output_file_path, tmp_nhf)

        cfg = main_cfg.model_copy()
        cfg.output_name = Path(tmp_nhf.name)
        cfg.output_file_path = tmp_nhf
        cfg.lakes.dem.path = dummy_dem
        cfg.lakes.nid.path = nid
        cfg.lakes.nwm.fp_associated_path = lakes_root / "tmp_fp.gpkg"

        tmp_nwm = lakes_root / "tmp_nwm_lakes.gpkg"
        geom = [box(-1718569.5, 1363475.7, -1717437.7, 1363977.3)]
        gdf_nwm_lk = gpd.GeoDataFrame(crs=5070, geometry=geom, data={"newID": [1]})
        gdf_nwm_lk.to_file(tmp_nwm, layer="lakes")
        cfg.lakes.nwm.path = tmp_nwm

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
            },
        )

        gdf_nwm_attr.to_file(tmp_nwm_attr, layer="lakes_attr")
        cfg.lakes.nwm.attrib_src_path = tmp_nwm_attr
        cfg.lakes.nwm.attrib_src_layer = "lakes_attr"
        cfg.lakes.nwm.attrib_src_key = "lake_id"

        cfg.lakes.nwm.run = True
        cfg.lakes.adhoc.run = False
        cfg.lakes.ref_res.run = False
        cfg.lakes.ref_wb.run = False

        lakes_pipeline(cfg)

        expected_lakes = gpd.GeoDataFrame(
            geometry=[geom[0].centroid],
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
                "reservoir_index_AnA": [np.nan],
                "reservoir_index_Extended_AnA": [np.nan],
                "reservoir_index_GDL_AK": [np.nan],
                "reservoir_index_Medium_Range": [np.nan],
                "reservoir_index_Short_Range": [np.nan],
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
        data={"nidid": ["A1"], "dam_name": ["dam_1"], "latitude": [33.79988], "longitude": [-114.80959]}
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
    gdf = gdf[["lake_id", "attrib_src", "dam_id", "nidid", "latitude", "longitude", "geometry"]].copy()

    assert_geodataframe_equal(gdf, expected)


def test_dedup_lake_id__hydroseq(main_cfg: HFConfig) -> None:
    """Same lake_id, both NWM: lower hydroseq (more downstream) is kept."""
    gdf = gpd.GeoDataFrame(
        crs=5070,
        geometry=[Point(-1712832, 1357087), Point(-1712330, 1358180)],
        data={
            "lake_id": ["7", "7"],
            "_hydroseq": [500, 900],
            "attrib_src": ["nwm_lakes.gpkg", "nwm_lakes.gpkg"],
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
            "attrib_src": ["nwm_lakes.gpkg"],
            "dam_id": ["ls-7"],
            "nid": ["A5"],
        },
    )
    result = _dedup_lake_id(main_cfg, gdf)
    result = result[["lake_id", "_hydroseq", "attrib_src", "dam_id", "nid", "geometry"]].copy()
    assert_geodataframe_equal(result, expected, check_like=True)


def test_dedup_lake_id__mixed_sources(main_cfg: HFConfig) -> None:
    """Mixed duplicate sources: NWM priority wins over non-NWM; hydroseq tiebreak among same priority."""
    gdf = gpd.GeoDataFrame(
        crs=5070,
        geometry=[
            Point(-1718569.5, 1363475.7),  # lake 1, NWM
            Point(-1718569.5, 1363475.7),  # lake 1, non-NWM - should be dropped
            Point(-1717881.0, 1363177.0),  # lake 3, non-NWM
            Point(-1717880.0, 1363176.0),  # lake 5, non-NWM
            Point(-1717880.0, 1363176.5),  # lake 6, NWM, hydroseq=200
            Point(-1717880.0, 1363176.5),  # lake 6, non-NWM - should be dropped
            Point(-1712832, 1357087),  # lake 7, NWM, hydroseq=50
            Point(-1712330, 1358180),  # lake 7, NWM, hydroseq=100 - should be dropped
        ],
        data={
            "lake_id": [1, 1, 3, 5, 6, 6, 7, 7],
            "_hydroseq": [50, 50, 100, 150, 200, 200, 50, 100],
            "attrib_src": [
                "nwm_lakes.gpkg",
                None,
                None,
                None,
                "nwm_lakes.gpkg",
                None,
                "nwm_lakes.gpkg",
                "nwm_lakes.gpkg",
            ],
            "dam_id": ["ls-1", "ls-1", "ls-2", "ls-4", None, "ls-6", "ls-7", "ls-8"],
            "nid": ["A1", "A1", "A2", "A3", None, "A4", "A5", "A6"],
        },
    )
    expected = gpd.GeoDataFrame(
        crs=5070,
        geometry=[
            Point(-1718569.5, 1363475.7),  # lake 1, NWM, priority=0, hydroseq=50
            Point(-1712832, 1357087),  # lake 7, NWM, priority=0, hydroseq=50
            Point(-1717880.0, 1363176.5),  # lake 6, NWM, priority=0, hydroseq=200
            Point(-1717881.0, 1363177.0),  # lake 3, non-NWM, priority=1, hydroseq=100
            Point(-1717880.0, 1363176.0),  # lake 5, non-NWM, priority=1, hydroseq=150
        ],
        data={
            "lake_id": [1, 7, 6, 3, 5],
            "attrib_src": [
                "nwm_lakes.gpkg",
                "nwm_lakes.gpkg",
                "nwm_lakes.gpkg",
                None,
                None,
            ],
            "dam_id": ["ls-1", "ls-7", None, "ls-2", "ls-4"],
            "nid": ["A1", "A5", None, "A2", "A3"],
        },
    )
    result = _dedup_lake_id(main_cfg, gdf)
    result = result[["lake_id", "attrib_src", "dam_id", "nid", "geometry"]].copy()
    assert_geodataframe_equal(result, expected, check_like=True)


def test_join_nid__nwm_exclude(main_cfg: HFConfig) -> None:
    """Non-NWM rows sharing dam_id or nid with an NWM lake are excluded."""
    nid_df = pd.DataFrame(
        data={"nidid": ["A1"], "dam_name": ["dam_1"], "latitude": [33.79988], "longitude": [-114.80959]}
    )
    # NWM lake (lake_id=1) has dam_id="ls-1", nid="A1"
    # Non-NWM lake (lake_id=2) has same dam_id/nid but different lake_id
    # The non-NWM row should be excluded
    res_df = gpd.GeoDataFrame(
        crs=5070,
        geometry=[
            Point(-1718569.5, 1363475.7),  # NWM lake
            Point(-1717881.0, 1363177.0),  # non-NWM lake, same dam/nid
        ],
        data={
            "lake_id": [1, 2],
            "attrib_src": ["nwm_lakes.gpkg", None],
            "dam_id": ["ls-1", "ls-1"],
            "nid": ["A1", "A1"],
        },
    )
    expected = gpd.GeoDataFrame(
        crs=5070,
        geometry=[Point(-1718569.5, 1363475.7)],
        data={
            "lake_id": ["1"],
            "attrib_src": ["nwm_lakes.gpkg"],
            "dam_id": ["ls-1"],
            "nidid": ["A1"],
        },
    )

    gdf = _join_nid(main_cfg, res_df, nid_df)
    gdf = gdf[["lake_id", "attrib_src", "dam_id", "nidid", "geometry"]].copy()
    assert_geodataframe_equal(gdf, expected, check_like=True)


def test_dedup_lake_id__nwm_priority(main_cfg: HFConfig) -> None:
    """Same lake_id: NWM lake (attrib_src set) is kept over non-NWM."""
    gdf = gpd.GeoDataFrame(
        crs=5070,
        geometry=[Point(-1717881.0, 1363177.0), Point(-1717882.109, 1363178.697)],
        data={
            "lake_id": [1, 1],
            "_hydroseq": [100, 200],
            "attrib_src": [None, "nwm_lakes.gpkg"],
            "dam_id": ["ls-1", None],
            "nid": ["A1", None],
        },
    )
    expected = gpd.GeoDataFrame(
        crs=5070,
        geometry=[Point(-1717882.109, 1363178.697)],
        data={
            "lake_id": [1],
            "_hydroseq": [200],
            "attrib_src": ["nwm_lakes.gpkg"],
            "dam_id": [None],
            "nid": [None],
        },
    )
    result = _dedup_lake_id(main_cfg, gdf)
    result = result[["lake_id", "_hydroseq", "attrib_src", "dam_id", "nid", "geometry"]].copy()
    assert_geodataframe_equal(result, expected, check_like=True)


def test_join_nid__nwm_skip(main_cfg: HFConfig) -> None:
    """NWM lakes skip NID merge and are preserved as-is."""
    nid_df = pd.DataFrame(
        data={"nidid": ["A1"], "dam_name": ["dam_1"], "latitude": [33.79988], "longitude": [-114.80959]}
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
            "nid": ["A1", "A2"],
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
            "nidid": ["A2", "A1"],  # non-NWM nid=A2 survives rename; NWM has nid=A1
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
