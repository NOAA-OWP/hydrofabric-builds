"""Tests for module usgs_gages_builder."""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
import pytest
from pyprojroot import here
from shapely.geometry import Point

from hydrofabric_builds.streamflow_gauges.usgs_gages_builder import (
    extract_site_no,
    infer_state_from_filename,
    merge_adhoc_lakes_gages,
    merge_canadian_great_lakes,
    merge_gage_xy_into_gages,
    merge_minimal_gages,
    merge_nid_gages,
    merge_rfc_gages,
    merge_usace,
    merge_usbr,
    merge_usgs_shapefile_into_gages,
    read_kmz_points,
    strip_html,
)

CRS_4326 = "EPSG:4326"
CRS_5070 = "EPSG:5070"


# ===================================================================
# Fixtures
# ===================================================================


@pytest.fixture
def base_gages() -> gpd.GeoDataFrame:
    """Minimal gages GeoDataFrame with standard columns."""
    return gpd.GeoDataFrame(
        {
            "site_no": ["00000001", "00000002"],
            "name_plain": ["Gage A", "Gage B"],
            "state": ["TX", "OK"],
            "name_raw": ["-", "-"],
            "description": ["-", "-"],
            "status": ["USGS-active", "USGS-active"],
            "geometry": [Point(-97.0, 32.0), Point(-96.0, 33.0)],
        },
        geometry="geometry",
        crs=CRS_4326,
    )


@pytest.fixture
def base_kmz():
    """Minimal KMZFile with two gages."""
    with tempfile.TemporaryDirectory() as tmpdir:
        kmz_path = Path(tmpdir) / "streamgages_test.kmz"
        kml_path = Path(tmpdir) / "streamgages_test.kml"
        # Create a minimal kmz file from a GeoDataFrame
        gdf = gpd.GeoDataFrame(
            {
                "site_no": ["00000001", "00000002"],
                "name_plain": ["Gage A", "Gage B"],
                "state": ["TX", "OK"],
                "name_raw": ["-", "-"],
                "description": ["-", "-"],
                "status": ["USGS-active", "USGS-active"],
                "geometry": [Point(-97.0, 32.0), Point(-96.0, 33.0)],
            },
            geometry="geometry",
            crs=CRS_4326,
        )
        gdf.to_file(kml_path, driver="KML")
        with zipfile.ZipFile(kmz_path, "w", compression=zipfile.ZIP_DEFLATED) as kmz:
            kmz.write(kmz_path, arcname=kml_path)
        yield kmz_path


@pytest.fixture
def base_gages_5070() -> gpd.GeoDataFrame:
    """Minimal gages GeoDataFrame with standard columns."""
    gdf = gpd.GeoDataFrame(
        {
            "site_no": ["00000001", "00000002"],
            "name_plain": ["Gage A", "Gage B"],
            "state": ["TX", "OK"],
            "name_raw": ["-", "-"],
            "description": ["-", "-"],
            "status": ["USGS-active", "USGS-active"],
            "geometry": [Point(-97.0, 32.0), Point(-96.0, 33.0)],
        },
        geometry="geometry",
        crs=CRS_4326,
    )
    gages5070 = gdf.to_crs(CRS_5070)
    return gages5070


@pytest.fixture
def res_index_path() -> Path:
    """Path to a sample reservoir index file."""
    return here() / "tests/data/gages/reservoir_index_AnA_test.nc"


@pytest.fixture
def res_index_path_bad() -> Path:
    """Path to a sample reservoir index file."""
    return here() / "tests/data/lakes/nhf_lakes_test.gpkg"


@pytest.fixture
def adhoc_path() -> Path:
    """Path to adhoc_lakes.gpkg"""
    return here() / "tests/data/gages/adhoc_lakes_test.gpkg"


@pytest.fixture
def gages() -> gpd.GeoDataFrame:
    """Minimal gages GeoDataFrame with standard columns."""
    gages = gpd.read_file(here() / "tests/data/gages/gages_v1_test.gpkg")
    return gages


@pytest.fixture
def usbr() -> Path:
    """Path to USBR gages."""
    return here() / "tests/data/gages/usbr_test.gpkg"


@pytest.fixture
def usace_gages() -> Path:
    """Path to USACE gages."""
    return here() / "tests/data/gages/usace_crosswalk_test.gpkg"


@pytest.fixture
def kmz_file() -> Path:
    """Path to a sample KMZ file."""
    return here() / "tests/data/gages/streamgages_co_test.kmz"


@pytest.fixture
def kmz_blank_file() -> Path:
    """Path to a sample KMZ file with no gages."""
    return here() / "tests/data/gages/Blank_test.kmz"


# ===================================================================
# Tests: usgs_gages_builder — extract_site_no / strip_html / infer_state
# ===================================================================


class TestExtractSiteNo:
    def test_from_href(self) -> None:
        html = '<a href="https://waterdata.usgs.gov/nwis?site_no=01234567">link</a>'
        assert extract_site_no(html) == "01234567"

    def test_from_digits(self) -> None:
        assert extract_site_no("Station 01234567 near Tulsa") == "01234567"

    def test_none_input(self) -> None:
        assert extract_site_no(None) is None

    def test_no_match(self) -> None:
        assert extract_site_no("no numbers here") is None


class TestStripHtml:
    def test_strips_tags(self) -> None:
        assert (
            strip_html(
                "<a href='http://waterdata.usgs.gov/nwis/nwisman/?site_no=06444000'>06444000</a> WHITE RIVER AT CRAWFORD, NEBR.</a>"
            )
            == "06444000 WHITE RIVER AT CRAWFORD, NEBR."
        )

    def test_none_input(self) -> None:
        assert strip_html(None) is None


class TestInferStateFromFilename:
    def test_standard(self) -> None:
        assert infer_state_from_filename(Path("streamgages_texas.kmz")) == "Texas"

    def test_multi_word(self) -> None:
        assert infer_state_from_filename(Path("streamgages_new_york.kmz")) == "New York"

    def test_no_prefix(self) -> None:
        result = infer_state_from_filename(Path("something_ca.kmz"))
        assert result == "Ca"


class TestReadKMZPoints:
    def read_points(self, kmz_file: Path) -> None:
        gdf = read_kmz_points(kmz_file)
        assert isinstance(gdf, gpd.GeoDataFrame)
        assert len(gdf) > 0
        assert "site_no" in gdf.columns
        assert gdf.crs.to_epsg() == 4326

    def read_blank(self, kmz_blank_file: Path) -> None:
        gdf_blank = read_kmz_points(kmz_blank_file)
        assert isinstance(gdf_blank, gpd.GeoDataFrame)
        assert len(gdf_blank) == 0


# ===================================================================
# Tests: usgs_gages_builder — merge functions
# ===================================================================


class TestMergeMinimalGages:
    def test_appends_new(self, base_gages: gpd.GeoDataFrame) -> None:
        source = gpd.GeoDataFrame(
            {
                "site_no": ["99999999"],
                "station_nm": ["New Gage"],
                "geometry": [Point(-95.0, 34.0)],
                "status": ["TXDOT"],
            },
            geometry="geometry",
            crs=CRS_4326,
        )
        result = merge_minimal_gages(base_gages, source)
        assert len(result) == 3
        assert "99999999" in result["site_no"].values

    def test_no_duplicates(self, base_gages: gpd.GeoDataFrame) -> None:
        source = gpd.GeoDataFrame(
            {
                "site_no": ["00000001"],
                "station_nm": ["Existing Gage"],
                "geometry": [Point(-97.0, 32.0)],
                "status": ["TXDOT"],
            },
            geometry="geometry",
            crs=CRS_4326,
        )
        result = merge_minimal_gages(base_gages, source, update_existing=False)
        assert len(result) == 2


class TestMergeGageXy:
    def test_adds_new_from_csv(self, base_gages: gpd.GeoDataFrame, tmp_path: Path) -> None:
        csv = tmp_path / "gage_xy.csv"
        csv.write_text("gageid,lon,lat\n99999999,-100.0,35.0\n")
        result = merge_gage_xy_into_gages(base_gages, csv)
        assert len(result) == 3

    def test_excludes_ids(self, base_gages: gpd.GeoDataFrame, tmp_path: Path) -> None:
        csv = tmp_path / "gage_xy.csv"
        csv.write_text("gageid,lon,lat\n99999999,-100.0,35.0\n88888888,-99.0,34.0\n")
        result = merge_gage_xy_into_gages(base_gages, csv, exclude_ids=["99999999"])
        assert "99999999" not in result["site_no"].values
        assert "88888888" in result["site_no"].values

    def test_updates_existing_geometry(self, base_gages: gpd.GeoDataFrame, tmp_path: Path) -> None:
        csv = tmp_path / "gage_xy.csv"
        csv.write_text("gageid,lon,lat\n00000001,-101.0,36.0\n")
        result = merge_gage_xy_into_gages(base_gages, csv, update_existing=True)
        assert len(result) == 2


class TestMergeUsgsShapefile:
    def test_appends_from_shapefile(self, base_gages: gpd.GeoDataFrame, tmp_path: Path) -> None:
        src = gpd.GeoDataFrame(
            {
                "STAID": ["55555555"],
                "STANAME": ["Shp Gage"],
                "ST": ["CA"],
                "URL": ["http://example.com"],
                "geometry": [Point(-120.0, 37.0)],
            },
            geometry="geometry",
            crs=CRS_4326,
        )
        shp_path = tmp_path / "usgs.gpkg"
        src.to_file(shp_path, driver="GPKG")
        result = merge_usgs_shapefile_into_gages(base_gages, shp_path)
        assert "55555555" in result["site_no"].values


class TestMergeRfcGages:
    def test_appends_rfc(
        self,
        base_gages_5070: gpd.GeoDataFrame,
        tmp_path: Path,
        res_index_path: Path,
        res_index_path_bad: Path,
    ) -> None:
        rfc_csv = tmp_path / "rfc.csv"
        rfc_csv.write_text(
            "nws shef id,longitude,latitude,forecast status\n"
            "RFC001,-98.0,33.0,Forecasts are issued routinely year-round.\n"
            "RFC002,-99.0,34.0,Forecasts are issued as needed during times of high water but are not routinely available.\n"
        )
        rfc_path = rfc_csv
        nwm_rfc_path_bad = res_index_path_bad
        result = merge_rfc_gages(base_gages_5070, rfc_path, res_index_path)
        result_nonc = merge_rfc_gages(base_gages_5070, rfc_path, nwm_rfc_path_bad)
        assert len(result) == 3
        assert len(result_nonc) == 3


class TestMergeNIdGages:
    def test_append_nid(
        self,
        base_gages_5070: gpd.GeoDataFrame,
        tmp_path: Path,
        res_index_path: Path,
        res_index_path_bad: Path,
    ) -> None:
        nid_csv = tmp_path / "nid.csv"
        nid_csv.write_text(
            "NIDID,LONGITUDE,LATITUDE\nTX0001,-98.0,33.0\nTX0002,-99.0,34.0\nND00151,-98.7087,46.931"
        )
        nwm_rfc_path = res_index_path
        result = merge_nid_gages(base_gages_5070, nid_csv, nwm_rfc_path)
        new_length = len(base_gages_5070) + 1
        assert len(result) == new_length

        nwm_rfc_path_bad = res_index_path_bad
        result_bad = merge_nid_gages(base_gages_5070, nid_csv, nwm_rfc_path_bad)
        assert len(result_bad) == len(base_gages_5070)
        nid_csv_bad = tmp_path / "nid_bad.csv"
        nid_csv_bad.write_text("NIDID,LONGITUDE,LATITUDE\nTX0001,-98.0,33.0\nTX0002,-99.0,34.0\n")
        result_bad_noadd = merge_nid_gages(base_gages_5070, nid_csv_bad, nwm_rfc_path)
        assert len(result_bad_noadd) == len(base_gages_5070)


class TestMergeAdhocLakes:
    def test_append_adhoc_lakes(self, gages: gpd.GeoDataFrame, adhoc_path: Path):
        result = merge_adhoc_lakes_gages(gages, adhoc_path)
        adhoc_gages_total = len(gpd.read_file(adhoc_path))
        gages_total = len(gages)
        assert len(result) == adhoc_gages_total + gages_total


class TestMergeCanadianGreatLakes:
    def test_adds_erie_ontario(self, gages: gpd.GeoDataFrame) -> None:
        from hydrofabric_builds.schemas.hydrofabric import GreatLakesMapping

        cfg = GreatLakesMapping()
        result = merge_canadian_great_lakes(gages, cfg)
        total_gages = len(gages)
        assert len(result) == total_gages + 2
        assert "canada_great_lakes" in result["status"].values


class TestMergeUsbr:
    def test_appends_usbr(self, gages: gpd.GeoDataFrame, usbr: Path) -> None:
        result = merge_usbr(gages, usbr)
        total_gages = len(gages)
        usbr_length = len(gpd.read_file(usbr))
        assert any("usbr-" in s for s in result["site_no"].values)
        assert "USBR" in result["status"].values
        assert len(result) == total_gages + usbr_length


class TestMergeUsace:
    def test_appends_usace(self, gages: gpd.GeoDataFrame, usace_gages: Path) -> None:
        result = merge_usace(gages, usace_gages)
        total_gages = len(gages)
        usace_length = len(gpd.read_file(usace_gages))
        assert "USACE" in result["status"].values
        assert len(result) == total_gages + usace_length
