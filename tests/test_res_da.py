from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import xarray as xr
from pandas.testing import assert_frame_equal
from pyprojroot import here
from shapely import Point

from hydrofabric_builds.lakes.da import (
    _all_level_pool,
    _check_gages_exist,
    _merge,
    _read_adhoc,
    _read_res_index,
)


@pytest.fixture
def res_index_path() -> Path:
    """Real reservoir index is 110 kb"""
    return here() / "tests/data/lakes/reservoir_index_AnA.nc"


def test_merge__mixed() -> None:
    """A mixed case of merging adhoc and reservoir index including:
    - non-duplicated rfc (4)
    - non-duplicaed usgs (2)
    - non-duplicated usace (3)
    - non-duplicaed adhoc rfc (4)
    - duplicated adhoc rfc / index usgs -> chooses adhoc
    - lake in adhoc that is not in nhf lakes -> dropped
    - lake not in res index or adhoc -> gets level pool (1)
    """
    gdf_lakes = gpd.GeoDataFrame(
        geometry=[
            Point(-2035500, 2088294),
            Point(-2050566, 2088080),
            Point(159971, 1135281),
            Point(121487, 2686555),
            Point(56091, 2661978),
            Point(-1655121, 1406319),
            Point(624229, 2753739),
            Point(910763, 2443910),
        ],
        data={
            "nhf_lake_id": [
                1254387044031094,
                1254385525906421,
                1271431333013718,
                127859655967084,
                1278078392476481,
                1261703406200352,
                1278784300184414,
                1277324208337912,
            ],
            "lake_id": [
                "120053476",
                "8932968",
                "1127701",
                "4817675",
                "4943477",
                "9997014",
                "4800002",
                "4800004",
            ],
        },
    )

    df_res_index = pd.DataFrame.from_records(
        [
            {
                "lake_id": "120053476",
                "site_no": "usgs-fake",
                "da_type": 2,
            },  # duplicated - in adhoc
            {"lake_id": "8932968", "site_no": "ILAC1", "da_type": 4},  # RFC - not duplicated
            {"lake_id": "1127701", "site_no": "07344210", "da_type": 2},  # USGS - not duplicated
            {"lake_id": "4817675", "site_no": "MN00585", "da_type": 3},  # USACE - not duplicated
        ]
    )

    df_great_lakes = pd.DataFrame(
        data={"lake_id": ["4800002", "4800004"], "site_no": ["04127885", "04159130"], "da_type": [6, 6]}
    )

    df_adhoc = pd.DataFrame.from_records(
        [
            {
                "lake_id": "120053476",
                "site_no": "STPC1",
                "da_type": 4,
            },  # duplicated - rfc, should be kept
            {"lake_id": "4943477", "site_no": "fake-rfc", "da_type": 4},  # not in index, should be kept
            {"lake_id": "0", "site_no": "null", "da_type": 4},  # not in nhf lakes, should not be kept
        ]
    )

    expected = pd.DataFrame.from_records(
        [
            {
                "nhf_lake_id": 1254387044031094,
                "lake_id": "120053476",
                "site_no": "STPC1",
                "da_type": 4,
            },  # RFC from adhoc
            {
                "nhf_lake_id": 1254385525906421,
                "lake_id": "8932968",
                "site_no": "ILAC1",
                "da_type": 4,
            },  # RFC - not duplicated
            {
                "nhf_lake_id": 1271431333013718,
                "lake_id": "1127701",
                "site_no": "07344210",
                "da_type": 2,
            },  # USGS - not duplicated
            {
                "nhf_lake_id": 127859655967084,
                "lake_id": "4817675",
                "site_no": "MN00585",
                "da_type": 3,
            },  # USACE - not duplicated)
            {
                "nhf_lake_id": 1278078392476481,
                "lake_id": "4943477",
                "site_no": "fake-rfc",
                "da_type": 4,
            },  # adhoc
            {
                "nhf_lake_id": 1261703406200352,
                "lake_id": "9997014",
                "site_no": np.nan,
                "da_type": 1,
            },  # not joined, gets level pool
            {
                "nhf_lake_id": 1278784300184414,
                "lake_id": "4800002",
                "site_no": "04127885",
                "da_type": 6,
            },  # Lake Superior
            {
                "nhf_lake_id": 1277324208337912,
                "lake_id": "4800004",
                "site_no": "04159130",
                "da_type": 6,
            },  # Lake MI/Huron
        ]
    )

    output = _merge(gdf_lakes, df_list=[df_res_index, df_great_lakes, df_adhoc])

    assert_frame_equal(output, expected)


def test_merge__adhoc_dupe() -> None:
    """A single case where lake is present in index and RFC adhoc; adhoc is kept due to higher DA value"""
    gdf_lakes = gpd.GeoDataFrame(
        geometry=[
            Point(-2035500, 2088294),
        ],
        data={
            "nhf_lake_id": [
                1254387044031094,
            ],
            "lake_id": ["120053476"],
        },
    )

    df_res_index = pd.DataFrame.from_records(
        [
            {
                "lake_id": "120053476",
                "site_no": "usgs-fake",
                "da_type": 2,
            },  # duplicated - in adhoc
        ]
    )
    df_adhoc = pd.DataFrame.from_records(
        [
            {
                "lake_id": "120053476",
                "site_no": "STPC1",
                "da_type": 4,
            },  # duplicated - rfc, should be kept
        ]
    )

    expected = pd.DataFrame.from_records(
        [
            {
                "nhf_lake_id": 1254387044031094,
                "lake_id": "120053476",
                "site_no": "STPC1",
                "da_type": 4,
            },  # RFC from adhoc
        ]
    )

    output = _merge(gdf_lakes, df_list=[df_res_index, df_adhoc])

    assert_frame_equal(output, expected)


def test_merge__index_dupe() -> None:
    """A single case where lake is present duplicated in index. Higher DA value (3) is kept. This should not happen."""
    gdf_lakes = gpd.GeoDataFrame(
        geometry=[
            Point(-2035500, 2088294),
        ],
        data={
            "nhf_lake_id": [
                1254387044031094,
            ],
            "lake_id": ["120053476"],
        },
    )

    df_res_index = pd.DataFrame.from_records(
        [
            {
                "lake_id": "120053476",
                "site_no": "level-pool",
                "da_type": 1,
            },  # duplicated
            {
                "lake_id": "120053476",
                "site_no": "usace-fake",
                "da_type": 3,
            },  # duplicated - higher so will be kept
        ]
    )

    expected = pd.DataFrame.from_records(
        [
            {
                "nhf_lake_id": 1254387044031094,
                "lake_id": "120053476",
                "site_no": "usace-fake",
                "da_type": 3,
            },  # higher code
        ]
    )

    output = _merge(gdf_lakes, df_list=[df_res_index])

    assert_frame_equal(output, expected)


def test_read_adhoc() -> None:
    """Read an adhoc with one row and one null row"""
    gdf = gpd.GeoDataFrame(
        geometry=[Point(0, 0), Point(1, 1)],
        data={
            "filename": ["file.csv", "file.csv"],
            "locationId": ["BHDO1OT", "BUCK2OT"],
            "lake_id": [-99999, 486928],
        },
    )
    expected = pd.DataFrame(data={"lake_id": [486928], "site_no": ["BUCK2"], "da_type": [4]})

    output = _read_adhoc(gdf)
    assert_frame_equal(output, expected)


def test_read_res_index(res_index_path: Path) -> None:
    """Read the reservoir index."""
    ds = xr.open_dataset(res_index_path)
    df = _read_res_index(ds)
    assert {"site_no", "lake_id", "da_type"} == set(df.columns.values)
    assert set(df["da_type"].unique().tolist()) == {2, 3, 4}


def test_read_res_index__usgs_fix_list(res_index_path: Path) -> None:
    """Read the reservoir index and prepend a list of USGS site_no with 0."""
    ds = xr.open_dataset(res_index_path)
    df = _read_res_index(ds, usgs_fix_list=["137462010", "208250410", "21556525"])
    assert {"site_no", "lake_id", "da_type"} == set(df.columns.values)
    assert set(df["da_type"].unique().tolist()) == {2, 3, 4}
    assert len(df.loc[df["site_no"].isin(["0137462010", "0208250410", "021556525"])]) == 3


def test_all_level_pool() -> None:
    df = pd.DataFrame(
        data={
            "nhf_lake_id": [1254387044031094, 1277324208337912],
            "lake_id": ["120053476", "4800004"],
            "other_field": [1, 2],
        }
    )
    expected = pd.DataFrame(
        data={
            "nhf_lake_id": [1254387044031094, 1277324208337912],
            "lake_id": ["120053476", "4800004"],
            "site_no": [None, None],
            "da_type": [1, 1],
        }
    )
    output = _all_level_pool(df)
    assert_frame_equal(output, expected)


def test_check_gage_exist__all_exist(caplog: pytest.FixtureDef) -> None:
    """One gage in reservoir DA exists and one reservoir DA does not have gage."""
    df_res_da = pd.DataFrame(
        data={
            "nhf_lake_id": [1273759975268731, 1287496639639737],
            "lake_id": ["120022128", "5198470"],
            "site_no": ["CCKK2OT", None],
            "da_type": [4, 1],
        }
    )
    gdf_gages = gpd.GeoDataFrame(
        data={"site_no": ["CCKK2OT", "450157090060701"], "status": ["RFC", "USGS-active"]}
    )

    _check_gages_exist(df_res_da, gdf_gages, "site_no")

    assert "All gages for reservoir DA lake:gage crosswalk present in gages file." in caplog.text


def test_check_gage_exist__one_missing(caplog: pytest.FixtureDef) -> None:
    """One gage in reservoir DA exists and one gage does not."""
    df_res_da = pd.DataFrame(
        data={
            "nhf_lake_id": [1273759975268731, 1287496639639737],
            "lake_id": ["120022128", "5198470"],
            "site_no": ["CCKK2OT", "missing"],
            "da_type": [4, 4],
        }
    )
    gdf_gages = gpd.GeoDataFrame(
        data={"site_no": ["CCKK2OT", "450157090060701"], "status": ["RFC", "USGS-active"]}
    )

    _check_gages_exist(df_res_da, gdf_gages, "site_no")

    assert "1 (50%) gages for reservoir DA missing from gages layer" in caplog.text


def test_check_gage_exist__all_missing(caplog: pytest.FixtureDef) -> None:
    """All gages reservoir DA missing"""
    df_res_da = pd.DataFrame(
        data={
            "nhf_lake_id": [1273759975268731, 1287496639639737],
            "lake_id": ["120022128", "5198470"],
            "site_no": ["CCKK2OT", "missing"],
            "da_type": [4, 4],
        }
    )
    gdf_gages = gpd.GeoDataFrame(
        data={"site_no": ["CHET1OT", "450157090060701"], "status": ["RFC", "USGS-active"]}
    )

    _check_gages_exist(df_res_da, gdf_gages, "site_no")

    assert "2 (100%) gages for reservoir DA missing from gages layer" in caplog.text
