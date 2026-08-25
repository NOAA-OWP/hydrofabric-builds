import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from hydrofabric_builds.streamflow_gauges.usgs_gages_builder import add_missing_usgs_sites


def _fake_nwis_table() -> pd.DataFrame:
    # Mimic cleaned NWIS table
    return pd.DataFrame(
        {
            "agency_cd": ["USGS", "USGS"],
            "site_no": ["01000000", "02000000"],
            "station_nm": ["Alpha", "Beta"],
            "dec_lat_va": [40.0, 41.0],
            "dec_long_va": [-100.0, -101.0],
            "state_cd": ["01", "02"],
            "county_cd": ["001", "002"],
            "status": ["USGS-active", "USGS-active"],
        }
    )


def test_add_missing_usgs_sites_adds_and_fills(monkeypatch: pytest.MonkeyPatch) -> None:
    # Monkeypatch network fetch
    def fake_read_csv(
        url: str, sep: str = "\t", comment: str = "#", dtype: type[str] = str, engine: str = "python"
    ) -> pd.DataFrame:
        """fake csv reader"""
        return _fake_nwis_table()

    monkeypatch.setattr(pd, "read_csv", fake_read_csv)

    # Empty gages with REQUIRED columns (including 'status')
    gages_empty = gpd.GeoDataFrame(
        {
            "geometry": [],
            "state": [],
            "site_no": [],
            "name_plain": [],
            "name_raw": [],
            "description": [],
            "status": [],
        },
        geometry="geometry",
        crs="EPSG:4326",
    )

    incoming_ids = ["01000000", "02DC012", "02000000"]

    g_updated, usgs_ids, non_usgs, fetched = add_missing_usgs_sites(gages_empty, incoming_ids)

    # Partition
    assert set(usgs_ids) == {"01000000", "02000000"}
    assert set(non_usgs) == {"02DC012"}

    # Two sites added
    assert set(g_updated["site_no"]) == {"01000000", "02000000"}

    # name_plain from station_nm
    assert set(g_updated["name_plain"].astype(str)) == {"Alpha", "Beta"}

    # Status is NOT propagated; merge_minimal_gages fills non-mapped cols with '-'
    assert set(g_updated["status"]) == {"-"}

    # Non-mapped columns are '-'
    for col in ["state", "name_raw", "description"]:
        assert (g_updated[col] == "-").all()

    # Geometry created and CRS preserved
    assert not g_updated.geometry.is_empty.any()
    assert g_updated.crs and g_updated.crs.to_string().lower() == "epsg:4326"

    # Raw fetched table has USGS-active (from add_missing_usgs_sites), but not used in gages
    assert "status" in fetched.columns
    assert set(fetched["status"]) == {"USGS-active"}


def test_add_missing_usgs_sites_updates_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Monkeypatch network fetch
    def fake_read_csv(
        url: str, sep: str = "\t", comment: str = "#", dtype: type[str] = str, engine: str = "python"
    ) -> pd.DataFrame:
        """fake csv reader"""
        return _fake_nwis_table()

    monkeypatch.setattr(pd, "read_csv", fake_read_csv)

    # Existing gage
    gages_existing = gpd.GeoDataFrame(
        {
            "geometry": [Point(-120.0, 35.0)],
            "state": ["-"],
            "site_no": ["01000000"],  # matches "Alpha"
            "name_plain": ["Old Name"],  # will be overwritten to "Alpha"
            "name_raw": ["-"],
            "description": ["-"],
            "status": ["-"],  # remains '-'
        },
        geometry="geometry",
        crs="EPSG:4326",
    )

    g_updated, usgs_ids, non_usgs, fetched = add_missing_usgs_sites(gages_existing, ["01000000", "02000000"])

    # Contains both existing and new
    assert set(g_updated["site_no"]) == {"01000000", "02000000"}

    # name_plain updated for existing
    updated_alpha = g_updated.loc[g_updated["site_no"] == "01000000"].iloc[0]
    assert updated_alpha["name_plain"] == "Alpha"

    # Status remains '-' (not propagated)
    print(updated_alpha)
    assert updated_alpha["status"] == "TXDOT"

    # New row also has '-' status and '-' for non-mapped columns
    added_beta = g_updated.loc[g_updated["site_no"] == "02000000"].iloc[0]
    assert added_beta["status"] == "-"
    for col in ["state", "name_raw", "description"]:
        assert added_beta[col] == "-"

    # CRS preserved
    assert g_updated.crs and g_updated.crs.to_string().lower() == "epsg:4326"
