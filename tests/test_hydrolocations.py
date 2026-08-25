import shutil
from pathlib import Path

import geopandas as gpd
import pytest
from pyogrio.errors import DataLayerError
from pyprojroot import here

from hydrofabric_builds.hydrofabric.hydrolocations import hydrolocations_pipeline


@pytest.fixture
def sample_hl_path() -> Path:
    return here() / "tests/data/sample_hf_hl.gpkg"


@pytest.fixture
def tmp_hl_path() -> Path:
    return here() / "tests/data/tmp_hf_hl.gpkg"


@pytest.fixture
def tmp_hf_for_hl(sample_hl_path: Path, tmp_hl_path: Path) -> Path:
    """Copy the full sample HF gpkg. Includes flowpaths, lakes, waterbodies, and gages"""
    shutil.copy(sample_hl_path, tmp_hl_path)
    return tmp_hl_path


@pytest.fixture
def tmp_hf_for_hl__two_layers(sample_hl_path: Path, tmp_hl_path: Path) -> Path:
    """Create a GPKG with 2/3 layers.
    Note: If you drop tables with sqlite instead, OGR gets runtime warnings
    about tables being referenced in GPKG but not existing
    """
    gdf_fp = gpd.read_file(sample_hl_path, layer="flowpaths")
    gdf_fp.to_file(tmp_hl_path, layer="flowpaths", driver="GPKG", overwrite=True)
    gdf_lakes = gpd.read_file(sample_hl_path, layer="lakes")
    gdf_lakes.to_file(tmp_hl_path, layer="lakes", driver="GPKG", overwrite=True)
    gdf_gages = gpd.read_file(sample_hl_path, layer="gages")
    gdf_gages.to_file(tmp_hl_path, layer="gages", driver="GPKG", overwrite=True)

    return tmp_hl_path


@pytest.fixture
def tmp_hf_for_hl__one_layer(sample_hl_path: Path, tmp_hl_path: Path) -> Path:
    """Create a GPKG with 1/3 layers.
    Note: If you drop tables with sqlite instead, OGR gets runtime warnings
    about tables being referenced in GPKG but not existing
    """
    gdf_fp = gpd.read_file(sample_hl_path, layer="flowpaths")
    gdf_fp.to_file(tmp_hl_path, layer="flowpaths", driver="GPKG", overwrite=True)
    gdf_gages = gpd.read_file(sample_hl_path, layer="gages")
    gdf_gages.to_file(tmp_hl_path, layer="gages", driver="GPKG", overwrite=True)

    return tmp_hl_path


@pytest.fixture
def tmp_hf_for_hl__no_layers(sample_hl_path: Path, tmp_hl_path: Path) -> Path:
    """Create a GPKG with no HF layers.
    Note: If you drop tables with sqlite instead, OGR gets runtime warnings
    about tables being referenced in GPKG but not existing
    """
    gdf = gpd.read_file(sample_hl_path, layer="flowpaths")
    gdf.to_file(tmp_hl_path, layer="flowpaths")

    return tmp_hl_path


def test_hydrolocations_pipeline(tmp_hf_for_hl: Path) -> None:
    """Hydrolocations test - check IDs and downstream nexus are correct"""
    try:
        tmp_hf = tmp_hf_for_hl
        hydrolocations_pipeline(tmp_hf)

        gdf_hl = gpd.read_file(tmp_hf, layer="hydrolocations")
        assert gdf_hl["hy_id"].tolist() == [1, 2, 3, 4, 5, 6]
        assert gdf_hl["dn_nex_id"].tolist() == [21606, 21599, 21596, 21590, 21593, 21606]

        gdf_gages = gpd.read_file(tmp_hf, layer="gages")
        assert gdf_gages["hy_id"].tolist() == [4, 5]

        gdf_wb = gpd.read_file(tmp_hf, layer="waterbodies")
        assert gdf_wb["hy_id"].tolist() == [1, 2, 3]

        gdf_lk = gpd.read_file(tmp_hf, layer="lakes")
        assert gdf_lk["hy_id"].tolist() == [6]
    finally:
        tmp_hf.unlink(missing_ok=True)


def test_hydrolocations_pipeline__two_layers(tmp_hf_for_hl__two_layers: Path) -> None:
    """Hydrolocations test - check IDs and downstream nexus are correct when 2/3 tables are available"""
    try:
        tmp_hf = tmp_hf_for_hl__two_layers
        hydrolocations_pipeline(tmp_hf)

        # assert missing tables -f if this fails, the sample is built incorrectly
        with pytest.raises(DataLayerError):
            gpd.read_file(tmp_hf, layer="waterbodies")

        gdf_hl = gpd.read_file(tmp_hf, layer="hydrolocations")
        assert gdf_hl["hy_id"].tolist() == [1, 2, 3]
        assert gdf_hl["dn_nex_id"].tolist() == [21590, 21593, 21606]

        gdf_gages = gpd.read_file(tmp_hf, layer="gages")
        assert gdf_gages["hy_id"].tolist() == [1, 2]

        gdf_lk = gpd.read_file(tmp_hf, layer="lakes")
        assert gdf_lk["hy_id"].tolist() == [3]

    finally:
        tmp_hf.unlink(missing_ok=True)


def test_hydrolocations_pipeline__one_layer(tmp_hf_for_hl__one_layer: Path) -> None:
    """Hydrolocations test - check IDs and downstream nexus are correct when one table is available"""
    try:
        tmp_hf = tmp_hf_for_hl__one_layer
        # assert missing tables -f if this fails, the sample is built incorrectly
        with pytest.raises(DataLayerError):
            gpd.read_file(tmp_hf, layer="lakes")

        with pytest.raises(DataLayerError):
            gpd.read_file(tmp_hf, layer="waterbodies")

        hydrolocations_pipeline(tmp_hf)

        gdf_hl = gpd.read_file(tmp_hf, layer="hydrolocations")
        assert gdf_hl["hy_id"].tolist() == [1, 2]
        assert gdf_hl["dn_nex_id"].tolist() == [21590, 21593]

        gdf_gages = gpd.read_file(tmp_hf, layer="gages")
        assert gdf_gages["hy_id"].tolist() == [1, 2]

    finally:
        tmp_hf.unlink(missing_ok=True)


def test_hydrolocations_pipeline__no_layers(tmp_hf_for_hl__no_layers: Path) -> None:
    """Hydrolocations test - check IDs and downstream nexus are correct when no tables are available"""
    try:
        tmp_hf = tmp_hf_for_hl__no_layers

        # assert missing tables - if this fails, the sample is built incorrectly
        with pytest.raises(DataLayerError):
            gpd.read_file(tmp_hf, layer="gages")

        with pytest.raises(DataLayerError):
            gpd.read_file(tmp_hf, layer="lakes")

        with pytest.raises(DataLayerError):
            gpd.read_file(tmp_hf, layer="waterbodies")

        hydrolocations_pipeline(tmp_hf)

        gdf_hl = gpd.read_file(tmp_hf, layer="hydrolocations")
        assert gdf_hl["hy_id"].tolist() == []
        assert gdf_hl["dn_nex_id"].tolist() == []

    finally:
        tmp_hf.unlink(missing_ok=True)
