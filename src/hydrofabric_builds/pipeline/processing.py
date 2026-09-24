"""Contains all code for processing hydrofabric data"""

import logging
from collections.abc import Callable
from typing import Any, cast

import geopandas as gpd
import openlocationcode.openlocationcode as olc
import pandas as pd
from tqdm import tqdm

from hydrofabric_builds.config import HFConfig
from hydrofabric_builds.hydrofabric.aggregate import _aggregate_geometries
from hydrofabric_builds.hydrofabric.build import _build_hydrofabric
from hydrofabric_builds.hydrofabric.trace import _trace_stack
from hydrofabric_builds.hydrofabric.utils import (
    _check_network_cycles,
    _combine_hydrofabrics,
)
from hydrofabric_builds.schemas.hydrofabric import Aggregations, Classifications
from hydrofabric_builds.task_instance import TaskInstance

logger = logging.getLogger(__name__)


def _process_single_outlet(
    outlet: str,
    partition_data: dict[str, Any],
    cfg: HFConfig,
) -> dict[str, Any]:
    """Process outlet with pre-partitioned subgraph and data.

    Parameters
    ----------
    outlet : str
        Outlet ID
    partition_data : dict[str, Any]
        Contains:
        - "subgraph": rx.PyDiGraph (minimal, only this outlet)
        - "node_indices": dict (for subgraph)
        - "fp_lookup": dict (flowpath attributes + shapely_geometry)
        - "div_lookup": dict (divide attributes + shapely_geometry)
        - "flowpaths": pl.DataFrame (for fallback operations)
        - "divides": pl.DataFrame (for fallback operations)
    cfg : HFConfig
        Config

    Returns
    -------
    dict[str, Any]
        Dictionary containing outlet, classifications, aggregate_data, and num_features
    """
    filtered_divides = partition_data["divides"]
    valid_divide_ids: set[str] = set(filtered_divides["divide_id"].to_list())

    # Trace with subgraph
    classifications = _trace_stack(
        start_id=outlet,
        div_ids=valid_divide_ids,
        cfg=cfg,
        partition_data=partition_data,
    )

    # Aggregate geometries
    aggregate_data = _aggregate_geometries(
        classifications=classifications,
        partition_data=partition_data,
    )

    return {
        "outlet": outlet,
        "classifications": classifications.model_dump(),
        "aggregate_data": aggregate_data.model_dump(),
        "num_features": len(partition_data["subgraph"].nodes()),
    }


def _build_single_hydrofabric(
    outlet: str,
    outlet_data: dict[str, Any],
    id_offset: int,
    partition_data: dict[str, Any],
    cfg: HFConfig,
) -> dict[str, Any]:
    """Build a single outlet's hydrofabric with pre-partitioned subgraph and data.

    Parameters
    ----------
    outlet : str
        Outlet ID
    outlet_data : dict[str, Any]
        Outlet aggregation data from map phase
    id_offset : int
        Starting ID for this outlet
    partition_data : dict[str, Any]
        Contains:
        - "subgraph": rx.PyDiGraph (minimal, only this outlet)
        - "node_indices": dict (for subgraph)
        - "fp_lookup": dict (flowpath attributes + shapely_geometry)
        - "div_lookup": dict (divide attributes + shapely_geometry)
        - "flowpaths": pl.DataFrame (for fallback operations)
        - "divides": pl.DataFrame (for fallback operations)
    cfg : HFConfig
        Hydrofabric build config

    Returns
    -------
    dict[str, Any]
        Built hydrofabric data for this outlet
    """
    classifications = Classifications(**outlet_data["classifications"])
    aggregate_data = Aggregations(**outlet_data["aggregate_data"])

    hydrofabric = _build_hydrofabric(
        start_id=outlet,
        aggregate_data=aggregate_data,
        classifications=classifications,
        partition_data=partition_data,
        cfg=cfg,
        id_offset=id_offset,
    )

    return {
        "outlet": outlet,
        "flowpaths": hydrofabric["flowpaths"],
        "divides": hydrofabric["divides"],
        "nexus": hydrofabric["nexus"],
        "reference_flowpaths": hydrofabric["reference_flowpaths"],
        "virtual_flowpaths": hydrofabric["virtual_flowpaths"],
        "virtual_nexus": hydrofabric["virtual_nexus"],
        # "reference_virtual_flowpaths": hydrofabric["reference_virtual_flowpaths"],
        "next_available_id": hydrofabric["next_available_id"],
    }


def map_trace_and_aggregate(**context: dict[str, Any]) -> dict[str, Any]:
    """Execute MAP PHASE: Trace and aggregate flowpaths using pre-partitioned subgraphs.

    This task processes each outlet independently using pre-partitioned subgraphs
    and filtered data from the build_graph task. No large DataFrames are broadcasted.

    Parameters
    ----------
    **context : dict[str, Any]
        Airflow context

    Returns
    -------
    dict[str, Any]
        Dictionary with keys:
        - "outlet_aggregations": dict mapping outlet_id -> outlet data
        - "total_outlets": int total number of outlets

    Raises
    ------
    ValueError
        If no outlets found
    """
    ti = cast(TaskInstance, context["ti"])
    cfg = cast(HFConfig, context["config"])

    outlets: list[str] = ti.xcom_pull(task_id="build_graph", key="outlets")
    outlet_subgraphs: dict[str, dict[str, Any]] = ti.xcom_pull(task_id="build_graph", key="outlet_subgraphs")

    if not outlets:
        raise ValueError("No outlets found. Aborting run")

    # Apply debug limit if configured
    outlets_to_process = outlets[: cfg.build.debug_outlet_count] if cfg.build.debug_outlet_count else outlets

    results: list[dict[str, Any]] = []

    logger.info(f"map_flowpaths task: Processing {len(outlets_to_process)} outlets sequentially")
    for outlet in tqdm(outlets_to_process, desc="Processing outlets"):
        result = _process_single_outlet(
            outlet,
            outlet_subgraphs[outlet],
            cfg,
        )
        results.append(result)

    outlet_aggregations: dict[str, dict[str, Any]] = {result["outlet"]: result for result in results}

    return {
        "outlet_aggregations": outlet_aggregations,
        "total_outlets": len(outlets),
    }


def map_build_hydrofabric(**context: dict[str, Any]) -> dict[str, Any]:
    """Execute MAP PHASE: Build base hydrofabric layers with assigned ID ranges.

    Each outlet's classifications and aggregations are converted into
    flowpaths, divides, and nexus layers with unique IDs using pre-partitioned
    subgraphs and filtered data.

    Parameters
    ----------
    **context : dict[str, Any]
        Airflow context

    Returns
    -------
    dict[str, Any]
        Dictionary with keys:
        - "built_hydrofabrics": dict mapping outlet_id -> hydrofabric data

    Raises
    ------
    ValueError
        If required data from previous phases not found
    """
    ti = cast(TaskInstance, context["ti"])
    cfg = cast(HFConfig, context["config"])

    outlet_subgraphs: dict[str, dict[str, Any]] = ti.xcom_pull(task_id="build_graph", key="outlet_subgraphs")
    outlet_aggregations: dict[str, dict[str, Any]] = ti.xcom_pull(
        task_id="map_flowpaths", key="outlet_aggregations"
    )
    results: list[dict[str, Any]] = []

    if not outlet_aggregations:
        raise ValueError("Missing outlet aggregations")

    logger.info(f"map_build_base task: Building {len(outlet_aggregations)} hydrofabrics sequentially")
    global_nhf_id = 0
    results = []
    for outlet, outlet_data in tqdm(outlet_aggregations.items(), desc="Building hydrofabrics"):
        result = _build_single_hydrofabric(
            outlet,
            outlet_data,
            global_nhf_id,
            outlet_subgraphs[outlet],
            cfg,
        )
        results.append(result)
        global_nhf_id = result["next_available_id"]

    built_hydrofabrics: dict[str, dict[str, Any]] = {result["outlet"]: result for result in results}

    return {
        "built_hydrofabrics": built_hydrofabrics,
    }


_OLC_CHARS = {
    "2": 0,
    "3": 1,
    "4": 2,
    "5": 3,
    "6": 4,
    "7": 5,
    "8": 6,
    "9": 7,
    "C": 8,
    "F": 9,
    "G": 10,
    "H": 11,
    "J": 12,
    "M": 13,
    "P": 14,
    "Q": 15,
    "R": 16,
    "V": 17,
    "W": 18,
    "X": 19,
}

_OLC_CODE_LENGTH = 12


def _olc_to_int(code: str) -> int:
    code_int: int = 0
    for c in code:
        if c in _OLC_CHARS:
            code_int *= 20
            code_int += _OLC_CHARS[c]

    return code_int


def _encode_unique(lat: float, lon: float, code_length: int, used_ints: set[int]) -> tuple[str, int]:
    """Encode lat/lon to OLC, jittering to avoid collisions with used_ints."""
    geo_id = olc.encode(lat, lon, codeLength=code_length)
    olc_int = _olc_to_int(geo_id)
    if olc_int not in used_ints:
        return geo_id, olc_int

    # Jitter: try 1m offsets in spiral pattern until unique
    d = 9e-6  # ~1m in degrees
    # Spiral outward: N, NE, E, SE, S, SW, W, NW, then 2m ring, etc.
    jitter_dirs = [(0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1), (-1, 0), (-1, 1)]
    for ring in range(1, 21):  # up to ~20m
        for dlat, dlon in jitter_dirs:
            new_lat = lat + dlat * ring * d
            new_lon = lon + dlon * ring * d
            new_geo_id = olc.encode(new_lat, new_lon, codeLength=code_length)
            new_olc_int = _olc_to_int(new_geo_id)
            if new_olc_int not in used_ints:
                return new_geo_id, new_olc_int

    raise ValueError(f"Could not find unique OLC for ({lat:.6f}, {lon:.6f})")


def _build_olc_map(
    gdf: gpd.GeoDataFrame,
    id_col: str,
    point_getter: Callable,
    used_ints: set[int],
) -> dict[int, tuple[str, int]]:
    """Build {old_id: (olc_str, olc_int)} map from a GeoDataFrame.

    Parameters
    ----------
    gdf : gpd.GeoDataFrame
        Features in EPSG:4326 to encode.
    id_col : str
        Column name to use as map key.
    point_getter : callable
        Function taking a geometry and returning a Point (e.g. lambda g: g.centroid).
    used_ints : set[int]
        Set of already-assigned OLC ints to avoid collisions.

    Returns
    -------
    dict
        Mapping of original ID to (olc_string, olc_int).
    """
    result: dict[int, tuple[str, int]] = {}
    for _, row in gdf.iterrows():
        pt = point_getter(row["geometry"])
        geo_id, olc_int = _encode_unique(pt.y, pt.x, _OLC_CODE_LENGTH, used_ints)
        used_ints.add(olc_int)
        result[int(row[id_col])] = (geo_id, olc_int)
    return result


def _bulk_replace(
    df: pd.DataFrame,
    cols: list[str],
    replace_map: dict[int, tuple[str, int]],
) -> pd.DataFrame:
    """Replace several columns in a DataFrame by looking their values up in a dict.

    Parameters
    ----------
    df: DataFrame/GeoDataFrame whose columns will be transformed
    cols: list of columns to replace
    replace_map: dict for lookup-based transformation

    Returns
    -------
    The transformed DataFrame/GeoDataFrame
    """
    for col in cols:
        df[col] = df[col].apply(lambda e: None if pd.isna(e) else replace_map[int(e)][1])
        df[col] = df[col].astype("Int64") if df[col].isnull().values.any() else df[col].astype("Int64")

    return df


def reassign_ids(
    base_hf: dict[str, (gpd.GeoDataFrame | pd.DataFrame)],
) -> dict[str, (gpd.GeoDataFrame | pd.DataFrame)]:
    """Reassign fp_ids based on spatial sorting"""
    flowpaths = base_hf["flowpaths"]
    flowpaths_wgs84 = gpd.GeoDataFrame(flowpaths.copy()).to_crs("EPSG:4326")
    nexuses = base_hf["nexus"]
    nexuses_wgs84 = gpd.GeoDataFrame(nexuses.copy()).to_crs("EPSG:4326")
    divides = base_hf["divides"]

    used_ints: set[int] = set()
    fp_map = _build_olc_map(
        flowpaths_wgs84, "fp_id", lambda g: g.interpolate(0.5, normalized=True), used_ints
    )
    nex_map = _build_olc_map(nexuses_wgs84, "nex_id", lambda g: g.centroid, used_ints)

    # Add gid (OLC string) columns before replacing IDs
    flowpaths["gid"] = flowpaths["fp_id"].copy().apply(lambda e: None if pd.isna(e) else fp_map[int(e)][0])
    nexuses["gid"] = nexuses["nex_id"].copy().apply(lambda e: None if pd.isna(e) else nex_map[int(e)][0])
    divides["gid"] = divides["div_id"].copy().apply(lambda e: None if pd.isna(e) else fp_map[int(e)][0])

    flowpaths = _bulk_replace(flowpaths, ["fp_id", "fp_to_id", "div_id"], fp_map)
    flowpaths = _bulk_replace(flowpaths, ["up_nex_id", "dn_nex_id"], nex_map)

    nexuses = _bulk_replace(nexuses, ["dn_fp_id"], fp_map)
    nexuses = _bulk_replace(nexuses, ["nex_id"], nex_map)

    divides = _bulk_replace(divides, ["div_id"], fp_map)

    base_hf["flowpaths"] = flowpaths
    base_hf["nexus"] = nexuses
    base_hf["divides"] = divides

    virt_flowpaths = base_hf["virtual_flowpaths"]
    virt_nexuses = base_hf["virtual_nexus"]

    virt_fp_map = _build_olc_map(
        virt_flowpaths.copy().to_crs("EPSG:4326"),
        "virtual_fp_id",
        lambda g: g.interpolate(0.5, normalized=True),
        used_ints,
    )
    virt_nex_map = _build_olc_map(
        virt_nexuses.copy().to_crs("EPSG:4326"),
        "virtual_nex_id",
        lambda g: g.centroid,
        used_ints,
    )

    # Add gid (OLC string) columns for virtual tables
    virt_flowpaths["gid"] = (
        virt_flowpaths["virtual_fp_id"].copy().apply(lambda e: None if pd.isna(e) else virt_fp_map[int(e)][0])
    )
    virt_nexuses["gid"] = (
        virt_nexuses["virtual_nex_id"].copy().apply(lambda e: None if pd.isna(e) else virt_nex_map[int(e)][0])
    )

    base_hf["virtual_flowpaths"] = _bulk_replace(virt_flowpaths, ["virtual_fp_id"], virt_fp_map)
    base_hf["virtual_flowpaths"] = _bulk_replace(
        base_hf["virtual_flowpaths"], ["up_virtual_nex_id", "dn_virtual_nex_id"], virt_nex_map
    )
    base_hf["virtual_nexus"] = _bulk_replace(virt_nexuses, ["dn_virtual_fp_id"], virt_fp_map)
    base_hf["virtual_nexus"] = _bulk_replace(base_hf["virtual_nexus"], ["virtual_nex_id"], virt_nex_map)

    # Process reference_flowpaths: remap ID columns to OLC integers
    reference_fps = base_hf.get("reference_flowpaths")
    if reference_fps is not None and len(reference_fps) > 0:
        # Add gid BEFORE replacing IDs (gid uses the fp_map lookup with old IDs)
        reference_fps["gid"] = (
            reference_fps["fp_id"].copy().apply(lambda e: None if pd.isna(e) else fp_map[int(e)][0])
        )
        reference_fps = _bulk_replace(reference_fps, ["fp_id", "div_id"], fp_map)
        reference_fps = _bulk_replace(reference_fps, ["virtual_fp_id", "mainstem_virtual_fp_id"], virt_fp_map)
        base_hf["reference_flowpaths"] = reference_fps

    return base_hf


def reduce_combine_base_hydrofabric(**context: dict[str, Any]) -> dict[str, Any]:
    """Execute REDUCE PHASE: Combine all built hydrofabric layers into an aggregated dataset.

    All outlet hydrofabrics are concatenated into single unified layers
    for flowpaths, divides, and nexus points.

    Parameters
    ----------
    **context : dict[str, Any]
        Airflow context

    Returns
    -------
    dict[str, Any]
        Dictionary with keys:
        - "flowpaths": GeoDataFrame of combined flowpaths
        - "divides": GeoDataFrame of combined divides
        - "nexus": GeoDataFrame of combined nexus points

    Raises
    ------
    ValueError
        If no built hydrofabrics found from build phase
    """
    ti = cast(TaskInstance, context["ti"])
    cfg = cast(HFConfig, context["config"])
    built_hydrofabrics: dict[str, dict[str, Any]] = ti.xcom_pull(
        task_id="map_build_base", key="built_hydrofabrics"
    )

    if not built_hydrofabrics:
        raise ValueError("No built hydrofabrics found from build phase")

    result = _combine_hydrofabrics(built_hydrofabrics, cfg.crs)

    result = reassign_ids(result)

    _check_network_cycles(
        fp_gdf=result["flowpaths"],
        nex_gdf=result["nexus"],
        vfp_gdf=result.get("virtual_flowpaths"),
        vnex_gdf=result.get("virtual_nexus"),
    )

    return result
