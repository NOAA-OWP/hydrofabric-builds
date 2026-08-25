"""A file to host all Hydrofabric Schemas"""

import os
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any, Self

import pyarrow as pa
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pyprojroot import here

from hydrofabric_builds.helpers.stats import weighted_circular_mean, weighted_geometric_mean


class Classifications(BaseModel):
    """A Pydantic BaseModel Container to contain flowpath_id classifications for aggregation"""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    aggregation_pairs: list[tuple[str, ...]] = Field(
        default_factory=list,
        description=(
            "A list of tuples for flowpaths to be aggregated together. Format: (downstream_id, upstream_id, ...) where downstream merges into upstream"
        ),
    )
    non_nextgen_flowpaths: set[str] = Field(
        default_factory=set,
        description=(
            "Reference flowpaths classified as 'virtual' tributaries. These are flowpaths that are stream-order 1, with a total DA of < threshold where routing will not be run"
        ),
    )
    independent_flowpaths: set[str] = Field(
        default_factory=set,
        description=(
            "Flowpaths that remain independent and are NOT aggregated. These are large catchments (areasqkm > threshold) that form their own divides"
        ),
    )
    connector_segments: list[str] = Field(
        default_factory=list,
        description=(
            "Small flowpaths (areasqkm < threshold) that connect two higher-order streams. These have two upstream flowpaths where both have streamorder > 1. They remain independent despite being small because they serve as connectors between large stream branches, and aggregation would present inconsistencies within routing"
        ),
    )
    virtual_flowpath_pairs: list[tuple[str, ...]] = Field(
        default_factory=list,
        description=(
            "List of virtual flowpath groups. Each tuple contains flowpath IDs "
            "that form a connected chain (upstream → downstream). "
            "Multiple tuples can have the same downstream target. "
            "Example: [('A', 'B', 'C'), ('D', 'E'), ('F',)] where A→B→C, D→E, and F "
            "are three separate virtual flowpaths."
        ),
    )
    non_nextgen_virtual_flowpath_pairs: list[tuple[str, ...]] = Field(
        default_factory=list,
        description=(
            "List of non-NextGen virtual flowpath groups. Same structure as "
            "virtual_flowpath_pairs but for flowpaths that don't connect to "
            "routing segments."
        ),
    )
    processed_flowpaths: set[str] = Field(
        default_factory=set,
        description=(
            "Set of all flowpath IDs that have been processed during the outlet upstream tracing. Used internally to prevent re-processing flowpaths by mistake (which creates cycles)"
        ),
    )
    force_queue_flowpaths: set[str] = Field(
        default_factory=set,
        description="flowpaths that are required to be queued. These are only for streams deeply nested in no-divide connectors",
    )
    aggregation_set: set[str] = Field(
        default_factory=set,
        description=("A set flowpaths that have been aggregated together"),
    )


class Aggregations(BaseModel):
    """A Pydantic BaseModel Container to contain flowpath_id classifications for aggregation"""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    aggregates: list[dict] = Field(
        description=(
            "A list of geometries for aggreagated flowpaths and divides along with upstream and downstream identifiers"
        ),
    )
    independents: list[dict] = Field(
        description=("A list of independent segments and their geometries"),
    )
    non_nextgen_flowpaths: list[dict] = Field(
        description=("A list of virtual flowpaths and their geometries"),
    )
    connectors: list[dict] = Field(
        description=("A list of connection segments and their geometries"),
    )
    virtual_flowpaths: list[dict] = Field(
        description=("A list of all virtual flowpaths and their geometries"),
    )
    non_nextgen_virtual_flowpaths: list[dict] = Field(
        description=("A list of all non_nextgen virtual flowpaths and their geometries"),
    )


class Flowpaths:
    """The schema for flowpaths table"""

    @classmethod
    def columns(cls) -> list[str]:
        """Returns the columns associated with this schema

        Returns
        -------
        list[str]
            The schema columns
        """
        return [
            "fp_id",
            "dn_nex_id",
            "up_nex_id",
            "div_id",
            "geometry",
        ]

    @classmethod
    def arrow_schema(cls) -> pa.Schema:
        """Returns the PyArrow Schema object.

        Returns
        -------
        pa.Schema
            PyArrow schema for flowpaths table
        """
        return pa.schema(
            [
                pa.field("fp_id", pa.int32(), nullable=False),
                pa.field("dn_nex_id", pa.int32(), nullable=False),
                pa.field("up_nex_id", pa.int32(), nullable=True),
                pa.field("div_id", pa.int32(), nullable=False),
                pa.field("geometry", pa.binary(), nullable=False),
            ]
        )


class Divides:
    """The schema for divides table"""

    @classmethod
    def columns(cls) -> list[str]:
        """Returns the columns associated with this schema

        Returns
        -------
        list[str]
            The schema columns
        """
        return ["div_id", "geometry"]

    @classmethod
    def arrow_schema(cls) -> pa.Schema:
        """Returns the PyArrow Schema object.

        Returns
        -------
        pa.Schema
            PyArrow schema for divides table
        """
        return pa.schema(
            [
                pa.field("div_id", pa.int32(), nullable=False),
                pa.field("geometry", pa.binary(), nullable=False),
            ]
        )


class Nexus:
    """The schema for Nexus table"""

    @classmethod
    def columns(cls) -> list[str]:
        """Returns the columns associated with this schema

        Returns
        -------
        list[str]
            The schema columns
        """
        return ["nex_id", "geometry"]

    @classmethod
    def arrow_schema(cls) -> pa.Schema:
        """Returns the PyArrow Schema object.

        Returns
        -------
        pa.Schema
            PyArrow schema for nexus table
        """
        return pa.schema(
            [
                pa.field("nex_id", pa.int32(), nullable=False),
                pa.field("geometry", pa.binary(), nullable=False),
            ]
        )


class HydrofabricDomains(StrEnum):
    """The domains used when querying the hydrofabric

    Attributes
    ----------
    AK : str
        Alaska
    CONUS : str
        Conterminous United States
    GL : str
        The US Great Lakes
    HI : str
        Hawai'i
    PRVI : str
        Puerto Rico, US Virgin Islands
    """

    AK = "ak_hf"
    CONUS = "conus_hf"
    GL = "gl_hf"
    HI = "hi_hf"
    PRVI = "prvi_hf"


class HydrofabricDomainsGPKG(StrEnum):
    """The domains used when querying the hydrofabric

    Attributes
    ----------
    AK : str
        Alaska
    CONUS : str
        Conterminous United States
    GL : str
        The US Great Lakes
    HI : str
        Hawai'i
    PRVI : str
        Puerto Rico, US Virgin Islands
    """

    AK = "AK"
    CONUS = "CONUS"
    GL = "GL"
    HI = "HI"
    PRVI = "PRVI"


class HydrofabricCRS(Enum):
    """The domains used when querying the hydrofabric

    Attributes
    ----------
    AK : str
        Alaska
    CONUS : str
        Conterminous United States
    GL : str
        The US Great Lakes
    HI : str
        Hawai'i
    PRVI : str
        Puerto Rico, US Virgin Islands
    """

    AK = 3338
    CONUS = 5070
    GL = 5070  # TEMP: MAY CHANGE
    HI = 102007
    PRVI = 32161


class BuildHydrofabricConfig(BaseModel):
    """Configs for buld hydrofabric stage"""

    reference_divides_path: str = Field(
        default="data/reference_divides.parquet",
        description="The location of the reference fabric divides.",
    )
    reference_flowpaths_path: str = Field(
        default="data/reference_flowpaths.parquet",
        description="The location of the reference fabric flowpaths.",
    )

    divide_aggregation_threshold: float = Field(
        default=3.0, description="Threshold for divides to aggreagate into an upstream catchment [km^2]"
    )

    debug_outlet_count: int | None = Field(
        default=None,
        description="Debug setting to limit the number of outlets processed. None (default) processes all outlets. Set to a positive integer to limit for testing.",
    )

    @field_validator("debug_outlet_count")
    @classmethod
    def validate_debug_outlet_count(cls, v: int | None) -> int | None:
        """Validate debug_outlet_count is None or positive."""
        if v is not None and v <= 0:
            raise ValueError("debug_outlet_count must be None (for all outlets) or a positive integer")
        return v


class AggTypeEnum(StrEnum):
    """Zonal statistics aggregation types"""

    mean = "mean"
    mode = "mode"
    max = "max"
    circular_mean = "weighted_circular_mean"
    quantile_dist = "quantile_dist"
    quartile_dist = "quartile_dist"
    geom_mean = "weighted_geometric_mean"
    percent = "percent"
    groundwater = "groundwater"


def get_operation(op: str) -> Any:
    """Helper to return zonal statistics aggregation type

    Parameters
    ----------
    op : str
        requested operation

    Returns
    -------
    Any
        string operation, list of string operations, or callable custom function
    """
    assert op in AggTypeEnum, ValueError("Invalid aggregation type")

    # make a mapping from the Enum keys where {key: key}
    mapping = dict(zip(AggTypeEnum.__members__.keys(), AggTypeEnum.__members__.keys(), strict=False))
    mapping.update(
        {
            "weighted_circular_mean": weighted_circular_mean,  # type: ignore[dict-item]
            "weighted_geometric_mean": weighted_geometric_mean,  # type: ignore[dict-item]
            "quartile_dist": ["quantile(q=0.25)", "quantile(q=0.5)", "quantile(q=0.75)", "quantile(q=1.0)"],  # type: ignore[dict-item]
            "quantile_dist": [
                "quantile(q=0.1)",
                "quantile(q=0.2)",
                "quantile(q=0.3)",
                "quantile(q=0.4)",
                "quantile(q=0.5)",
                "quantile(q=0.6)",
                "quantile(q=0.7)",
                "quantile(q=0.8)",
                "quantile(q=0.9)",
                "quantile(q=1)",
            ],  # type: ignore[dict-item]
        }
    )

    return mapping[op]


class DivideAttributeConfig(BaseModel):
    """Pydantic model for divide attributes attribute configuration"""

    data_dir: Path = Field(
        description="Top level directory for data layers", default=here() / "data/divide_attributes"
    )
    agg_type: AggTypeEnum = Field(description="Zonal stats aggregation type")
    field_name: str = Field(description="Output field name for divide attribute")
    file_name: Path = Field(description="File path of attribute raster")
    tmp: Path = Field(
        description="Temp file path for parquet",
        default_factory=lambda data: Path("/tmp/divide-attributes") / f"tmp_{data['field_name']}.parquet",
    )

    @model_validator(mode="after")
    def make_tmp_dir(self: Any) -> Self:  # type: ignore[misc,type-var]
        """Model validator to create a temp directory if it does not exist"""
        self.tmp.parents[0].mkdir(parents=True, exist_ok=True)
        return self

    @model_validator(mode="after")
    def full_file_name(self: Any) -> Self:  # type: ignore[misc,type-var]
        """Join the root data dir to the file name"""
        self.file_name = self.data_dir / self.file_name
        return self


class DivideAttributesModelConfig(BaseModel):
    """Pydantic model for divide attributes model configuration"""

    hf_path: Path = Field(None, description="Path to input and output hydrofabric")
    crs: str = Field(description="Domain CRS", default="EPSG:5070")
    processes: int = Field(
        description="Number of processes to use for multiprocessing", default=os.cpu_count()
    )
    data_dir: Path = Field(
        description="Directory of all input data", default=here() / "data/divide_attributes"
    )
    divide_id: str = Field(description="Field name for unique divide id", default="div_id")
    attributes: list[DivideAttributeConfig] = Field(
        None, description="List of attributes to be computed. Specify in DivideAttributeConfig data model."
    )
    divides_path_list: list[Path] | None = Field(
        description="List of divides paths to use for parallel run. ex. list of VPU subsets.",
        default=None,
    )
    tmp_dir: Path = Field(description="Temp path for saving files", default=Path("/tmp/divide-attributes"))
    split_vpu: bool | None = Field(
        description="If running in parallel, this will split the domain divides file into separate files."
        "Each VPU can be run separately and will be stitched at end."
        "This will replace anything input to the `divides_path_list`",
        default=False,
    )
    debug: bool = Field(
        description="Setting debug to true will save all temporary files. Setting to false will delete files if run fails.",
        default=False,
    )

    @model_validator(mode="after")
    def make_tmp_dir(self: Any) -> Self:  # type: ignore[misc,type-var]
        """Model validator to create a temp directory if it does not exist"""
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        return self


class FlowpathAttributesModelConfig(BaseModel):
    """Configurations for running flowpath attributes"""

    hf_path: Path = Field(
        None,
        title="Hydrofabric Path",
        description="Path to input and output hydrofabric",
    )
    flowpath_id: str = Field(default="fp_id", title="Flowpath ID", description="Flowpath ID field")
    use_stream_order: bool = Field(
        title="Stream Order Setting",
        description="Setting to use stream order to calculate Manning's n (n), Bottom Width (BtmWdth), and Channel side slope (ChSlp). When true, calculate these attributes from stream order. When false, use defaults.",
        default=True,
    )
    dem_path: Path = Field(
        default=here() / Path("data/usgs_250m_dem_5070.tif"), title="DEM Path", description="Path to DEM"
    )
    tw_path: Path = Field(
        default=here() / Path("data/TW_bf_predictions.parquet"),
        title="Topwidth Path",
        description="Path to RiverML topwidth predictions",
    )
    y_path: Path = Field(
        default=here() / Path("data/Y_bf_predictions.parquet"),
        title="Y Path",
        description="Path to RiverML Y predictions",
    )
    r_path: Path = Field(
        default=here() / Path("data/r_predictions.parquet"),
        title="R Path",
        description="Path to RiverML R predictions",
    )


class StreamOrder:
    """Returns dictionary of stream-order derived parameters from WRF GIS Preprocessor

    Source: https://github.com/NCAR/wrf_hydro_gis_preprocessor/blob/5781ad4788434e8fd4ec16f3a3805d98536a9f82/wrfhydro_gis/wrfhydro_functions.py#L128
    Accessed 10/20/25
    """

    @classmethod
    def n(cls) -> dict:
        """Order-based Mannings N values for Strahler orders 1-10"""
        return {
            1: 0.096,
            2: 0.076,
            3: 0.060,
            4: 0.047,
            5: 0.037,
            6: 0.030,
            7: 0.025,
            8: 0.021,
            9: 0.018,
            10: 0.022,
        }

    @classmethod
    def chsslp(cls) -> dict:
        """Order-based Channel Side-Slope values for Strahler orders 1-10"""
        return {1: 0.03, 2: 0.03, 3: 0.03, 4: 0.04, 5: 0.04, 6: 0.04, 7: 0.04, 8: 0.04, 9: 0.05, 10: 0.10}

    @classmethod
    def bw(cls) -> dict:
        """Order-based Bottom-width values for Strahler orders 1-10"""
        return {1: 1.6, 2: 2.4, 3: 3.5, 4: 5.3, 5: 7.4, 6: 11.0, 7: 14.0, 8: 16.0, 9: 26.0, 10: 110.0}


class FlowpathAttributesConfig(BaseModel):
    """Flowpath attributes model to configure and calculate attributes

    Defaults from WRF GIS Preprocessor
    Source: https://github.com/NCAR/wrf_hydro_gis_preprocessor/blob/5781ad4788434e8fd4ec16f3a3805d98536a9f82/wrfhydro_gis/wrfhydro_functions.py#L128
    Accessed 10/20/25
    """

    use_stream_order: bool = Field(
        title="Stream Order Setting",
        description="Setting to use stream order to calculate Manning's n (n), Bottom Width (BtmWdth), and Channel side slope (ChSlp). When true, calculate these attributes from stream order. When false, use defaults.",
        default=True,
    )
    stream_order: int | None = Field(
        None, title="Strahler Stream Order", description="Strahler Stream Order 1-10"
    )
    total_da_sqkm: float = Field(
        title="Drainage Area (km2)", description="Drainage area (sqkm) from flowpaths"
    )
    y: float | None = Field(
        None, title="Estimated Depth", description="Estimated depth associated with TopWdth (m)", alias="Y"
    )
    r: float | None = Field(None, title="Dingman's r", description="Dingmans's r", alias="r")
    n: float = Field(
        title="Mannning's in channel roughness",
        description="Manning's in channel roughness / n. Can be derived from Strahler stream order. Defaults to 0.035 without stream order",
        default=0.035,
        alias="n",
    )
    ncc: float | None = Field(
        None,
        title="Compound Channel Top Width",
        description="Compound Channel Top Width (m). 2*n",
        alias="nCC",
    )
    btmwdth: float = Field(
        title="Bottom width of channel",
        description="Bottom width of channel (m). Can be derived from Strahler stream order. Defaults to 5 without stream order",
        default=5,
        alias="BtmWdth",
    )
    topwdth: float | None = Field(None, title="Top Width", description="Top Width (m)", alias="TopWdth")
    topwdthcc: float | None = Field(
        None,
        title="Compound Channel Top Width",
        description="Compound Channel Top Width (m)",
        alias="TopWdthCC",
    )
    chslp: float = Field(
        title="Channel Side Slope",
        description="Channel side slope. Can be derived from Strahler stream order. Defaults to 0.05 without stream order.",
        default=0.05,
        alias="ChSlp",
    )
    mean_elevation: float | None = Field(
        None, title="Elevation", description="Mean elevation (m) between nodes from 3DEP", alias="alt"
    )
    slope: float | None = Field(
        None, title="Slope", description="Slope (meters/meters) computed from 3DEP", alias="So"
    )
    musx: float = Field(
        title="Muskingum Weighting Coeffiecent",
        description="Muskingum Weighting Coefficient. Defaults to 0.2",
        default=0.2,
        alias="MusX",
    )
    musk: float = Field(
        title="Muskingum routing time",
        default=3600,
        description="Muskingum routing time (seconds). Defaults to 3600",
        alias="MusK",
    )
    y_ml: float | None = Field(
        None,
        title="Estimated Depth ML",
        description="Estimated depth associated with TopWdth (m) calculated from RiverML",
        alias="Y_ml",
    )
    r_ml: float | None = Field(
        None, title="Dingman's r ML", description="Dingmans's r calculated from RiverML", alias="r_ml"
    )
    topwdth_ml: float | None = Field(
        None, title="Top Width ML", description="Top Width (m) calculated from RiverML", alias="TopWdth_ml"
    )
    topwdthcc_ml: float | None = Field(
        None,
        title="Compound Channel Top Width ML",
        description="Compound Channel Top Width (m) calculated from RiverML",
        alias="TopWdthCC_ml",
    )

    model_config = ConfigDict(
        populate_by_name=True,
    )

    @model_validator(mode="after")
    def stream_order_classify(self: Any) -> Any:
        """Model validator derive variables from stream order"""
        if self.use_stream_order and self.stream_order:
            for field, func in zip(
                ["n", "chslp", "btmwdth"],
                [StreamOrder.n, StreamOrder.chsslp, StreamOrder.bw],
                strict=False,
            ):
                try:
                    setattr(self, field, func()[self.stream_order])
                except KeyError:
                    continue

        # set compound channel - from WRF-Hydro
        self.ncc = 2 * self.n

        # topwdth - from WRF-Hydro
        self.topwdth = 2.44 * (self.total_da_sqkm**0.34)

        # set topwdthcc - from WRF-Hydro
        self.topwdthcc = (3 * self.topwdth) if self.topwdth else None

        # set topwdthcc - from WRF-Hydro
        self.topwdthcc_ml = (3 * self.topwdth_ml) if self.topwdth_ml else None

        return self


class GageInput(BaseModel):
    """gages: gageinput class"""

    dir: Path | None = None
    path: Path | None = None
    gage_source_crs: str = "EPSG:4326"
    id_col_name: str = "site_no"
    x_col_name: str | None = None
    y_col_name: str | None = None
    area_col_name: str = "area_sqkm"


class GagesInputs(BaseModel):
    """gages: configs collection for all inputs"""

    usgs_discontinued: GageInput = Field(
        default_factory=lambda: GageInput(dir=Path("usgs_gages_discontinued"))
    )
    usgs_active: GageInput = Field(default_factory=lambda: GageInput(dir=Path("usgs_active_gages")))
    txdot_gages: GageInput = Field(
        default_factory=lambda: GageInput(path=Path("TXDOT_gages/TXDOT_gages.txt"))
    )
    CADWR_ENVCA: GageInput = Field(
        default_factory=lambda: GageInput(
            path=Path("CADWR_ENVCA/gage_xy.csv"), x_col_name="lon", y_col_name="lat"
        )
    )
    CIROH_UA: GageInput = Field(
        default_factory=lambda: GageInput(
            path=Path("CIROH_UA/gage_area.csv"), id_col_name="gage", area_col_name="area_sqkm"
        )
    )
    nwm_calib_gages: GageInput = Field(
        default_factory=lambda: GageInput(path=Path("nwm_calib/nwm_calib_gages_07112025.csv"))
    )


class GagesTarget(BaseModel):
    """gages: target/output configs"""

    crs: str = "EPSG:5070"
    snap_tolerance_m: float = 100.0
    update_existing: bool = True
    exclude_ids: list[str | int] = Field(default_factory=lambda: ["15056210", "15493000"])
    out_gpkg: Path = Path("gages.gpkg")
    gpkg_layer_name: str = "gages"


class GagesBlock(BaseModel):
    """aggregating all inputs gages classes configs here"""

    input_dir: Path = Path("data/gages")
    inputs: GagesInputs = Field(default_factory=GagesInputs)
    target: GagesTarget = Field(default_factory=GagesTarget)


class NLDIUpstreamBasins(BaseModel):
    """gages: for getting the upstream basins from NLDI USGS API"""

    run_NLDI_upstream_basins: bool = False
    path: Path = Path("nldi_upstream_basins.gpkg")
    layer_polys: str = "NLDI_upstream_basins"
    layer_points: str = "sites"


class AssignFPConfig(BaseModel):
    """gages: a class for assigning flowpaths to gages"""

    rel_err: float = 0.25
    buffer_m: float = 500.0
    parallel: bool = False
    max_workers: int | None = None
    USGS_NLDI_crs: str = "EPSG:4326"
    work_crs: str = "EPSG:5070"


# --- your top-level gages config now has defaults ---
class GagesConfig(BaseModel):
    """Gages config class"""

    gages: GagesBlock = Field(default_factory=GagesBlock)
    NLDI_upstream_basins: NLDIUpstreamBasins = Field(default_factory=NLDIUpstreamBasins)
    assign_fp_to_gages: AssignFPConfig = Field(default_factory=AssignFPConfig)

    model_config = ConfigDict(extra="ignore")

    def resolve_paths(self, base: Path | None = None) -> "GagesConfig":
        """Resolves paths"""
        base = base or Path.cwd()

        def _resolve(p: Path | None, root: Path) -> Path | None:
            """Resolve paths by getting absolutes"""
            if p is None:
                return None
            return p if p.is_absolute() else (root / p)

        input_root = self.gages.input_dir or base
        for inp in [
            self.gages.inputs.usgs_discontinued,
            self.gages.inputs.usgs_active,
            self.gages.inputs.txdot_gages,
            self.gages.inputs.CADWR_ENVCA,
            self.gages.inputs.nwm_calib_gages,
        ]:
            if inp.dir is not None:
                inp.dir = _resolve(inp.dir, input_root)
            if inp.path is not None:
                inp.path = _resolve(inp.path, input_root)

        self.gages.target.out_gpkg = _resolve(self.gages.target.out_gpkg, base)  # type: ignore
        self.NLDI_upstream_basins.path = _resolve(self.NLDI_upstream_basins.path, base)  # type: ignore
        return self


# RFC-DA Configs
class ResNWMLakesInputs(BaseModel):
    """NWM preparation inputs to generate RFC-DA"""

    prep_nwm_lakes: bool = Field(
        default=False,
        description="Prepare NWM/HF 2.2 lakes to be used in RFCDA. Generally, this file will be available on s3 and synced.",
    )
    input_path: Path = Field(
        default=Path("source_files/nwm_patch_conus_nextgen.gpkg"),
        description="Source path if preparing NWM lakes from scratch. When using defaults, WaterbodiesConfig will inject preceding input path.",
    )
    layer: str = Field(default="lakes")
    buffer_size_m: int | float = Field(
        default=500, description="Buffer size for waterbodies when matching to lakes"
    )
    output_path: Path = Field(
        default=Path("source_files/nwm_lakes.gpkg"),
        description="Output path if creating or file to use in pipeline if not creating. When using defaults, WaterbodiesConfig will inject preceding input path.",
    )


class ResNIDInputs(BaseModel):
    """NID inputs to generate RFC-DA"""

    path: Path = Field(
        default="source_files/NID2019_U.csv",
        description="Source path. When using defaults, WaterbodiesConfig will inject preceding input path.",
    )
    src_crs: str | None = Field(default="EPSG:4326", description="Source CRS")
    output_crs: str = Field(default="EPSG:5070", description="Output CRS")
    drop_states: list[str] | None = Field(
        default=["AK", "HI", "PR", "GU"], description="States to drop from NID"
    )


class ResReferenceWaterbodiesInputs(BaseModel):
    """Reference waterbodies inputs to generate RFC-DA"""

    path: Path = Field(
        default="reference_reservoirs/reference_waterbodies.gpkg",
        description="Source path. When using defaults, WaterbodiesConfig will inject preceding input path.",
    )
    layer: str = Field(default="reference_waterbodies", description="GPKG layer")
    src_crs: str | None = Field(None, description="Source CRS")
    output_crs: str = Field(default="EPSG:5070", description="Output CRS")
    id_col: str = Field(default="comid", description="Reference waterbodies ID column")


class ResReferenceReservoirsInputs(BaseModel):
    """Reference reservoirs inputs to generate RFC-DA"""

    path: Path = Field(
        default="reference_reservoirs/reference-reservoirs-v1.gpkg",
        description="Source path. When using defaults, WaterbodiesConfig will inject preceding input path.",
    )
    layer: str = Field(default="reference-reservoirs-v1", description="GPKG layer")
    src_crs: str | None = Field(None, description="Source CRS")
    output_crs: str = Field(default="EPSG:5070", description="Output CRS")
    distance_to_fp_col: str = Field(default="distance_to_fp_m", description="Distance to flowpath (m) column")
    wb_area_col: str = Field(default="wb_areasqkm", description="Area (km2) column")
    ref_wb_id_col: str = Field(default="ref_fab_wb", description="Reference waterbody ID column")
    min_wb_area_sqkm: float = Field(
        default=0.2, description="Minimum waterbody area (km2) to keep for RFC-DA. Use 0 to remove None."
    )
    max_distance_m: float = Field(
        default=1000.0,
        description="max distance of reference reservoir points from column 'distance_to_fp_m'",
    )


class ResOSMInputs(BaseModel):
    """OSM inputs to generate RFC-DA"""

    path: Path = Field(
        default="source_files/osm_dams_all.gpkg",
        description="Source path. When using defaults, WaterbodiesConfig will inject preceding input path.",
    )
    layer: str = Field(default="osm_dams_all", description="GPKG layer")
    filter_col: str = Field(
        default="waterway", description="column's name which has dam and non-dam infrastructures"
    )
    filter_val: str = Field(default="dam", description="value used to filter column")


class ResDEMInputs(BaseModel):
    """DEM inputs to generate RFC-DA"""

    path: Path = Field(
        default="source_files/USGS_Seamless_DEM_13.vrt",
        description="Source path. When using defaults, WaterbodiesConfig will inject preceding input path.",
    )
    prefer_crs_of_dem: bool = Field(
        default=True, description="Reproject polygons to DEM CRS before sampling of True"
    )
    band: int = Field(default=1, description="Band of raster as opened in rasterio")
    nodata: int | float | None = Field(None, description="Nodata value. Let rasterio infer if null")


class ResRules(BaseModel):
    """Rules for generating RFC-DA"""

    max_waterbody_nearest_dist_m: float = Field(
        default=1000.0, description="Max waterbdody nearest distance (m) for nearest WB ↔ dam selection"
    )
    min_area_sqkm: float = Field(
        default=0.2,
        description="Removes waterbodies smaller than this threshold. Use 0 to remove none.",
    )


class WaterbodiesConfig(BaseModel):
    """Config for waterbodies. Includes RFC-DA configs."""

    input_dir: Path = Field(
        default=here() / Path("data/reservoirs"),
        description="Input data directory. For defaults, this will be prepended to datasets.",
    )
    output_dir: Path = Field(
        default=here() / Path("data/reservoirs/output"),
        description="Output directory. For defaults, will be prepended to output RFCDA name",
    )
    rfcda_output_name: str = Field(default="rfc-da-hydraulics-v1.gpkg", description="Output gpkg name")
    rfcda_file: Path = Field(
        default_factory=lambda data: data["output_dir"] / data["rfcda_output_name"],
        description="Full file path. By default will concatenate output dir and file name",
    )
    default_src_crs: str = Field(default="EPSG:4326", description="Default source CRS")
    work_crs: str = Field(
        default="EPSG:5070",
        description="CRS for projected ops, distances, buffers. area-equal crs for SuperCONUS",
    )

    nid: ResNIDInputs = Field(default=ResNIDInputs(), description="NID config")
    refwb: ResReferenceWaterbodiesInputs = Field(
        default=ResReferenceWaterbodiesInputs(), description="Reference waterbodies config"
    )
    refres: ResReferenceReservoirsInputs = Field(
        default=ResReferenceReservoirsInputs(), description="Reference reservoirs configs"
    )
    osm: ResOSMInputs = Field(default=ResOSMInputs(), description="OSM configs")
    dem: ResDEMInputs = Field(default=ResDEMInputs(), description="DEM configs")
    nwm_lakes: ResNWMLakesInputs = Field(
        default=ResNWMLakesInputs(),
        description="NWM Lakes configuration. Includes if data prep should be run.",
    )
    rules: ResRules = Field(default=ResRules(), description="Rules")

    @model_validator(mode="after")
    def inject_dirs(self: Any) -> Self:  # type: ignore[misc,type-var]
        """Inject input directories into each input config path"""
        self.nid.path = self.input_dir / self.nid.path
        self.osm.path = self.input_dir / self.osm.path
        self.refwb.path = self.input_dir / self.refwb.path
        self.refres.path = self.input_dir / self.refres.path
        self.dem.path = self.input_dir / self.dem.path
        self.nwm_lakes.input_path = self.input_dir / self.nwm_lakes.input_path
        self.nwm_lakes.output_path = self.input_dir / self.nwm_lakes.output_path

        return self


### fp_crosswalk  ###
class FPCrosswalkReference(BaseModel):
    """fp_crosswalk: reference (file1) network"""

    id_col: str = "flowpath_id"


class FPCrosswalkTarget(BaseModel):
    """fp_crosswalk: target (file2) network"""

    path: Path = Path("data/nwm_flows.gpkg")
    layer: str = "nwm_streams"
    id_col: str = "ID"


class FPCrosswalkOutputs(BaseModel):
    """fp_crosswalk: final matches table inside a GPKG (no geometry is fine)"""

    path: Path = Path("data/fp_crosswalk_matches.parquet")


class FPCrosswalkConfig(BaseModel):
    """Config class for the `fp_crosswalk:` block in the YAML."""

    # nested blocks
    reference: FPCrosswalkReference = Field(default_factory=FPCrosswalkReference)
    target: FPCrosswalkTarget = Field(default_factory=FPCrosswalkTarget)
    outputs: FPCrosswalkOutputs = Field(default_factory=FPCrosswalkOutputs)

    # geometric / matching params
    search_radius_m: float = 15.0
    percent_inside_min: float = 0.005
    node_snap_tol_m: float = 0.01
    endpoint_buffer_m: float = 20.0

    # parallelization
    parallel: bool = True
    n_jobs: int = 3
    parallel_chunksize: int = 200

    path: Path = Field(default=Path("/data/nhd_crosswalk.parquet"))

    model_config = ConfigDict(extra="ignore")

    def resolve_paths(self, base: Path | None = None) -> "FPCrosswalkConfig":
        """
        Resolve all relative paths against `base`.

        Usage:
            fp_cfg = FPCrosswalkConfig(**raw["fp_crosswalk"]).resolve_paths(base)
        """
        base = base or Path.cwd()

        def _resolve(p: Path, root: Path) -> Path:
            return p if p.is_absolute() else (root / p)

        # reference / target files
        self.reference.path = _resolve(self.reference.path, base)
        self.target.path = _resolve(self.target.path, base)

        # checkpoint + outputs
        self.checkpoint.path = _resolve(self.checkpoint.path, base)
        self.outputs.matches_gpkg = _resolve(self.outputs.matches_gpkg, base)

        return self
