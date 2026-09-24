from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from geopandas.testing import assert_geodataframe_equal
from shapely.geometry import Point

from hydrofabric_builds.streamflow_gauges.assign_fp_to_gage import override_flowpath_id


@pytest.fixture
def gages() -> gpd.GeoDataFrame:
    """Gages with algorithmically assigned flowpath IDs."""
    return gpd.GeoDataFrame(
        {
            "site_no": ["01234567", "76543210"],
            "fp_id": pd.Series([100, 300], dtype=pd.Int64Dtype()),
            "virtual_fp_id": pd.Series([101, 301], dtype=pd.Int64Dtype()),
            "geometry": [Point(-90, 30), Point(-91, 31)],
        },
        geometry="geometry",
        crs="EPSG:4326",
    )


@pytest.fixture
def flowpath_overrides() -> pd.DataFrame:
    """Hardcoded flowpath IDs for one gage."""
    return pd.DataFrame(
        {
            "site_no": ["01234567"],
            "fp_id": pd.Series([200], dtype=pd.Int64Dtype()),
            "virtual_fp_id": pd.Series([201], dtype=pd.Int64Dtype()),
        }
    )


@pytest.fixture
def expected_gages() -> gpd.GeoDataFrame:
    """Gages expected after applying the hardcoded flowpath IDs."""
    return gpd.GeoDataFrame(
        {
            "site_no": ["01234567", "76543210"],
            "fp_id": pd.Series([200, 300], dtype=pd.Int64Dtype()),
            "virtual_fp_id": pd.Series([201, 301], dtype=pd.Int64Dtype()),
            "geometry": [Point(-90, 30), Point(-91, 31)],
        },
        geometry="geometry",
        crs="EPSG:4326",
    )


def test_override_flowpath_id(
    gages: gpd.GeoDataFrame,
    flowpath_overrides: pd.DataFrame,
    expected_gages: gpd.GeoDataFrame,
) -> None:
    result = override_flowpath_id(gages, flowpath_overrides)

    assert_geodataframe_equal(result, expected_gages)
