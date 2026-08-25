from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import polars as pl
import pytest
import rasterio
from geopandas.testing import assert_geodataframe_equal
from polars.testing import assert_frame_equal
from pyprojroot import here
from rasterio.transform import from_bounds
from shapely.geometry import LineString, MultiLineString

from hydrofabric_builds.hydrofabric.flowpath_attributes import (
    _create_base_polars,
    _dem_attributes,
    _other_flowpath_attributes,
    _riverml_attributes,
    flowpath_attributes_pipeline,
)
from hydrofabric_builds.schemas.hydrofabric import (
    FlowpathAttributesConfig,
    FlowpathAttributesModelConfig,
    StreamOrder,
)


@pytest.fixture
def flowpath_attributes_model_cfg() -> Path:
    data_dir = here() / "tests/data/flowpath_attributes"
    data_dir.mkdir(exist_ok=True)
    return FlowpathAttributesModelConfig(
        hf_path=data_dir / "sample_fp_tmp.gpkg",
        flowpath_id="fp_id",
        use_stream_order=True,
        dem_path=data_dir / "sample_dem.tif",
        tw_path=data_dir / "sample_tw.parquet",
        y_path=data_dir / "sample_y.parquet",
        r_path=data_dir / "sample_r.parquet",
        output=data_dir / "output_test.gpkg",
    )


@pytest.fixture
def flowpath_attributes_dummy_dem(flowpath_attributes_model_cfg: FlowpathAttributesModelConfig) -> Path:
    """Dummy DEM for flowpath attributes"""
    dem = flowpath_attributes_model_cfg.dem_path
    if not dem.exists():
        arr = np.array([[1.0, 1.0, 1.0], [2.0, 2.0, 2.0], [3.0, 3.0, 3.0]])
        transform = from_bounds(3, 0, 0, 3, 1, 1)
        with rasterio.open(
            dem,
            "w",
            transform=transform,
            dtype=np.float64,
            count=1,
            compression="lzw",
            height=3,
            width=3,
            crs=4326,
        ) as dst:
            dst.write(arr, 1)

    return dem


@pytest.fixture
def flowpath_attributes_dummy_gpkg(flowpath_attributes_model_cfg: FlowpathAttributesModelConfig) -> Path:
    """Dummy flowpaths for flowpath attributes"""
    fp = flowpath_attributes_model_cfg.hf_path

    g1 = LineString([(-5, -1), (-5, -4)])
    g2 = LineString([(-5, -4), (-4, 2)])
    g3 = LineString([(1, 1), (-2, 1)])
    g4 = MultiLineString([[(0, -3.5), (0, -1)], [(0, -1.5), (-1, -1)]])

    data = {"fp_id": [1, 2, 3, 4], "stream_order": [1, 2, 3, 3], "total_da_sqkm": [1, 2, 3, 4]}
    geometry = [g1, g2, g3, g4]
    gdf = gpd.GeoDataFrame(data=data, geometry=geometry, crs=4326)

    gdf.to_file(fp, layer="flowpaths")

    ref_fp = gpd.GeoDataFrame(data={"fp_id": [1, 2, 3, 4], "ref_fp_id": [900, 800, 700, 600]})
    ref_fp.to_file(fp, layer="reference_flowpaths")

    return fp


@pytest.fixture
def flowpath_attributes_dem_values(
    flowpath_attributes_model_cfg: FlowpathAttributesModelConfig,
) -> gpd.GeoDataFrame:
    """Results from dummy DEM"""
    gdf = gpd.read_file(flowpath_attributes_model_cfg.hf_path, layer="flowpaths")

    gdf["geometry"] = gdf["geometry"].line_merge()
    gdf["mean_elevation"] = [(2 + 3) / 2, (1 + 3) / 2, (1 + 1) / 2, (2 + 2 + 2 + 3) / 4]
    gdf["slope"] = [abs((3 - 2) / 3), abs((1 - 3) / 6.082763), 1e-4, abs((3 - 2) / 2.5)]  # line 3 is slope 0

    return gdf


@pytest.fixture
def flowpath_attributes_riverml_parquets(
    flowpath_attributes_model_cfg: FlowpathAttributesModelConfig,
) -> dict[str, Path]:
    """Sample parquets for riverml outputs"""
    y = flowpath_attributes_model_cfg.y_path
    tw = flowpath_attributes_model_cfg.tw_path
    r = flowpath_attributes_model_cfg.r_path

    if not y.exists():
        df = pd.DataFrame(data={"FEATUREID": [600, 700, 800, 900], "prediction": [0.1, 0.4, 0.3, 0.5]})
        df.to_parquet(y, index=False)

    if not tw.exists():
        df = pd.DataFrame(data={"FEATUREID": [600, 700, 800, 900], "prediction": [15.0, 14.0, 13.0, 11.0]})
        df.to_parquet(tw, index=False)

    if not r.exists():
        df = pd.DataFrame(data={"FEATUREID": [600, 700, 800, 900], "prediction": [2.5, 1.5, 1.0, 2.0]})
        df.to_parquet(r, index=False)

    return {"y": y, "tw": tw, "r": r}


@pytest.fixture
def flowpath_attributes_base_polars_df(flowpath_attributes_dummy_gpkg: Path) -> pl.DataFrame:
    """Base polars df created in flowpath attributes"""
    gdf = gpd.read_file(flowpath_attributes_dummy_gpkg, layer="flowpaths")

    df = pl.from_pandas(gdf[["fp_id", "stream_order", "total_da_sqkm"]])
    df = df.with_columns(
        pl.lit(None).alias("n"),
        pl.lit(None).alias("r"),
        pl.lit(None).alias("y"),
        pl.lit(None).alias("ncc"),
        pl.lit(None).alias("btmwdth"),
        pl.lit(None).alias("chslp"),
        pl.lit(None).alias("musx"),
        pl.lit(None).alias("musk"),
        pl.lit(None).alias("topwdth"),
        pl.lit(None).alias("topwdthcc"),
        pl.lit(None).alias("topwdthcc_ml"),
    )

    return df


@pytest.fixture
def flowpath_attributes_riverml_values(
    flowpath_attributes_model_cfg: FlowpathAttributesModelConfig,
) -> pl.DataFrame:
    """Results from riverml join"""
    gdf = gpd.read_file(flowpath_attributes_model_cfg.hf_path, layer="flowpaths")

    df = pl.from_pandas(gdf[["fp_id", "stream_order", "total_da_sqkm"]])
    df = df.with_columns(
        pl.lit(None).alias("n"),
        pl.lit(None).alias("r"),
        pl.lit(None).alias("y"),
        pl.lit(None).alias("ncc"),
        pl.lit(None).alias("btmwdth"),
        pl.lit(None).alias("chslp"),
        pl.lit(None).alias("musx"),
        pl.lit(None).alias("musk"),
        pl.lit(None).alias("topwdth"),
        pl.lit(None).alias("topwdthcc"),
        pl.lit(None).alias("topwdthcc_ml"),
        pl.Series([11.0, 13.0, 14.0, 15.0]).alias("topwdth_ml"),
        pl.Series([0.5, 0.3, 0.4, 0.1]).alias("y_ml"),
        pl.Series([2.0, 1.0, 1.5, 2.5]).alias("r_ml"),
    )

    return df


@pytest.fixture
def flowpath_attributes_other_values(flowpath_attributes_base_polars_df: pl.DataFrame) -> pl.DataFrame:
    """Results from other attribute calculations based on stream-order derived model"""
    df = flowpath_attributes_base_polars_df
    df = df.with_columns(
        pl.Series([0.096, 0.076, 0.060, 0.060]).alias("n"),
        pl.Series([0.192, 0.152, 0.12, 0.12]).alias("ncc"),
        pl.Series([1.6, 2.4, 3.5, 3.5]).alias("btmwdth"),
        pl.Series([0.03, 0.03, 0.03, 0.03]).alias("chslp"),
        pl.Series([0.2, 0.2, 0.2, 0.2]).alias("musx"),
        pl.Series([3600, 3600, 3600, 3600]).alias("musk"),
        pl.Series([33.0, 39.0, 42.0, 45.0]).alias("topwdthcc_ml"),
        pl.Series([11.0, 13.0, 14.0, 15.0]).alias("topwdth_ml"),
        pl.Series([0.5, 0.3, 0.4, 0.1]).alias("y_ml"),
        pl.Series([7.32, 9.265338, 10.634873, 11.727663]).alias("topwdthcc"),
        pl.Series([2.0, 1.0, 1.5, 2.5]).alias("r_ml"),
        pl.Series(
            [
                2.44,
                3.088446089287483,
                3.544958,
                3.9092210026373557,
            ]
        ).alias("topwdth"),
        pl.lit(None).alias("y"),
    )
    return df


@pytest.fixture
def flowpath_attributes_output() -> gpd.GeoDataFrame:
    """Full flowpath attributes output geometry"""
    data = {
        "fp_id": [1, 2, 3, 4],
        "stream_order": [1, 2, 3, 3],
        "total_da_sqkm": [1, 2, 3, 4],
        "mean_elevation": [(2 + 3) / 2, (1 + 3) / 2, (1 + 1) / 2, (2 + 2 + 2 + 3) / 4],
        "slope": [abs((3 - 2) / 3), abs((1 - 3) / 6.082763), 1e-4, abs((3 - 2) / 2.5)],
        "n": [0.096, 0.076, 0.060, 0.060],
        "r": [None, None, None, None],
        "y": [None, None, None, None],
        "ncc": [0.192, 0.152, 0.12, 0.12],
        "btmwdth": [1.6, 2.4, 3.5, 3.5],
        "chslp": [0.03, 0.03, 0.03, 0.03],
        "musx": [0.2, 0.2, 0.2, 0.2],
        "musk": [3600, 3600, 3600, 3600],
        "topwdth": [
            2.44,
            3.088446089287483,
            3.544958,
            3.9092210026373557,
        ],
        "topwdthcc": [7.32, 9.265338267862449, 10.634872991932461, 11.727663007912067],
        "topwdthcc_ml": [33.0, 39.0, 42.0, 45.0],
        "topwdth_ml": ([11.0, 13.0, 14.0, 15.0]),
        "y_ml": [0.5, 0.3, 0.4, 0.1],
        "r_ml": [2.0, 1.0, 1.5, 2.5],
    }
    geometry = [
        LineString([(-5, -1), (-5, -4)]),
        LineString([(-5, -4), (-4, 2)]),
        LineString([(1, 1), (-2, 1)]),
        MultiLineString([[(0, -3.5), (0, -1)], [(0, -1.5), (-1, -1)]]),
    ]
    gdf = gpd.GeoDataFrame(data=data, geometry=geometry, crs=4326)

    return gdf


class TestFlowpathAttributesSchemas:
    """Tests for Flowpath Attribute Schemas"""

    def test_flowpath_attributes_model_config(self) -> None:
        """Flowpath Attribues Model Config defaults"""
        model = FlowpathAttributesModelConfig()
        assert model.use_stream_order is True
        assert model.flowpath_id == "fp_id"
        assert model.dem_path == here() / Path("data/usgs_250m_dem_5070.tif")
        assert model.tw_path == here() / Path("data/TW_bf_predictions.parquet")
        assert model.y_path == here() / Path("data/Y_bf_predictions.parquet")

    @pytest.mark.parametrize(
        "func",
        [
            pytest.param(StreamOrder.n, id="Stream order mannings N"),
            pytest.param(StreamOrder.chsslp, id="Stream order chhslp"),
            pytest.param(StreamOrder.bw, id="Stream order bw"),
        ],
    )
    def test_stream_order(self, func: Any) -> None:
        """Stream order derived variable dictionaries"""
        stream_order_dict = func()
        assert isinstance(stream_order_dict, dict)
        assert list(stream_order_dict.keys()) == list(range(1, 11))
        for v in list(stream_order_dict.values()):
            assert isinstance(v, float)

    def test_flowpath_attributes_config__stream_order(self) -> None:
        """Flowpath Attributes Config using stream order derived variables"""
        cfg = FlowpathAttributesConfig(use_stream_order=True, stream_order=1, total_da_sqkm=1)
        assert cfg.stream_order == 1
        assert cfg.n == 0.096
        assert cfg.chslp == 0.03
        assert cfg.btmwdth == 1.6
        assert cfg.ncc == 0.192  # n * 2
        assert cfg.topwdthcc_ml is None
        assert cfg.y_ml is None
        assert cfg.r_ml is None
        assert cfg.topwdth == 2.44
        assert cfg.topwdthcc == 7.32
        assert cfg.mean_elevation is None
        assert cfg.slope is None
        assert cfg.musk == 3600
        assert cfg.musx == 0.2

    def test_flowpath_attributes_config__defaults(self) -> None:
        """Flowpath Attributes Config not using stream order derived variables (defaults)"""
        cfg = FlowpathAttributesConfig(use_stream_order=False, total_da_sqkm=1)
        assert cfg.stream_order is None
        assert cfg.n == 0.035
        assert cfg.chslp == 0.05
        assert cfg.btmwdth == 5
        assert cfg.ncc == 0.07  # n * 2
        assert cfg.topwdthcc == 7.32  # 3 * topwdth
        assert cfg.y is None
        assert cfg.topwdth == 2.44
        assert cfg.topwdthcc_ml is None
        assert cfg.y_ml is None
        assert cfg.topwdth_ml is None
        assert cfg.mean_elevation is None
        assert cfg.slope is None
        assert cfg.musk == 3600
        assert cfg.musx == 0.2

    def test_flowpath_attributes_config__complete(self) -> None:
        """Flowpath Attributes Config with all fields filled out"""
        cfg = FlowpathAttributesConfig(
            use_stream_order=True,
            stream_order=1,
            y_ml=1,
            topwdth_ml=2,
            mean_elevation=3,
            slope=0.05,
            total_da_sqkm=1,
        )
        assert cfg.stream_order == 1
        assert cfg.n == 0.096
        assert cfg.chslp == 0.03
        assert cfg.btmwdth == 1.6
        assert cfg.ncc == 0.192  # n * 2
        assert cfg.topwdth == 2.44
        assert cfg.topwdthcc == 7.32  # 3 * topwdth
        assert cfg.topwdth_ml == 2
        assert cfg.topwdthcc_ml == 6  # topwdth * 3
        assert cfg.y_ml == 1
        assert cfg.mean_elevation == 3
        assert cfg.slope == 0.05
        assert cfg.musk == 3600
        assert cfg.musx == 0.2


class TestFlowpathAttributes:
    """Tests for Flowpath Attributes"""

    def test_dem_attributes(
        self,
        flowpath_attributes_model_cfg: FlowpathAttributesModelConfig,
        flowpath_attributes_dummy_dem: Path,
        flowpath_attributes_dummy_gpkg: Path,
        flowpath_attributes_dem_values: gpd.GeoDataFrame,
    ) -> None:
        """Compares DEM values. Requires all un-called fixtures to run correctly."""
        try:
            gdf = gpd.read_file(flowpath_attributes_model_cfg.hf_path, layer="flowpaths")
            gdf = _dem_attributes(flowpath_attributes_model_cfg, gdf)
            assert_geodataframe_equal(gdf, flowpath_attributes_dem_values, check_less_precise=True)
        finally:
            flowpath_attributes_model_cfg.hf_path.unlink(missing_ok=True)

    def test_create_base_polars(
        self, flowpath_attributes_dummy_gpkg: Path, flowpath_attributes_base_polars_df: pl.DataFrame
    ) -> None:
        """Polars df setup correctly"""
        gdf = gpd.read_file(flowpath_attributes_dummy_gpkg, layer="flowpaths")
        output = _create_base_polars(gdf)
        assert_frame_equal(output, flowpath_attributes_base_polars_df)

    def test_riverml_attributes(
        self,
        flowpath_attributes_model_cfg: FlowpathAttributesModelConfig,
        flowpath_attributes_base_polars_df: pl.DataFrame,
        flowpath_attributes_riverml_values: pl.DataFrame,
        flowpath_attributes_riverml_parquets: dict[str, Path],
    ) -> None:
        """Join riverml attributes. Requires all un-called fixtures to run correctly."""
        output = _riverml_attributes(flowpath_attributes_model_cfg, flowpath_attributes_base_polars_df)
        assert_frame_equal(output, flowpath_attributes_riverml_values)

    def test_other_attributes(
        self,
        flowpath_attributes_model_cfg: FlowpathAttributesModelConfig,
        flowpath_attributes_riverml_values: pl.DataFrame,
        flowpath_attributes_other_values: pl.DataFrame,
    ) -> None:
        """Other stream attributes"""
        output = _other_flowpath_attributes(flowpath_attributes_model_cfg, flowpath_attributes_riverml_values)
        assert_frame_equal(output, flowpath_attributes_other_values)

    def test_flowpath_attributes_pipeline(
        self,
        flowpath_attributes_model_cfg: FlowpathAttributesModelConfig,
        flowpath_attributes_output: gpd.GeoDataFrame,
        flowpath_attributes_dummy_dem: Path,
        flowpath_attributes_dummy_gpkg: Path,
        flowpath_attributes_riverml_parquets: dict[str, Path],
    ) -> None:
        """Run whole pipeline. Requires all un-called fixtures to run correctly."""
        try:
            flowpath_attributes_pipeline(flowpath_attributes_model_cfg)
            output = gpd.read_file(flowpath_attributes_model_cfg.hf_path, layer="flowpaths")
            assert_geodataframe_equal(output, flowpath_attributes_output)

        finally:
            flowpath_attributes_model_cfg.hf_path.unlink(missing_ok=True)
