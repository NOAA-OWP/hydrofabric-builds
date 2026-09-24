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
    non_nextgen_virtual_flowpath_pairs: list[tuple[str, ...]] = Field(
        default_factory=list,
        description="List of non-NextGen virtual flowpath pairs (source_id, target_id).",
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
            "fp_to_id",
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
                pa.field("fp_to_id", pa.int32(), nullable=True),
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
    HI = 32604
    PRVI = 6566


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

    headwater_virtual_length_threshold: float = Field(
        default=0.3,
        description="Order-1 headwater flowpaths shorter than this length (km) are virtualized instead of independent",
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
    domain_mask: Path | None = Field(
        default=None,
        description="Mask a domain to only calculate attributes for a subset. Built to accommodate AK domain being smaller than full state.",
    )
    domain_mask_layer: str | None = Field(default=None, description="GPKG layer to use for mask")
    divides_masked: Path | None = Field(default=None, description="Path to saved masked divides to")

    @model_validator(mode="after")
    def make_tmp_dir(self: Any) -> Self:  # type: ignore[misc,type-var]
        """Model validator to create a temp directory if it does not exist"""
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        return self

    @model_validator(mode="after")
    def make_divides_mask(self: Any) -> Self:  # type: ignore[misc,type-var]
        """Model validator to create a path to save the divides mask if none input"""
        if self.domain_mask and not self.divides_masked:
            self.divides_masked = self.data_dir / "divides_mask.gpkg"
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
        default=None,
        title="Topwidth Path",
        description="Path to RiverML topwidth predictions. If None, it will be skipped.",
    )
    y_path: Path = Field(
        default=None,
        title="Y Path",
        description="Path to RiverML Y predictions. If None, it will be skipped.",
    )
    r_path: Path = Field(
        default=None,
        title="R Path",
        description="Path to RiverML R predictions. If None, it will be skipped.",
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
    status_col_name: str | None = None


class NWMRFCInput(BaseModel):
    """NWM reservoir index file for retaining RFC and USACE gages. USACE IDs are found in NID."""

    path: Path = Path("rfc/reservoir_index_AnA.nc")
    rfc_id_col: str = "rfc_gage_id"
    usace_id_col: str = "usace_gage_id"


class GagesInputs(BaseModel):
    """gages: configs collection for all inputs"""

    usgs_discontinued: GageInput = Field(
        default_factory=lambda: GageInput(dir=Path("usgs_gages_discontinued"))
    )
    usgs_active: GageInput = Field(default_factory=lambda: GageInput(dir=Path("usgs_active_gages")))
    txdot_gages: GageInput = Field(
        default_factory=lambda: GageInput(path=Path("TXDOT_gages/TXDOT_gages.txt"))
    )
    other: GageInput = Field(
        default_factory=lambda: GageInput(path=Path("other/gage_xy.csv"), x_col_name="lon", y_col_name="lat")
    )
    CIROH_UA: GageInput = Field(
        default_factory=lambda: GageInput(
            path=Path("CIROH_UA/gage_area.csv"), id_col_name="gage", area_col_name="area_sqkm"
        )
    )
    nwm_calib_gages: GageInput = Field(
        default_factory=lambda: GageInput(path=Path("nwm_calib/nwm_calib_gages_07112025.csv"))
    )
    routelink: GageInput = Field(
        default_factory=lambda: GageInput(path=Path("RouteLink_CONUS_EPSG4326.gpkg"), id_col_name="gages")
    )
    rfc: GageInput = Field(
        default_factory=lambda: GageInput(
            path=Path("rfc/nwps_all_gauges_report.csv"),
            id_col_name="nws shef id",
            x_col_name="longitude",
            y_col_name="latitude",
            status_col_name="forecast status",
        ),
        description="Table of active NWS gages. Retrieved from https://water.noaa.gov/about/data-and-web-services-catalog on 6/15/26",
    )
    nid: GageInput = Field(
        default_factory=lambda: GageInput(
            path=Path(
                "rfc/NID2019_U.csv",
            ),
            id_col_name="NIDID",
            x_col_name="LONGITUDE",
            y_col_name="LATITUDE",
        )
    )
    nwm_rfc: NWMRFCInput = Field(
        default=NWMRFCInput(), description="An NWM v3 reservoid index file with RFC gages to retain"
    )
    adhoc_lakes: GageInput = Field(
        default_factory=lambda: GageInput(
            path=Path("rfc/adhoc_lakes.gpkg"), id_col_name="locationId", x_col_name="Lon", y_col_name="Lat"
        ),
        description="Adhoc lakes from reference waterbodies",
    )
    canada_great_lakes: bool = Field(
        default=False,
        description="Flag to pull Lake Erie and Lake Ontario Canadian gages from GreatLakesMapping class defined in Lakes",
    )
    usbr: GageInput = Field(
        default_factory=lambda: GageInput(
            path=Path("other/usbr.gpkg"), id_col_name="locId", x_col_name="Lon", y_col_name="Lat"
        ),
        description="USBR lakes",
    )
    usace: GageInput = Field(
        default_factory=lambda: GageInput(path=Path("other/usace_crosswalk.gpkg"), id_col_name="location"),
        description="USACE gages/reservoirs",
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
    prebuilt_gages: Path | None = Field(
        default=None,
        description="Path to a pre-built gages gpkg. If set, skip gage collection (steps 1-8) and use this table for assignment.",
    )
    prebuilt_gages_layer: str = Field(
        default="gages",
        description="Layer name in the pre-built gages gpkg.",
    )


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
    override_fp_path: Path | None = Field(
        default=None,
        description="A csv with columns `site_no`, `fp_id`, and `virtual_fp_id` to override algorithmically chosen flowpath associations",
    )


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
            self.gages.inputs.other,
            self.gages.inputs.nwm_calib_gages,
        ]:
            if inp.dir is not None:
                inp.dir = _resolve(inp.dir, input_root)
            if inp.path is not None:
                inp.path = _resolve(inp.path, input_root)

        self.gages.target.out_gpkg = _resolve(self.gages.target.out_gpkg, base)  # type: ignore
        self.NLDI_upstream_basins.path = _resolve(self.NLDI_upstream_basins.path, base)  # type: ignore
        return self


### Lakes ###
class NIDInputs(BaseModel):
    """Lakes: National Inventory of Dams (NID) inputs to improve hydaulic paramters. NID is available for Reference Reservoirs data."""

    path: Path = Field(
        default="input/NID2019_U.csv",
        description="Source path. When using defaults, WaterbodiesConfig will inject preceding input path.",
    )
    src_crs: str | None = Field(default="EPSG:4326", description="Source CRS")
    output_crs: str = Field(default="EPSG:5070", description="Output CRS")
    drop_states: list[str] | None = Field(
        default=["AK", "HI", "PR", "GU"], description="States to drop from NID"
    )


class LakesDEMInputs(BaseModel):
    """Lakes: DEM inputs for Lakes"""

    path: Path = Field(
        default="input/COP90_DEM_SuperCONUS.tif",
        description="Source path. LakesConfig will inject preceding input path.",
    )
    nodata: int | float | None = Field(None, description="Nodata value. Let rasterio infer if null")


class ReferenceReservoirsInput(BaseModel):
    """Lakes: Reference Reservoirs is a point dataset of dams including hydraulic parameters from the National Inventory of Dams (NID)."""

    path: Path = Field(
        default=Path("input/reference-reservoirs-v1.gpkg"),
        description="Source path. When using defaults, LakesConfig will inject preceding input path.",
    )
    layer: str = Field(default="reference-reservoirs-v1", description="GPKG layer")
    run: bool = Field(
        default=True,
        description="Flag to run reference reservoirs input. Must be set to false if file is not present.",
    )
    src_crs: str | None = Field(default=None, description="Source CRS")
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
    ref_res_keep: list[str] = Field(
        default=[], description="List of reference reservoir dam_id's to keep in RFCDA layer"
    )


class NWMLakeInput(BaseModel):
    """Lakes: NWM Lakes is a polygon dataset with COMID. This is the operational lakes layer provided by OWP."""

    path: Path = Field(
        default=Path("input/nwm_lakes.gpkg"),
        description="Source path. LakesConfig will inject preceding input path.",
    )
    buffered_path: Path = Field(
        default=Path("input/nwm_lakes_sconus_input_500m_buffer.gpkg"),
        description="Source path, with polygons buffered out (for validating duplicate lake points). LakesConfig will inject preceding input path.",
    )
    layer: str = Field(default="lakes", description="GPKG layer")
    fp_associated_path: Path = Field(
        default=Path("output/fp_associated.gpkg"),
        description="A temporary layer to read lakes from where flowpaths have been associated and attributes joined. Skips flowpath association.",
    )
    run: bool = Field(
        default=True, description="Flag to run NWM lakes input. Must be set to false if file is not present."
    )
    improve_placement_ref_res: bool = Field(
        default=True, description="Flag to use reference reservoirs to improve placement of lakes."
    )
    improve_placement_path: Path = Field(
        default=Path("output/fp_improved placement.gpkg"),
        description="A temporary layer to read lakes from where flowpaths have been associated, attributes joined, and reference reservoirs folded in to improve placement. Skips reference reservoirs folding.",
    )
    use_cached_improve_placement: bool = Field(
        default=False,
        description="Use the cached layer stored at `improve_placement_path` regardless of whether the process is requested to run.",
    )
    associate_flowpaths: bool = Field(default=True, description="Flag to run flowpath association")
    flowpath_association_method: str = Field(
        default="polygon_outlet",
        description="Type of flowpath association. Options are `polygon_outlet` or `nearest_point`",
    )
    search_radius_m: float = Field(
        default=1000.0, description="Distance from flowpath to search when associating flowpaths with points."
    )
    max_refres_search_distance_m: float = Field(
        default=500.0,
        description="Distance between NWM lake and reference reservoirs used when improving NWM lake placement.",
    )
    intersection_length_min_m: float = Field(
        default=3.0,
        description="Minimum prefered intersection lenght when associating polygons with flowpaths. If the flowpath intersection is extremely short, it can sometimes be almost entirely on a long downstream flowpath.",
    )
    id_field: str = Field(default="newID", description="ID field for NWM lakes input")
    attrib_src_path: Path | None = Field(
        default=None,
        description="Source file for joining attributes from another file. It will be skipped if null.",
    )
    attrib_src_layer: str | None = Field(
        default=None, description="Source file layer for importing attributes"
    )
    attrib_src_key: str = Field(
        default="lake_id", description="Source file key to match when importing attributes"
    )
    fields: list[str] = Field(
        default=[
            "lake_id",
            "res_id",
            "LkArea",
            "LkMxE",
            "WeirC",
            "WeirL",
            "WeirE",
            "OrificeC",
            "OrificeA",
            "OrificeE",
            "Dam_Length",
            "ifd",
            "reservoir_index_AnA",
            "reservoir_index_Extended_AnA",
            "reservoir_index_GDL_AK",
            "reservoir_index_Medium_Range",
            "reservoir_index_Short_Range",
        ],
        description="Fields to retain from NWM lakes data. IDs and geometry will be kept by default.",
    )


class RefWaterbodyInput(BaseModel):
    """Lakes: Reference Waterbodies is a polygon dataset with COMID. Reference waterbodies will be used for Adhoc Lakes that are only found in Reference Waterbodies. The entire Reference Waterbodies file is not run."""

    path: Path = Field(
        default=Path("input/reference_waterbodies.gpkg"),
        description="Source path. LakesConfig will inject preceding input path.",
    )
    run: bool = Field(
        default=True,
        description="Flag to run Reference Waterbodies input. Must be set to false if file is not present.",
    )
    fp_associated_path: Path = Field(
        default=Path("input/refwb_tmp.gpkg"),
        description="A temporary layer to read waterbodies from where flowpaths have been associated and attributes joined. Skips flowpath association.",
    )
    associate_flowpaths: bool = Field(default=True, description="Flag to run flowpath association")
    flowpath_association_method: str = Field(
        default="polygon_outlet",
        description="Type of flowpath association. Options are `polygon_outlet` or `nearest_point`",
    )
    search_radius_m: float = Field(
        default=1000.0, description="Distance from flowpath to search when associating flowpaths."
    )
    id_field: str = Field(default="comid", description="ID field for reference waterbodies")
    output_id_field: str = Field(
        default="lake_id", description="ID field to change name to for reference waterbodies"
    )
    intersection_length_min_m: float = Field(
        default=3.0,
        description="Minimum prefered intersection length when associating polygons with flowpaths. If the flowpath intersection is extremely short, it can sometimes be almost entirely on a long downstream flowpath.",
    )
    attrib_src_path: Path | None = Field(
        default=None,
        description="Source file for joining attributes from another file. It will be skipped if null.",
    )
    attrib_src_layer: str | None = Field(
        default=None, description="Source file layer for importing attributes"
    )
    attrib_src_key: str = Field(
        default=None, description="Source file key to match when importing attributes"
    )


class AdhocLakeInput(BaseModel):
    """Lakes: Adhoc lakes have been mapped to COMID, site_no (gage), and dam_id (reference reservoirs) when possible. Adhoc lakes that are only in reference waterbodies are flagged to force inclusion."""

    path: Path = Field(
        default=Path("input/adhoc_lakes.gpkg"),
        description="Source path. LakesConfig will inject preceding input path.",
    )
    layer: str = "adhoc_lakes"
    run: bool = Field(
        default=True, description="Flag to run Adhoc Lake input. Must be set to false if file is not present."
    )
    ref_wb_field: str = Field(
        default="ref_waterbodies_only",
        description="Field in the adhoc lakes table that flags if a lake is only in the reference waterbodies dataset (not in NWM lakes)",
    )


class USBRLakeInput(BaseModel):
    """Lakes: USBR lakes are mapped to COMID/lake_id. Add USBR lakes from reference reservoirs when not included in nwm lakes."""

    path: Path = Field(
        default=Path("input/usbr_lake_crosswalk.gpkg"),
        description="Source path. LakesConfig will inject preceding input path.",
    )
    layer: str = "usbr_lake_crosswalk"
    run: bool = Field(
        default=True, description="Flag to run USBR Lake input. Must be set to false if file is not present."
    )
    ref_wb_field: str = Field(
        default="ref_wb_lake",
        description="Field in the USBR table that flags if a lake is only in the reference waterbodies dataset (not in NWM lakes)",
    )


class GreatLake(BaseModel):
    """Defines parameters about a Great Lake"""

    lake_id: str = Field(description="lake_id/NHD 2.2 COMID for Great Lake")
    fp_id: float = Field(description="NHF flowpath ID for Great Lake")
    virtual_fp_id: float = Field(description="NHF virtual flowpath ID for Great Lake")
    site_no: str = Field(description="Gage ID / site_no for Great Lake")
    lat: float | None = Field(default=None, description="Add a manual latitude if needed to place gage")
    lon: float | None = Field(default=None, description="Add a manual longitude if needed to place gage")
    data_source: str | None = Field(default=None, description="Data source of gage")


class GreatLakesMapping(BaseModel):
    """All Great Lakes mappings"""

    superior: GreatLake = GreatLake(
        lake_id="4800002",
        fp_id=1278348162056612,
        virtual_fp_id=1278346877373953,
        site_no="04127885",
        data_source="USGS",
    )
    mi_huron: GreatLake = GreatLake(
        lake_id="4800004",
        fp_id=1276364270499315,
        virtual_fp_id=1276364270423160,
        site_no="04159130",
        data_source="USGS",
    )
    erie: GreatLake = GreatLake(
        lake_id="4800006",
        fp_id=1286192735893685,
        virtual_fp_id=1286154743979494,
        site_no="02HA013",
        lat=42.93028,
        lon=-78.91417,
        data_source="Environment Canada: https://wateroffice.ec.gc.ca/report/real_time_e.html?stn=02HA013",
    )
    ontario: GreatLake = GreatLake(
        lake_id="4800007",
        fp_id=1287248237297035,
        virtual_fp_id=1287248166320950,
        site_no="IJC",
        lat=45.00639,
        lon=-74.79500,
        data_source="International Lake Ontario-St. Lawrence River Board: https://ijc.org/en/loslrb/watershed/outflow-changes",
    )


class LakesConfig(BaseModel):
    """Main Config for Lakes"""

    input_dir: Path = Field(
        default=here() / "data/sconus/lakes",
        description="Input directory. This will be prepended to all paths for other inputs",
    )
    lakes_path: Path = Field(
        default=Path("output/lakes_processed.gpkg"),
        description="Output process lakes file. This can be used to cache a completed lakes run and use in an NHF build.",
    )
    use_cached_lakes: bool = Field(
        default=False,
        description="Flag to use the file stored at `lakes_path` for lakes building. This will skip pipeline run. Note: IDs and flowpath association will not be changed.",
    )
    nwm: NWMLakeInput = Field(
        default=NWMLakeInput(),
        description="All NWM Lakes Input configs. NWM Lakes is a polygon dataset with COMID. This is the operational lakes layer provided by OWP.",
    )
    adhoc: AdhocLakeInput = Field(
        default=AdhocLakeInput(),
        description="All Adhoc Lakes Input configs. Adhoc lakes have been mapped to COMID, site_no (gage), and dam_id (reference reservoirs) when possible.",
    )
    usbr: USBRLakeInput = Field(
        default=USBRLakeInput(),
        description="All USBR Lakes Input configs. USBR lakes have been mapped to COMID when possible. If USBR lakes are missing in NWM lakes but available in reference waterbodies, theey will be added.",
    )
    ref_wb: RefWaterbodyInput = Field(
        default=RefWaterbodyInput(),
        description="All Reference Waterbody Input configs. Reference Waterbodies is a polygon dataset with COMID. Reference waterbodies will be used for Adhoc Lakes that are only found in Reference Waterbodies. The entire Reference Waterbodies file is not run.",
    )
    ref_res: ReferenceReservoirsInput = Field(
        default=ReferenceReservoirsInput(),
        description="All Reference Reservoirs Input configs. Reference Reservoirs is a point dataset of dams including hydraulic parameters from the National Inventory of Dams (NID).",
    )
    dem: LakesDEMInputs = Field(
        default=LakesDEMInputs(), description="DEM configs to use when getting elevations for lakes."
    )
    calculate_elevation: bool = Field(
        default=True,
        description="Flag to calculate elevation. This shold be turned on for production runs but can be turned off for faster debugging.",
    )
    nid: NIDInputs = Field(
        default=NIDInputs(),
        description="All National Inventory of Dams (NID) configs. NID is a point dataset of dam information that is joined to Reference Reservoirs.",
    )
    vfp_lk_crosswalk: bool = Field(
        default=True,
        description="Flag to perform virtual flowpath-lake crosswalk. This crosswalk intersects all flowpaths with a lake polygon or point.",
    )
    fp_id_field: str = Field(
        default="virtual_fp_id",
        description="Name of flowpath ID in dataset from which flowpaths are associated.",
    )
    fp_id_out_field: str = Field(
        default="virtual_fp_id",
        description="Name of flowpath ID field for output after flowpaths are identified",
    )
    output_comid_field: str = Field(
        default="lake_id", description="The common name of 'comid' field that is present in various datasets"
    )
    great_lakes: GreatLakesMapping = Field(default=GreatLakesMapping(), description="Great Lakes parameters")
    validate_duplicates: bool = Field(
        default=True,
        description="Flag to search for duplicate lake points during lakes validation. Defaults to true",
    )
    save_duplicate_gpkgs: bool = Field(
        default=False,
        description="Flag to save the resulting duplicate lake points/polygons as geopackages. Defaults to false",
    )
    fields: list[str] = Field(
        default=[
            "dam_id",
            "nidid",
            "lake_id",
            "res_id",
            "LkArea",
            "LkMxE",
            "WeirC",
            "WeirL",
            "WeirE",
            "OrificeC",
            "OrificeA",
            "OrificeE",
            "Dam_Length",
            "ifd",
            "reservoir_index_AnA",
            "reservoir_index_Extended_AnA",
            "reservoir_index_GDL_AK",
            "reservoir_index_Medium_Range",
            "reservoir_index_Short_Range",
        ],
        description="Final fields. IDs and geometry will be kept by default.",
    )

    @model_validator(mode="after")
    def inject_dirs(self: Any) -> Self:  # type: ignore[misc,type-var]
        """Inject input directories into each input config path"""
        self.lakes_path = self.input_dir / self.lakes_path
        self.nid.path = self.input_dir / self.nid.path
        self.nwm.path = self.input_dir / self.nwm.path
        self.nwm.buffered_path = self.input_dir / self.nwm.buffered_path
        self.nwm.fp_associated_path = self.input_dir / self.nwm.fp_associated_path
        self.nwm.improve_placement_path = self.input_dir / self.nwm.improve_placement_path
        self.ref_wb.path = self.input_dir / self.ref_wb.path
        self.ref_wb.fp_associated_path = self.input_dir / self.ref_wb.fp_associated_path
        self.ref_res.path = self.input_dir / self.ref_res.path
        self.dem.path = self.input_dir / self.dem.path
        self.adhoc.path = self.input_dir / self.adhoc.path
        self.usbr.path = self.input_dir / self.usbr.path

        # optional paths
        self.nwm.attrib_src_path = (
            self.input_dir / self.nwm.attrib_src_path if self.nwm.attrib_src_path else None
        )
        self.ref_wb.attrib_src_path = (
            self.input_dir / self.ref_wb.attrib_src_path if self.ref_wb.attrib_src_path else None
        )

        self.lakes_path.parent.mkdir(parents=True, exist_ok=True)

        return self


# Reservoir DA
class ResCrosswalkFields(BaseModel):
    """Fields in reservoir DA crosswalk file ("reservoir_index_AnA.netcdf).

    These can be changed in config file if needed and will be input read index functions.
    """

    lake_id_field: str = Field("lake_id", description="Lake ID field in Res ANA index file")
    usgs_gage_id_field: str = Field("usgs_gage_id", description="USGS gage ID field in reservoir index file")
    usgs_lake_id_field: str = Field("usgs_lake_id", description="USGS lake ID field in reservoir index file")
    usace_gage_id_field: str = Field(
        "usace_gage_id", description="USACE gage ID field in reservoir index file"
    )
    usace_lake_id_field: str = Field(
        "usace_lake_id", description="USACE lake ID field in reservoir index file"
    )
    rfc_gage_id_field: str = Field("rfc_gage_id", description="RFC gage ID in reservoir index")
    rfc_lake_id_field: str = Field("rfc_lake_id", description="RFC lake ID in reservoir index")


class ResCrossWalkInput(BaseModel):
    """Reservoir crosswalk file ("reservoir_index_AnA.netcdf")"""

    path: Path = Field(
        default=Path("input/reservoir_index_AnA.nc"),
        description="File with reservoir crosswalks for USGS, USACE, RFC",
    )
    fields: ResCrosswalkFields = Field(
        default=ResCrosswalkFields(), description="All field mappings for reservoir index file"
    )


class ActiveRFC(BaseModel):
    """Describes table of active NWS gages for reservoir DA. This is optionally used in gages.

    Retrieved from https://water.noaa.gov/about/data-and-web-services-catalog on 6/15/26
    """

    path: Path = Field(default=Path("input/nwps_all_gauges_report.csv"))
    id_field: str = Field(default="nws shef id")


class ResDAMapping(BaseModel):
    """Mapping of reservoir DA types to integer code"""

    level_pool: int = 1
    usgs_persistence: int = 2
    usace_persistence: int = 3
    usbr_persistence: int = 7
    rfc_forecast: int = 4
    great_lakes: int = 6


class AdhocResDAInput(BaseModel):
    """Adhoc lakes input for reservoir DA. Must include lake_id and an rfc_field with the name of RFC gage."""

    path: Path = Field(
        default=Path("input/adhoc_lakes.gpkg"),
        description="Source path. ResDAConfig will inject preceding input path.",
    )
    layer: str = Field(default="adhoc_lakes", description="Layer in adhoc lakes gpkg")
    run: bool = Field(
        default=False,
        description="Flag to use Adhoc Lake input. Must be set to false if file is not present.",
    )
    rfc_field: str = Field(default="locationId", description="Field containing RFC gage ID")
    lake_id_field: str = Field(default="lake_id", description="Field containing common lake COMID")
    null_value: int = Field(default=-99999, description="Missing data value")


class USACEResDAInput(BaseModel):
    """USACE : lake_id crosswalk for reservoir DA."""

    path: Path = Field(
        default=Path("input/usace_crosswalk.gpkg"),
        description="Source path. ResDAConfig will inject preceding input path.",
    )
    run: bool = Field(
        default=False,
        description="Flag to use USACE reservoir input. Must be set to false if file is not present.",
    )
    lake_id_field: str = Field(default="lake_id", description="Field containing common lake COMID")
    id_field: str = Field(default="location", description="Field containing shared reservoir/gage ID.")


class USBRResDAInput(BaseModel):
    """USBR : lake_id crosswalk for reservoir DA."""

    path: Path = Field(
        default=Path("input/usbr_lake_crosswalk.gpkg"),
        description="Source path. ResDAConfig will inject preceding input path.",
    )
    run: bool = Field(
        default=False,
        description="Flag to use USBR reservoir input. Must be set to false if file is not present.",
    )
    lake_id_field: str = Field(default="lake_id", description="Field containing common lake COMID")
    id_field: str = Field(default="locId", description="Field containing shared reservoir/gage ID.")


class ResDAConfig(BaseModel):
    """Configuration for reservoir DA"""

    input_dir: Path = Field(
        default=here() / "data/lakes",
        description="Input directory. This will be prepended to all paths for other inputs",
    )
    all_level_pool: bool = Field(
        default=False,
        description="Flag to make all reservoirs level pool. This can be used in domains with no lake-gage crosswalk available.",
    )
    adhoc: AdhocResDAInput = Field(
        default=AdhocResDAInput(),
        description="All Adhoc Lakes Input configs. Adhoc lakes have been mapped to COMID/lake_id, site_no (gage), and dam_id (reference reservoirs) when possible.",
    )
    lake_id_field: str = Field(
        default="lake_id", description="The common name of 'comid' field that is present in various datasets"
    )
    gage_id_field: str = Field(default="site_no", description="Name for output gage ID field")
    da_type_field: str = Field(default="da_type", description="Name for output reservoir DA type field")
    res_crosswalk: ResCrossWalkInput = Field(
        default=ResCrossWalkInput(), description="Data for the gage-lake crosswalk"
    )
    great_lakes: bool = Field(
        default=False, description="Flag to add the Great Lakes mappings to the dataframe"
    )

    generate_additional_crosswalk: bool = Field(
        default=False,
        description="Flag to generate lake:gage crosswalks for lakes without RFC or gage information.",
    )
    active_rfc: ActiveRFC = Field(
        default=ActiveRFC(), description="Table of active NWS gages used to filter NWM reservoir index."
    )
    usace: USACEResDAInput = Field(
        default=USACEResDAInput(), description="Crosswalked table of USACE reservoir/gages to lake_id."
    )
    usbr: USBRResDAInput = Field(
        default=USBRResDAInput(), description="Crosswalked table of USBR reservoir/gages to lake_id."
    )
    usgs_fix_list: list[str] | None = Field(
        default=None,
        description="List of USGS site_no in the reservoir index that are missing a leading 0. These values will have 0 prepended during the pipeline.",
    )

    @model_validator(mode="after")
    def inject_dirs(self: Any) -> Self:  # type: ignore[misc,type-var]
        """Inject input directories into each input config path"""
        self.adhoc.path = self.input_dir / self.adhoc.path
        self.res_crosswalk.path = self.input_dir / self.res_crosswalk.path
        self.active_rfc.path = self.input_dir / self.active_rfc.path
        self.usace.path = self.input_dir / self.usace.path
        self.usbr.path = self.input_dir / self.usbr.path
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


class ValidateHFConfig(BaseModel):
    """config class for the hf_validate block in the YAML"""

    calibration_gages_path: Path = Path("data/gages/validation/calibratable_gages.csv")
    routelink_gages_path: Path | None = None


class NWMDefaultHydraulics(Enum):
    """Default values for NWM hydraulic params"""

    WeirC = 0.4
    WeirL = 10.0  # m
    OrificeC = 0.1
    OrificeA = 1.0  # m²
    ifd = 0.899


class GroundWaterProjectionCONUS(Enum):
    """CRS, origin (top, left corner), and size of CONUS NWM grids for groundwater; source:  Fulldom_CONUS_FullRouting.nc"""

    PROJ4 = "+proj=lcc +lat_1=30 +lat_2=60 +lat_0=40.0000076293945 +lon_0=-97 +x_0=0 +y_0=0 +a=6370000 +b=6370000 +units=m +no_defs"
    X_ORIGIN = -2303874.17655
    Y_ORIGIN = -1919874.66329
    WIDTH = 18432
    HEIGHT = 15360
    DX = 250
    DY = 250


class GroundWaterProjectionAK(Enum):
    """CRS, origin (top, left corner), and size of NWM grids for groundwater; source:  Fulldom_AK_FullRouting.nc"""

    PROJ4 = "+proj=stere +lat_0=90 +lat_ts=60 +lon_0=-135 +x_0=0 +y_0=0 +R=6370000 +units=m +no_defs"
    X_ORIGIN = -1130764.7202253528
    Y_ORIGIN = -3163389.53353531
    WIDTH = 3516
    HEIGHT = 1816
    DX = 250
    DY = 250


class GroundWaterProjectionHI(Enum):
    """CRS, origin (top, left corner), and size of NWM grids for groundwater; source:  Fulldom_HI_FullRouting.nc"""

    PROJ4 = "+proj=lcc +units=m +a=6370000.0 +b=6370000.0 +lat_1=10.0 +lat_2=30.0 +lat_0=20.6 +lon_0=-157.42 +x_0=0 +y_0=0 +k_0=1.0 +nadgrids=@null +wktext +no_defs"
    X_ORIGIN = -294950.07097397465
    Y_ORIGIN = -194949.36969098
    WIDTH = 5900
    HEIGHT = 3900
    DX = 100
    DY = 100


class GroundWaterProjectionPRVI(Enum):
    """CRS, origin (top, left corner), and size of NWM grids for groundwater; source:  Fulldom_PRVI_FullRouting.nc"""

    PROJ4 = "+proj=lcc +units=m +a=6370000.0 +b=6370000.0 +lat_1=18.1 +lat_2=18.1 +lat_0=18.1 +lon_0=-65.91 +x_0=0 +y_0=0 +k_0=1.0 +nadgrids=@null +wktext  +no_defs"
    X_ORIGIN = -149949.83
    Y_ORIGIN = -54948.968
    WIDTH = 3000
    HEIGHT = 1100
    DX = 100
    DY = 100
