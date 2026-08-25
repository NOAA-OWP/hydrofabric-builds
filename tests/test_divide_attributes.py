import warnings
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import yaml
from astropy.stats.circstats import circmean
from geopandas.testing import assert_geodataframe_equal
from pandas.testing import assert_frame_equal
from pyprojroot import here
from shapely.geometry import box

from hydrofabric_builds.helpers.stats import weighted_circular_mean, weighted_geometric_mean
from hydrofabric_builds.hydrofabric.divide_attributes import (
    _calculate_attribute,
    _calculate_glaciers,
    _prep_multiprocessing_configs,
    _prep_multiprocessing_rasters,
    _vpu_splitter,
    divide_attributes_pipeline_parallel,
    divide_attributes_pipeline_single,
)
from hydrofabric_builds.schemas.hydrofabric import (
    AggTypeEnum,
    DivideAttributeConfig,
    DivideAttributesModelConfig,
)


@pytest.fixture
def tmp_divides() -> Path:
    tmp_path = here() / "tests/data/divide_attributes/tmp_divide_attributes_divides.gpkg"
    gdf = gpd.read_file(here() / "tests/data/divide_attributes/divide_attributes_divides.gpkg")
    gdf.to_file(tmp_path, layer="divides")
    return tmp_path


@pytest.fixture
def divide_attribute_tmp_dir() -> Path:
    tmp_dir = here() / "tests/data/divide_attributes/tmp/divide-attributes"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return tmp_dir


@pytest.fixture
def divide_attributes_model_config() -> DivideAttributesModelConfig:
    (here() / "tests/data/divide_attributes/tmp/divide-attributes").mkdir(exist_ok=True, parents=True)

    with open(here() / "tests/data/divide_attributes/sample_divide_attributes_config.yaml") as f:
        data = yaml.safe_load(f)

    # prepare attribute list
    attributes = []
    for _, val in enumerate(data["attributes"]):
        # if there is no data_dir specified for each attribute, append the model's main data dir
        try:
            data["attributes"][val]["data_dir"]
        except KeyError:
            data["attributes"][val]["data_dir"] = data["data_dir"]
        attributes.append(data["attributes"][val])
    data["attributes"] = attributes

    model = DivideAttributesModelConfig.model_validate(data)
    return model


@pytest.fixture
def divide_attributes_bexp() -> dict[str, Any]:
    """Fixture contains config, vpu 03N path, and results"""
    cfg = DivideAttributeConfig(
        file_name=here() / "tests/data/divide_attributes/bexp_0.tif", agg_type="mode", field_name="bexp_mode"
    )
    vpu_path = here() / "tests/data/divide_attributes/vpu_03N.gpkg"
    results = pd.DataFrame(data={"bexp_mode": [3.8358047008514404] * 2})
    return {"config": cfg, "vpu_path": vpu_path, "results": results}


@pytest.fixture
def divide_attributes_twi() -> dict[str, Any]:
    """Fixture contains config, vpu 03N path, and results"""
    cfg = DivideAttributeConfig(
        file_name=here() / "tests/data/divide_attributes/twi.tif",
        agg_type="quartile_dist",
        field_name="twi_quartile",
    )
    vpu_path = here() / "tests/data/divide_attributes/vpu_03N.gpkg"
    results = pd.DataFrame(
        data={
            "twi_q25": [2.515662670135498, 3.0192484855651855],
            "twi_q50": [3.3970980644226074, 3.5839314460754395],
            "twi_q75": [4.570732116699219, 4.815537929534912],
            "twi_q100": [9.391340255737305, 9.557568550109863],
        }
    )
    return {"config": cfg, "vpu_path": vpu_path, "results": results}


@pytest.fixture
def divide_attributes_aspect() -> dict[str, Any]:
    """Fixture contains config, vpu 03N path, and results"""
    cfg = DivideAttributeConfig(
        file_name=here() / "tests/data/divide_attributes/usgs_250m_aspect_5070.tif",
        agg_type="weighted_circular_mean",
        field_name="aspect_circmean",
    )
    vpu_path = here() / "tests/data/divide_attributes/vpu_03N.gpkg"
    results = pd.DataFrame(
        data={
            "aspect_circmean": [2.6788329323693696, 1.4929676850304032],
        }
    )
    return {"config": cfg, "vpu_path": vpu_path, "results": results}


@pytest.fixture
def divide_attributes_aspect_lake() -> dict[str, Any]:
    """Fixture contains config, lakes (VPU 16) path, and results"""
    cfg = DivideAttributeConfig(
        file_name=here() / "tests/data/divide_attributes/usgs_250m_aspect_5070.tif",
        agg_type="weighted_circular_mean",
        field_name="aspect_circmean",
    )
    vpu_path = here() / "tests/data/divide_attributes/lake_16_111425.gpkg"
    results = pd.DataFrame(
        data={
            "aspect_circmean": [None],
        }
    )
    return {"config": cfg, "vpu_path": vpu_path, "results": results}


@pytest.fixture
def divide_attributes_dksat() -> dict[str, Any]:
    """Fixture contains config, vpu 03N path, and results"""
    cfg = DivideAttributeConfig(
        file_name=here() / "tests/data/divide_attributes/dksat_0.tif",
        agg_type="weighted_geometric_mean",
        field_name="dksat_geomean",
    )
    vpu_path = here() / "tests/data/divide_attributes/vpu_03N.gpkg"
    results = pd.DataFrame(
        data={
            "dksat_geomean": [8.584832031904611e-06, 1.341045813479675e-05],
        }
    )
    return {"config": cfg, "vpu_path": vpu_path, "results": results}


@pytest.fixture
def pipeline_results() -> dict[str, Any]:
    return pd.DataFrame(
        data={
            "bexp_mode": [
                3.8358047008514404,
                3.8358047008514404,
                9.270909309387207,
                8.370306015014648,
                8.370306015014648,
            ],
            "dksat_geomean": [
                8.584832031904611e-06,
                1.341045813479675e-05,
                2.2422710502747236e-06,
                4.831606554425143e-06,
                2.8500623676763363e-06,
            ],
            "twi_q25": [
                2.515662670135498,
                3.0192484855651855,
                3.173346996307373,
                3.290754556655884,
                4.159274578094482,
            ],
            "twi_q50": [
                3.3970980644226074,
                3.5839314460754395,
                3.767436981201172,
                3.9177677631378174,
                4.271378517150879,
            ],
            "twi_q75": [
                4.570732116699219,
                4.815537929534912,
                6.707566261291504,
                5.501246452331543,
                4.462091445922852,
            ],
            "twi_q100": [
                9.391340255737305,
                9.557568550109863,
                14.696468353271484,
                12.008649826049805,
                6.724186897277832,
            ],
            "aspect_circmean": [
                2.6788329323693696,
                1.4929676850304032,
                2.949461597005031,
                -2.211277378349467,
                0.6647588929701888,
            ],
            "lon": [-81.347576, -81.357058, -81.401389, -81.384266, -81.376746],
            "lat": [36.351394, 36.327656, 36.368095, 36.324204, 36.351550],
            "cgw": [0.04, 0.04, 0.04, 0.05, None],
            "expon": [3.3, 3.3, 3.3, 3.2, None],
            "max_gw_storage": [0.03, 0.03, 0.03, 0.02, None],
            "glacier_percent": [0.0, 0.0, 0.0, 0.0, 0.0],
        }
    )


@pytest.fixture
def glacier_hf() -> Path:
    """Divides for glaciers"""
    divides_path = here() / "tests/data/glacier_hf.gpkg"
    divides = gpd.GeoDataFrame(geometry=[box(0, 0, 2, 2), box(2, 2, 4, 4)], data={"div_id": [1, 2]}, crs=4326)
    divides.to_file(divides_path, layer="divides", driver="GPKG")
    return divides_path


@pytest.fixture
def glacier_parquet() -> Path:
    """ "Glaciers parquet"""
    glaciers_path = here() / "tests/data/glaciers.parquet"
    glaciers = gpd.GeoDataFrame(
        geometry=[box(0, 0, 1, 1), box(2, 2, 2.5, 2.5)], data={"glacier_id": [3, 4]}, crs=4326
    )
    glaciers.to_parquet(glaciers_path)
    return glaciers_path


@pytest.fixture
def glacier_model_cfg(glacier_hf: Path, glacier_parquet: Path) -> DivideAttributesModelConfig:
    """Config for glaciers"""
    glacier_model = {
        "hf_path": glacier_hf,
        "processes": 1,
        "data_dir": here() / "tests/data/divide_attributes",
        "divide_id": "div_id",
        "attributes": [
            DivideAttributeConfig(agg_type="percent", field_name="glacier_percent", file_name=glacier_parquet)
        ],
        "crs": "EPSG:4326",
    }

    cfg = DivideAttributesModelConfig.model_validate(glacier_model)
    return cfg


@pytest.fixture
def expected_glacier(glacier_hf: Path) -> gpd.GeoDataFrame:
    """Expected glaciers geodataframe"""
    gdf = gpd.read_file(glacier_hf, layer="divides")
    gdf.insert(1, "glacier_percent", [25, 6.25])
    return gdf


class TestDivideAttributesSchemas:
    """Tests for Divide Attribute Schemas"""

    def test_divide_attribute_config_pydantic(self) -> None:
        """DivideAttributeConfig model - no defaults"""
        model = DivideAttributeConfig(
            agg_type="mean",
            field_name="test",
            data_loader="tif",
            file_name="test.tif",
            tmp="/tmp/divide-attributes/test.parquet",
        )
        assert model.agg_type == AggTypeEnum.mean
        assert model.field_name == "test"
        assert model.file_name == here() / "data/divide_attributes/test.tif"
        assert model.tmp == Path("/tmp/divide-attributes/test.parquet")

    def test_divide_attribute_config_pydantic__defaults(self) -> None:
        """DivideAttributeConfig model - default factory for DivideAttributeConfig.tmp"""
        model = DivideAttributeConfig(
            agg_type="mean", field_name="test", data_loader="tif", file_name="test.tif"
        )
        assert model.agg_type == AggTypeEnum.mean
        assert model.field_name == "test"
        assert model.file_name == here() / "data/divide_attributes/test.tif"
        assert model.tmp == Path("/tmp/divide-attributes/tmp_test.parquet")  # default

    def test_divide_attribute_model_config_pydantic(self) -> None:
        """DivideAttributeModelConfig model - no defaults"""
        tmp_dir = Path("./data/tmp/divide-attributes")
        attributes = [
            DivideAttributeConfig(
                agg_type="mean", field_name="test", data_loader="tif", file_name="test.tif"
            ),
            DivideAttributeConfig(
                agg_type="mode", field_name="test2", data_loader="tif", file_name="test2.tif"
            ),
        ]
        model = DivideAttributesModelConfig(
            data_dir="./data",
            divides_path="./data/divides.gpkg",
            divide_id="div_id",
            output="./data/divides.gpkg",
            divides_path_list=["./data/div1.gpkg", "./data/div2.gpkg"],
            tmp_dir=tmp_dir,
            attributes=attributes,
            split_vpu=False,
            debug=True,
        )

        # model validator creates tmp folder
        assert tmp_dir.exists()
        assert model.data_dir == Path("./data")
        assert model.divide_id == "div_id"
        assert model.attributes == attributes
        assert model.divides_path_list == [Path("./data/div1.gpkg"), Path("./data/div2.gpkg")]
        assert model.tmp_dir == Path("./data/tmp/divide-attributes")
        assert model.split_vpu is False
        assert model.debug is True

        # teardown
        if tmp_dir.is_dir():
            try:
                tmp_dir.rmdir()
            except OSError:
                return

    def test_divide_attribute_model_config_pydantic__defaults(self) -> None:
        """DivideAttributeModelConfig model - defaults [divide_id, crs, tmp_dir, split_vpu, output]"""
        tmp_dir = Path("/tmp/divide-attributes")
        attributes = [
            DivideAttributeConfig(
                agg_type="mean", field_name="test", data_loader="tif", file_name="test.tif"
            ),
            DivideAttributeConfig(
                agg_type="mode", field_name="test2", data_loader="tif", file_name="test2.tif"
            ),
        ]
        model = DivideAttributesModelConfig(attributes=attributes)

        # model validator creates tmp folder
        assert tmp_dir.exists()
        assert model.data_dir == here() / "data/divide_attributes"  # default
        assert model.divide_id == "div_id"  # default
        assert model.attributes == attributes
        assert model.divides_path_list is None  # default
        assert model.tmp_dir == Path("/tmp/divide-attributes")  # default
        assert model.split_vpu is False  # default
        assert model.debug is False  # default

        # teardown
        if tmp_dir.is_dir():
            try:
                tmp_dir.rmdir()
            except OSError:
                return


class TestDivideAttributes:
    test_tmp_dir = here() / "tests/data/divide_attributes/tmp/divide-attributes"

    def test_vpu_splitter(
        self,
        divide_attributes_model_config: DivideAttributesModelConfig,
        tmp_divides: Path,
        divide_attribute_tmp_dir: Path,
    ) -> None:
        """VPU spliter splits gpkg with 2 VPUs"""
        gpkgs = _vpu_splitter(divide_attributes_model_config)

        try:
            expected = [divide_attributes_model_config.tmp_dir / f"vpu_{vpu}.gpkg" for vpu in ["03N", "05"]]
            assert set(gpkgs) == set(expected)
            assert len(gpkgs) == len(expected)

            for gpkg in gpkgs:
                assert gpkg.exists() is True
                gdf = gpd.read_file(gpkg, layer="divides")
                assert len(gdf["vpu_id"].unique()) == 1
        finally:
            for gpkg in gpkgs:
                gpkg.unlink(missing_ok=True)

    def test_calculate_attributes__mode(
        self,
        divide_attributes_model_config: DivideAttributesModelConfig,
        divide_attributes_bexp: dict[str, Any],
        tmp_divides: Path,
    ) -> None:
        """Fixture includes config, VPU03N, and result"""
        try:
            _calculate_attribute(
                divide_attributes_model_config,
                divide_attributes_bexp["config"],
                alt_divides_path=divide_attributes_bexp["vpu_path"],
            )

            assert divide_attributes_bexp["config"].tmp.exists() is True
            df = pd.read_parquet(divide_attributes_bexp["config"].tmp)
            assert_frame_equal(df[["bexp_mode"]], divide_attributes_bexp["results"], check_exact=False)

        finally:
            divide_attributes_bexp["config"].tmp.unlink(missing_ok=True)

    def test_calculate_attributes__quartile(
        self,
        divide_attributes_model_config: DivideAttributesModelConfig,
        divide_attributes_twi: dict[str, Any],
        tmp_divides: Path,
    ) -> None:
        """Fixture includes config, VPU03N, and results"""
        try:
            _calculate_attribute(
                divide_attributes_model_config,
                divide_attributes_twi["config"],
                alt_divides_path=divide_attributes_twi["vpu_path"],
            )

            assert divide_attributes_twi["config"].tmp.exists() is True
            df = pd.read_parquet(divide_attributes_twi["config"].tmp)
            assert_frame_equal(
                df[["twi_q25", "twi_q50", "twi_q75", "twi_q100"]],
                divide_attributes_twi["results"],
                check_exact=False,
            )

        finally:
            divide_attributes_twi["config"].tmp.unlink(missing_ok=True)

    def test_calculate_attributes__circmean(
        self,
        divide_attributes_model_config: DivideAttributesModelConfig,
        divide_attributes_aspect: dict[str, Any],
        divide_attributes_aspect_lake: dict[str, Any],
        tmp_divides: Path,
    ) -> None:
        """Fixture includes config, VPU03N, and results. Custom exactextract"""
        try:
            _calculate_attribute(
                divide_attributes_model_config,
                divide_attributes_aspect["config"],
                alt_divides_path=divide_attributes_aspect["vpu_path"],
            )

            assert divide_attributes_aspect["config"].tmp.exists() is True
            df = pd.read_parquet(divide_attributes_aspect["config"].tmp)
            assert_frame_equal(
                df[["aspect_circmean"]],
                divide_attributes_aspect["results"],
                check_exact=False,
            )

            _calculate_attribute(
                divide_attributes_model_config,
                divide_attributes_aspect_lake["config"],
                alt_divides_path=divide_attributes_aspect_lake["vpu_path"],
            )

            assert divide_attributes_aspect_lake["config"].tmp.exists() is True
            df = pd.read_parquet(divide_attributes_aspect_lake["config"].tmp)
            assert_frame_equal(
                df[["aspect_circmean"]],
                divide_attributes_aspect_lake["results"],
                check_exact=False,
            )

        finally:
            divide_attributes_aspect["config"].tmp.unlink(missing_ok=True)
            divide_attributes_aspect_lake["config"].tmp.unlink(missing_ok=True)

    def test_calculate_attributes__gmean(
        self,
        divide_attributes_model_config: DivideAttributesModelConfig,
        divide_attributes_dksat: dict[str, Any],
        tmp_divides: Path,
    ) -> None:
        """Fixture includes config, VPU03N, and results. Custom exactextract"""
        try:
            _calculate_attribute(
                divide_attributes_model_config,
                divide_attributes_dksat["config"],
                alt_divides_path=divide_attributes_dksat["vpu_path"],
            )

            assert divide_attributes_dksat["config"].tmp.exists() is True
            df = pd.read_parquet(divide_attributes_dksat["config"].tmp)
            assert_frame_equal(
                df[["dksat_geomean"]],
                divide_attributes_dksat["results"],
                check_exact=False,
            )

        finally:
            divide_attributes_dksat["config"].tmp.unlink(missing_ok=True)

    multiprocessing_raster_data = [
        pytest.param(
            2,
            2,
            [
                test_tmp_dir / "bexp_0_0.tif",
                test_tmp_dir / "bexp_0_1.tif",
            ],
            id="multiprocessing rasters 2 processes, 2 divides",
        ),
        pytest.param(
            2,
            3,
            [
                test_tmp_dir / "bexp_0_0.tif",
                test_tmp_dir / "bexp_0_1.tif",
            ],
            id="multiprocessing rasters 3 processes, 2 divides",
        ),
    ]

    @pytest.mark.parametrize("processes,n_divides,expected", multiprocessing_raster_data)
    def test_prep_multiprocessing_raster(
        self,
        divide_attributes_bexp: dict[str, Any],
        divide_attribute_tmp_dir: Path,
        processes: int,
        n_divides: int,
        expected: list[Path],
        tmp_divides: Path,
    ) -> None:
        """Generates raster per process but no more rasters than divides"""
        raster_list = _prep_multiprocessing_rasters(
            processes=processes,
            cfg=divide_attributes_bexp["config"],
            n_divides=n_divides,
            tmp_dir=divide_attribute_tmp_dir,
        )
        try:
            assert raster_list == expected
            for ras in raster_list:
                assert ras.exists() is True
        finally:
            for ras in raster_list:
                ras.unlink() if ras.exists() else None

    multiprocessing_config_data = [
        pytest.param(
            2,
            2,
            [
                test_tmp_dir / "bexp_0_0.tif",
                test_tmp_dir / "bexp_0_1.tif",
            ],
            [
                test_tmp_dir / "tmp_bexp_mode_0.parquet",
                test_tmp_dir / "tmp_bexp_mode_1.parquet",
            ],
            [
                test_tmp_dir / "bexp_0_0.tif",
                test_tmp_dir / "bexp_0_1.tif",
            ],
            id="multiprocessing config generator 2 processes, 2 divides",
        ),
        pytest.param(
            2,
            3,
            [
                test_tmp_dir / "bexp_0_0.tif",
                test_tmp_dir / "bexp_0_1.tif",
            ],
            [
                test_tmp_dir / "tmp_bexp_mode_0.parquet",
                test_tmp_dir / "tmp_bexp_mode_1.parquet",
                test_tmp_dir / "tmp_bexp_mode_2.parquet",
            ],
            [
                test_tmp_dir / "bexp_0_0.tif",
                test_tmp_dir / "bexp_0_1.tif",
                test_tmp_dir / "bexp_0_0.tif",
            ],
            id="multiprocessing config genreator 2 processes, 3 divides",
        ),
    ]

    @pytest.mark.parametrize(
        "processes,n_divides,raster_list,expected_tmp_files,expected_file_names", multiprocessing_config_data
    )
    def test_prep_multiprocessing_config(
        self,
        divide_attributes_bexp: dict[str, Any],
        divide_attribute_tmp_dir: Path,
        processes: int,
        n_divides: int,
        raster_list: list[Path],
        expected_tmp_files: list[Path],
        expected_file_names: list[Path],
        tmp_divides: Path,
    ) -> None:
        """Generates config per process with correct raster and temp name"""
        configs = _prep_multiprocessing_configs(
            processes=processes,
            cfg=divide_attributes_bexp["config"],
            n_divides=n_divides,
            tmp_dir=divide_attribute_tmp_dir,
            raster_list=raster_list,
        )

        for i, cfg in enumerate(configs):
            assert cfg.tmp == expected_tmp_files[i]
            assert cfg.file_name == expected_file_names[i]

    def test_divide_attributes_pipeline_single(
        self,
        divide_attributes_model_config: DivideAttributesModelConfig,
        pipeline_results: pd.DataFrame,
        tmp_divides: Path,
    ) -> None:
        """Single pipeline"""
        cfg = divide_attributes_model_config
        try:
            divide_attributes_pipeline_single(cfg)
            gdf = gpd.read_file(tmp_divides)
            assert_frame_equal(
                gdf[
                    [
                        "bexp_mode",
                        "dksat_geomean",
                        "twi_q25",
                        "twi_q50",
                        "twi_q75",
                        "twi_q100",
                        "aspect_circmean",
                        "lon",
                        "lat",
                        "cgw",
                        "expon",
                        "max_gw_storage",
                        "glacier_percent",
                    ]
                ],
                pipeline_results,
                check_exact=False,
            )

        finally:
            path_list = cfg.tmp_dir.glob("*")
            for f in path_list:
                f.unlink(missing_ok=True)
            tmp_divides.unlink(missing_ok=True)

    def test_divide_attributes_pipeline_parallel(
        self,
        divide_attributes_model_config: DivideAttributesModelConfig,
        pipeline_results: pd.DataFrame,
        tmp_divides: Path,
    ) -> None:
        try:
            cfg = divide_attributes_model_config
            with warnings.catch_warnings():
                warnings.simplefilter(action="ignore", category=DeprecationWarning)
                divide_attributes_pipeline_parallel(cfg, processes=2)
            gdf = gpd.read_file(tmp_divides)
            assert_frame_equal(
                gdf[
                    [
                        "bexp_mode",
                        "dksat_geomean",
                        "twi_q25",
                        "twi_q50",
                        "twi_q75",
                        "twi_q100",
                        "aspect_circmean",
                        "lon",
                        "lat",
                        "cgw",
                        "expon",
                        "max_gw_storage",
                        "glacier_percent",
                    ]
                ],
                pipeline_results,
                check_exact=False,
            )

        finally:
            path_list = cfg.tmp_dir.glob("*")
            for f in path_list:
                f.unlink(missing_ok=True)
            tmp_divides.unlink(missing_ok=True)

    def test_glaciers(
        self,
        glacier_hf: Path,
        glacier_parquet: Path,
        glacier_model_cfg: DivideAttributesModelConfig,
        expected_glacier: gpd.GeoDataFrame,
    ) -> None:
        """Tests glaciers against a test case in 4326"""
        try:
            with warnings.catch_warnings():
                warnings.simplefilter(
                    action="ignore", category=UserWarning
                )  # UserWarning for area calculation in 4326
                glaciers_attribute = [
                    cfg
                    for cfg in glacier_model_cfg.attributes
                    if ("glacier" in cfg.file_name.name) or ("glims" in cfg.file_name.name)
                ][0]
                _calculate_glaciers(glacier_model_cfg, glaciers_attribute)

            output = gpd.read_file(glacier_hf, layer="divides")

            assert_geodataframe_equal(output, expected_glacier)
        finally:
            glacier_hf.unlink(missing_ok=True)
            glacier_parquet.unlink(missing_ok=True)


class TestDivideAttributesStats:
    def test_weighted_circular_mean(self) -> None:
        """astropy is the reference for our function. astropy is test-only dependency.
        astropy input is in radians, our implementation is in degrees
        """
        values = [0, 1, 2]
        weights = [0, 0, 1]

        assert weighted_circular_mean(values, weights) == circmean(np.radians(values), weights=weights)

    def test_weighted_circular_mean__bad_weights(self) -> None:
        """Value error for incorrect weights shape"""
        values = [0, 1, 2]
        weights = [0, 0, 1, 1]

        with pytest.raises(ValueError):
            weighted_circular_mean(values, weights)

    def test_weighted_geometric_mean(self) -> None:
        values = [1, 1, 2]
        weights = [0, 0, 1]

        assert weighted_geometric_mean(values, weights) == np.array([2.0])
