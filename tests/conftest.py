"""conftest for test_trace.py with edge case fixtures."""

from pathlib import Path

import geopandas as gpd
import pytest
from pyprojroot import here

from hydrofabric_builds.config import HFConfig, TaskSelection
from hydrofabric_builds.schemas.hydrofabric import (
    BuildHydrofabricConfig,
)
from scripts.hf_runner import TaskInstance


@pytest.fixture
def task_instance() -> TaskInstance:
    """Fixture providing a TaskInstance."""
    return TaskInstance()


@pytest.fixture
def mock_geopackages() -> tuple[str, str]:
    """Create paths to test data files."""
    return str(here() / "tests/data/sample_divides.parquet"), str(
        here() / "tests/data/sample_flowpaths.parquet"
    )


@pytest.fixture
def sample_divides() -> gpd.GeoDataFrame:
    """Open the sample test data"""
    _gdf = gpd.read_parquet(here() / "tests/data/sample_divides.parquet")
    _gdf["divide_id"] = _gdf["divide_id"].astype("int").astype("str")
    return _gdf


@pytest.fixture
def sample_flowpaths() -> gpd.GeoDataFrame:
    """Open the sample test data"""
    _gdf = gpd.read_parquet(here() / "tests/data/sample_flowpaths.parquet")
    _gdf["flowpath_id"] = _gdf["flowpath_id"].astype("int").astype("str")
    return _gdf


@pytest.fixture
def sample_config(mock_geopackages: tuple[str, str], tmp_path: Path) -> HFConfig:
    """Fixture providing a sample HFConfig."""
    return HFConfig(
        build=BuildHydrofabricConfig(
            reference_divides_path=mock_geopackages[0], reference_flowpaths_path=mock_geopackages[1]
        ),
        tasks=TaskSelection(
            build_hydrofabric=True,
            divide_attributes=False,
            flowpath_attributes=False,
            waterbodies=False,
            gages=False,
        ),
        output_dir=tmp_path,
    )


@pytest.fixture
def expected_graph() -> dict[str, list[str]]:
    """Fixture providing expected network graph structure."""
    return {
        "6720527": ["6720517", "6720515"],
        "6720413": ["6722029"],
        "6712619": ["6712621"],
        "6720391": ["6722665"],
        "6720467": ["6720439"],
        "6721863": ["6720475", "6720473"],
        "6720417": ["6720413", "6720371"],
        "6720795": ["6721871", "6720607"],
        "6720797": ["6720703", "6720701"],
        "6720475": ["6720383"],
        "6720431": ["6720385"],
        "6720493": ["6720531", "6720497"],
        "6720389": ["6720449", "6720381"],
        "6719083": ["6719081", "6719063"],
        "6720393": ["6722017"],
        "6720525": ["6722667"],
        "6720559": ["6721869"],
        "6722665": ["6720363"],
        "6720497": ["6720437"],
        "6722667": ["6719083"],
        "6720519": ["6720559"],
        "6722035": ["6720431"],
        "6712111": ["6712133"],
        "6720473": ["6720391", "6720389"],
        "6712133": ["6712725"],
        "6720515": ["6721863", "6720551"],
        "6720881": ["6720893"],
        "6719081": ["6720393"],
        "6720689": ["6720681", "6720679"],
        "6720877": ["6720797", "6720795"],
        "6712621": ["6712111"],
        "6722029": ["6722041", "6722035"],
        "6722023": ["6720503"],
        "6720871": ["6720821"],
        "6720679": ["6720677", "6720675"],
        "6722017": ["6720407"],
        "6720531": ["6722499"],
        "6720703": ["6720683", "6720651"],
        "6720801": ["6720643"],
        "6720433": ["6722023"],
        "6720675": ["6722501"],
        "6720521": ["6720519"],
        "6720607": ["6720527", "6720525"],
        "6720407": ["6720433", "6720417"],
        "6720879": ["6720883", "6720877"],
        "6721871": ["6720581"],
        "6722041": ["6720493"],
        "6720645": ["6720609"],
        "6720683": ["6720773", "6720689"],
    }


@pytest.fixture
def trace_case_upstream_no_divide_config(tmp_path: Path) -> HFConfig:
    """Fixture providing a sample HFConfig."""
    return HFConfig(
        build=BuildHydrofabricConfig(
            reference_divides_path=str(here() / "tests/data/trace_cases/upstream_no_divide_div.parquet"),
            reference_flowpaths_path=str(here() / "tests/data/trace_cases/upstream_no_divide_fp.parquet"),
        ),
        tasks=TaskSelection(
            build_hydrofabric=True,
            divide_attributes=False,
            flowpath_attributes=False,
            waterbodies=False,
            gages=False,
        ),
        output_dir=tmp_path,
    )


@pytest.fixture
def trace_case_bad_connector_no_divide_config(tmp_path: Path) -> HFConfig:
    """Fixture providing a sample HFConfig."""
    return HFConfig(
        build=BuildHydrofabricConfig(
            reference_divides_path=str(here() / "tests/data/trace_cases/bad_connector_no_divide_div.parquet"),
            reference_flowpaths_path=str(
                here() / "tests/data/trace_cases/bad_connector_no_divide_fp.parquet"
            ),
        ),
        tasks=TaskSelection(
            build_hydrofabric=True,
            divide_attributes=False,
            flowpath_attributes=False,
            waterbodies=False,
            gages=False,
        ),
        output_dir=tmp_path,
    )


@pytest.fixture
def trace_case_no_divide_coastal_outlet(tmp_path: Path) -> HFConfig:
    """Fixture providing a sample HFConfig."""
    return HFConfig(
        build=BuildHydrofabricConfig(
            reference_divides_path=str(here() / "tests/data/trace_cases/no_divide_coastal_div.parquet"),
            reference_flowpaths_path=str(here() / "tests/data/trace_cases/no_divide_coastal_fp.parquet"),
        ),
        tasks=TaskSelection(
            build_hydrofabric=True,
            divide_attributes=False,
            flowpath_attributes=False,
            waterbodies=False,
            gages=False,
        ),
        output_dir=tmp_path,
    )


@pytest.fixture
def trace_case_hudson_river_large_scale(tmp_path: Path) -> HFConfig:
    """Fixture providing a sample HFConfig."""
    return HFConfig(
        build=BuildHydrofabricConfig(
            reference_divides_path=str(here() / "tests/data/trace_cases/hudson_river_large_case_div.parquet"),
            reference_flowpaths_path=str(
                here() / "tests/data/trace_cases/hudson_river_large_case_fp.parquet"
            ),
        ),
        tasks=TaskSelection(
            build_hydrofabric=True,
            divide_attributes=False,
            flowpath_attributes=False,
            waterbodies=False,
            gages=False,
        ),
        output_dir=tmp_path,
    )


@pytest.fixture
def trace_case_sioux_falls(tmp_path: Path) -> HFConfig:
    """Fixture providing a sample HFConfig."""
    return HFConfig(
        build=BuildHydrofabricConfig(
            reference_divides_path=str(here() / "tests/data/trace_cases/sioux_falls_div.parquet"),
            reference_flowpaths_path=str(here() / "tests/data/trace_cases/sioux_falls_fp.parquet"),
        ),
        tasks=TaskSelection(
            build_hydrofabric=True,
            divide_attributes=False,
            flowpath_attributes=False,
            waterbodies=False,
            gages=False,
        ),
        output_dir=tmp_path,
    )


@pytest.fixture
def trace_case_large_braided(tmp_path: Path) -> HFConfig:
    """Fixture providing a sample HFConfig."""
    return HFConfig(
        build=BuildHydrofabricConfig(
            reference_divides_path=str(here() / "tests/data/trace_cases/large_braided_div.parquet"),
            reference_flowpaths_path=str(here() / "tests/data/trace_cases/large_braided_fp.parquet"),
        ),
        tasks=TaskSelection(
            build_hydrofabric=True,
            divide_attributes=False,
            flowpath_attributes=False,
            waterbodies=False,
            gages=False,
        ),
        output_dir=tmp_path,
    )


@pytest.fixture
def trace_case_small_braided(tmp_path: Path) -> HFConfig:
    """Fixture providing a sample HFConfig."""
    return HFConfig(
        build=BuildHydrofabricConfig(
            reference_divides_path=str(here() / "tests/data/trace_cases/small_braided_div.parquet"),
            reference_flowpaths_path=str(here() / "tests/data/trace_cases/small_braided_fp.parquet"),
        ),
        tasks=TaskSelection(
            build_hydrofabric=True,
            divide_attributes=False,
            flowpath_attributes=False,
            waterbodies=False,
            gages=False,
        ),
        output_dir=tmp_path,
    )
