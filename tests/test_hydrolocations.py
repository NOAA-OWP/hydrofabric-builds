import shutil
from pathlib import Path

import geopandas as gpd
import pytest
from pyprojroot import here

from hydrofabric_builds.hydrofabric.hydrolocations import hydrolocations_pipeline


@pytest.fixture
def tmp_hf_for_hl() -> Path:
    sample_path = here() / "tests/data/sample_hf_hl.gpkg"
    tmp_path = here() / "tests/data/tmp_hf_hl.gpkg"
    shutil.copy(sample_path, tmp_path)
    return tmp_path


def test_hydrolocations_pipeline(tmp_hf_for_hl: Path) -> None:
    """Hydrolocations test - check IDs and downstream nexus are correct"""
    try:
        hydrolocations_pipeline(tmp_hf_for_hl)

        gdf_hl = gpd.read_file(tmp_hf_for_hl, layer="hydrolocations")
        assert gdf_hl["hy_id"].tolist() == [1, 2, 3, 4, 5]
        assert gdf_hl["dn_nex_id"].tolist() == [21606, 21599, 21596, 21590, 21593]

        gdf_gages = gpd.read_file(tmp_hf_for_hl, layer="gages")
        assert gdf_gages["hy_id"].tolist() == [4, 5]

        gdf_wb = gpd.read_file(tmp_hf_for_hl, layer="waterbodies")
        assert gdf_wb["hy_id"].tolist() == [1, 2, 3]
    finally:
        tmp_hf_for_hl.unlink(missing_ok=True)
