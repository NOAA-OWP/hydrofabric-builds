from pathlib import Path

import pytest
from pyprojroot import here

from hydrofabric_builds._version import __version__
from hydrofabric_builds.config import HFConfig


@pytest.fixture
def sample_config_yaml_1() -> Path:
    return here() / "tests/data/sample_config_1.yaml"


@pytest.fixture
def sample_config_yaml_2() -> Path:
    return here() / "tests/data/sample_config_2.yaml"


def test_from_yaml_1(sample_config_yaml_1: Path) -> None:
    """Tests hydrofabric path generation and appending data dir to divide attribute"""
    cfg = HFConfig.from_yaml(sample_config_yaml_1)

    assert cfg.output_file_path == Path(f"data/nhf_{__version__}.gpkg")
    assert cfg.divide_attributes.attributes[0].data_dir == Path("data/divide_attributes")
    assert cfg.divide_attributes.attributes[0].file_name == Path("data/divide_attributes/nwm/bexp_0.tif")
    assert cfg.divide_attributes.hf_path == Path(f"data/nhf_{__version__}.gpkg")
    assert cfg.flowpath_attributes.hf_path == Path(f"data/nhf_{__version__}.gpkg")


def test_from_yaml_2(sample_config_yaml_2: Path) -> None:
    """Tests for input HF name and for divide attributes when there is a data directory in the attribute that is different than main"""
    cfg = HFConfig.from_yaml(sample_config_yaml_2)

    assert cfg.output_file_path == Path("data/hf_test.gpkg")
    assert cfg.divide_attributes.data_dir == Path("data/divide_attributes")
    assert cfg.divide_attributes.attributes[0].data_dir == Path("different")
    assert cfg.divide_attributes.attributes[0].file_name == Path("different/nwm/bexp_0.tif")
