from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal
from pyprojroot import here
from pytest_mock import MockerFixture
from shapely import Point, Polygon

from hydrofabric_builds.config import HFConfig, TaskSelection
from hydrofabric_builds.lakes.lakes import crosswalk_vfp_lk


@pytest.fixture
def nhf() -> Path:
    """Small test case NHF used for lakes"""
    return here() / "tests/data/lakes/nhf_lakes_test.gpkg"


def test_crosswalk_vfp_lk__poly(mocker: MockerFixture, caplog: pytest.FixtureRequest, nhf: Path) -> None:
    """Tests 4 cases for crosswalking virtual flowpath to polygons

    Case 1: 1 poly : 1 VFP (#1)
    Case 2: 1 poly : 2 VFP (#2)
    Case 3: 1 : poly : 0 VFP intersection; retain VFP from flowpath association (#3)
    Case 4: 2 polys intersect same VFP; retain both (#0 and #1)"""
    # cfg only used for default field names
    cfg = HFConfig(
        tasks=TaskSelection(
            lakes=True,
            build_hydrofabric=False,
            divide_attributes=False,
            flowpath_attributes=False,
            gages=False,
            hydrolocations=False,
            fp_crosswalk=False,
            validate_hf=False,
        )
    )
    gdf_vfp = gpd.read_file(nhf, layer="virtual_flowpaths")

    # 4 lake points with corresponding virtual fp ID
    gdf_lakes = gpd.GeoDataFrame(
        data={
            "nhf_lake_id": [0, 1, 2, 3],  # 0 and 1 are nearby and intersect same VFP
            "lake_id": ["0", "10", "20", "30"],
            "virtual_fp_id": [1261203340715366.0, 1261203340715366.0, 1261203514748431.0, 1261203340715366.0],
        },
        crs=5070,
        geometry=[
            Point((-1721772, 1359235)),
            Point((-1721773.761, 1359236.096)),
            Point((-1716134.079, 1360752.333)),
            Point((-1724351.959, 1360146.479)),
        ],
    )

    # 3 lake polygons: polygon 1 has one intersection, polygon 2 has two intersections, polygon 3 has no intersections and retains original associated VFP
    gdf_lake_polys = gpd.GeoDataFrame(
        geometry=[
            Polygon(
                (
                    (-1722122, 1359560),
                    (-1721433, 1359576),
                    (-1721424, 1358891),
                    (-1722118, 1358915),
                    (-1722122, 1359560),
                )
            ),
            Polygon(
                (
                    (-1722134, 1359560),
                    (-1721433, 1359576),
                    (-1721424, 1358891),
                    (-1722118, 1358915),
                    (-1722134, 1359560),
                )
            ),
            Polygon(
                (
                    (-1716756, 1360979),
                    (-1715531, 1361012),
                    (-1715612, 1360496),
                    (-1716660, 1360496),
                    (-1716756, 1360979),
                )
            ),
            Polygon(
                (
                    (-1724883, 1360383),
                    (-1723835, 1360479),
                    (-1723883, 1359883),
                    (-1724819, 1359818),
                    (-1724883, 1360383),
                )
            ),
        ],
        data={"lake_id": ["0", "10", "20", "30"]},
        crs=5070,
    )

    mocker.patch("hydrofabric_builds.lakes.lakes._get_lake_geom", return_value=gdf_lake_polys)

    expected = pd.DataFrame(
        {
            "nhf_lake_id": [0, 1, 2, 2, 3],
            "lake_id": ["0", "10", "20", "20", "30"],
            "virtual_fp_id": [
                1261203340715366.0,
                1261203340715366.0,
                1261203514748431.0,
                1261203455606765.0,
                1261203340715366.0,
            ],
        }
    )

    output = crosswalk_vfp_lk(cfg, gdf_lakes, gdf_vfp)

    assert "All lakes did not intersect virtual flowpaths" in caplog.text
    assert_frame_equal(output, expected, check_exact=True)


def test_crosswalk_vfp_lk__points_mixed(
    mocker: MockerFixture, caplog: pytest.FixtureRequest, nhf: Path
) -> None:
    """Tests crosswalking virtual flowpath to lake point where one point intersects and one point does not intersect (Alaska)"""
    # cfg only used for default field names
    cfg = HFConfig(
        tasks=TaskSelection(
            lakes=True,
            build_hydrofabric=False,
            divide_attributes=False,
            flowpath_attributes=False,
            gages=False,
            hydrolocations=False,
            fp_crosswalk=False,
            validate_hf=False,
        )
    )
    gdf_vfp = gpd.read_file(nhf, layer="virtual_flowpaths")

    # 3 lake points with corresponding virtual fp ID
    gdf_lakes = gpd.GeoDataFrame(
        data={
            "nhf_lake_id": [4, 5],
            "lake_id": ["40", "50"],
            "virtual_fp_id": [1261204768380326.0, 1261203455606765.0],
        },
        crs=5070,
        geometry=[
            Point((-1717413.071685534, 1366279.5299988713)),  # intersects vfp
            Point((-1718508.9, 1364791.1)),  # does not intersect vfp
        ],
    )

    gdf_lake_points = gpd.GeoDataFrame(
        geometry=[
            Point((-1717413.071685534, 1366279.5299988713)),  # intersects vfp
            Point((-1718508.9, 1364791.1)),  # does not intersect vfp
        ],
        data={"lake_id": ["40", "50"]},
        crs=5070,
    )

    mocker.patch("hydrofabric_builds.lakes.lakes._get_lake_geom", return_value=gdf_lake_points)

    expected = pd.DataFrame(
        {
            "nhf_lake_id": [4, 5],
            "lake_id": ["40", "50"],
            "virtual_fp_id": [1261204768380326.0, 1261203455606765.0],
        }
    )

    output = crosswalk_vfp_lk(cfg, gdf_lakes, gdf_vfp)

    assert "All lakes did not intersect virtual flowpaths" in caplog.text
    assert_frame_equal(output, expected, check_exact=True)


def test_crosswalk_vfp_lk__points_all_intersect(
    mocker: MockerFixture, caplog: pytest.FixtureRequest, nhf: Path
) -> None:
    """Tests crosswalking virtual flowpath to lake point where all points intersect. Two points intersect the same VFP (Alaska)"""
    # cfg only used for default field names
    cfg = HFConfig(
        tasks=TaskSelection(
            lakes=True,
            build_hydrofabric=False,
            divide_attributes=False,
            flowpath_attributes=False,
            gages=False,
            hydrolocations=False,
            fp_crosswalk=False,
            validate_hf=False,
        )
    )
    gdf_vfp = gpd.read_file(nhf, layer="virtual_flowpaths")

    gdf_lakes = gpd.GeoDataFrame(
        data={
            "nhf_lake_id": [4, 6, 7],
            "lake_id": ["40", "60", "70"],
            "virtual_fp_id": [1261204768380326.0, 1261204768380326.0, 1261204701378777.0],
        },
        crs=5070,
        geometry=[
            Point((-1717413.071685534, 1366279.5299988713)),  # intersects vfp 1
            Point((-1715326.9287977172, 1364249.5084713956)),  # intersects vfp 1
            Point((-1718777.1819110143, 1366619.340906919)),  # intersects vfp 2
        ],
    )

    gdf_lake_points = gpd.GeoDataFrame(
        geometry=[
            Point((-1717413.071685534, 1366279.5299988713)),  # intersects vfp 1
            Point((-1715326.9287977172, 1364249.5084713956)),  # intersects vfp 1
            Point((-1718777.1819110143, 1366619.340906919)),  # intersects vfp 2
        ],
        data={"lake_id": ["40", "60", "70"]},
        crs=5070,
    )

    mocker.patch("hydrofabric_builds.lakes.lakes._get_lake_geom", return_value=gdf_lake_points)

    expected = pd.DataFrame(
        {
            "nhf_lake_id": [4, 6, 7],
            "lake_id": ["40", "60", "70"],
            "virtual_fp_id": [1261204768380326.0, 1261204768380326.0, 1261204701378777.0],
        }
    )

    output = crosswalk_vfp_lk(cfg, gdf_lakes, gdf_vfp)

    assert "All lakes did not intersect virtual flowpaths" not in caplog.text
    assert_frame_equal(output, expected, check_exact=True)
