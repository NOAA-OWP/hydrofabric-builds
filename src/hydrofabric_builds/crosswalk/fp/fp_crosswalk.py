from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry.base import BaseGeometry
from tqdm import tqdm

from hydrofabric_builds.hydrofabric.graph import _validate_and_fix_geometries

logger = logging.getLogger(__name__)


def _ensure_projected(gdf: gpd.GeoDataFrame, work_crs: str = "EPSG:5070") -> gpd.GeoDataFrame:
    if gdf.crs is None:
        raise ValueError("GeoDataFrame must have a CRS")
    if str(gdf.crs).upper() == work_crs.upper():
        return gdf
    return gdf.to_crs(work_crs)


def _read_geofile(file_path: str | Path, layer_name: str | None = None) -> gpd.GeoDataFrame:
    file_path = str(file_path)
    if file_path.endswith(".parquet"):
        return gpd.read_parquet(file_path)
    elif file_path.endswith((".gpkg", ".gdb")):
        return gpd.read_file(file_path, layer=layer_name)
    else:
        raise ValueError(f"Unsupported file type: {file_path}")


def _percent_in_buffer(geom: BaseGeometry, buffer: BaseGeometry) -> float:
    if geom.length == 0:
        return 0.0
    return geom.intersection(buffer).length / geom.length


def build_crosswalk(
    reference_flowpaths: gpd.GeoDataFrame,
    nwm_flows: gpd.GeoDataFrame,
    ref_id_col: str,
    nwm_id_col: str,
    search_radius_m: float = 20.0,
    percent_inside_min: float = 0.01,
) -> pd.DataFrame:
    """
    Build crosswalk table mapping reference flowpath IDs to *best* NWM flow IDs.

    For each reference flowpath:
      - buffer by `search_radius_m`,
      - find intersecting NWM segments,
      - compute percent of each NWM segment inside that buffer,
      - keep only the segment with the highest percent_inside,
      - if best percent_inside >= percent_inside_min:
            record its nwm_id
        else:
            record nwm_id = NaN.

    Returns
    -------
    DataFrame with one row per reference flowpath:
        ref_id, nwm_id, percent_inside
    """
    sindex = nwm_flows.sindex
    results: list[dict[str, Any]] = []

    for idx in tqdm(
        reference_flowpaths.index,
        total=len(reference_flowpaths),
        desc="Building crosswalk",
    ):
        ref_id = reference_flowpaths.at[idx, ref_id_col]
        ref_geom = reference_flowpaths.geometry.iloc[reference_flowpaths.index.get_loc(idx)]

        # If no geometry, still append a row with NaNs
        if ref_geom is None or ref_geom.is_empty:
            results.append(
                {
                    "ref_id": ref_id,
                    "nhd_feature_id": np.nan,
                    "percent_inside": np.nan,
                }
            )
            continue

        buffer = ref_geom.buffer(search_radius_m)
        candidate_idx = sindex.query(buffer, predicate="intersects")

        # Track best candidate for this ref_id
        best_nwm_id: Any | None = None
        best_pct: float = 0.0

        for nwm_idx in candidate_idx:
            nwm_geom = nwm_flows.geometry.iloc[nwm_idx]
            nwm_id = nwm_flows.iloc[nwm_idx][nwm_id_col]

            percent_inside = _percent_in_buffer(nwm_geom, buffer)

            if percent_inside > best_pct:
                best_pct = percent_inside
                best_nwm_id = nwm_id

        # If there were no candidates at all:
        if best_nwm_id is None:
            results.append(
                {
                    "ref_id": ref_id,
                    "nhd_feature_id": np.nan,
                    "percent_inside": np.nan,
                }
            )
            continue

        # If best candidate doesn't meet threshold, still keep ref_id,
        # but mark nwm_id as NaN, and keep best_pct for diagnostics.
        if best_pct < percent_inside_min:
            results.append(
                {
                    "ref_id": ref_id,
                    "nhd_feature_id": np.nan,
                    "percent_inside": round(best_pct, 4),
                }
            )
        else:
            results.append(
                {
                    "ref_id": ref_id,
                    "nhd_feature_id": best_nwm_id,
                    "percent_inside": round(best_pct, 4),
                }
            )
    pd_results = pd.DataFrame(results)
    pd_results["ref_id"] = pd_results["ref_id"].astype("Int64")
    pd_results["nhd_feature_id"] = pd_results["nhd_feature_id"].astype("Int64")
    return pd_results


def build_crosswalk_from_files(
    reference_path: str | Path,
    nwm_path: str | Path,
    ref_id_col: str,
    nwm_id_col: str,
    reference_layer: str | None = None,
    nwm_layer: str | None = None,
    work_crs: str = "EPSG:5070",
    search_radius_m: float = 50.0,
    percent_inside_min: float = 0.9,
) -> pd.DataFrame:
    """
    Build crosswalk table from file paths.

    :param reference_path: (product 1) reference flowpath path. the product you want to find the best matching fp from product 2
    :param nwm_path: the path to product 2
    :param ref_id_col: column name of reference flowpath
    :param nwm_id_col: column name of NWM flowpath (product 2)
    :param reference_layer: reference flowpath layer name
    :param nwm_layer: nwm flowpath layer name
    :param work_crs: work CRS in config file
    :param search_radius_m: buffering radius around reference flowpath
    :param percent_inside_min: minimum percentage for considering the flowpath in product 2 be in buffered area in product 1
    :return: crosswalk table
    """
    logger.info(f"fp_crosswalk: Loading reference flowpaths (product 1) from {reference_path}")
    reference_flowpaths = _read_geofile(reference_path, reference_layer)
    reference_flowpaths = _ensure_projected(reference_flowpaths, work_crs)
    reference_flowpaths = _validate_and_fix_geometries(reference_flowpaths, geom_type="flowpaths")

    logger.info(f"fp_crosswalk: Loading NWM flows (product 2) from {nwm_path}")
    nwm_flows = _read_geofile(nwm_path, nwm_layer)
    nwm_flows = _ensure_projected(nwm_flows, work_crs)
    nwm_flows = _validate_and_fix_geometries(nwm_flows, geom_type="flowpaths")

    logger.info(f"Matching {len(nwm_flows)} NWM flows against {len(reference_flowpaths)} reference flowpaths")

    crosswalk = build_crosswalk(
        reference_flowpaths=reference_flowpaths,
        nwm_flows=nwm_flows,
        ref_id_col=ref_id_col,
        nwm_id_col=nwm_id_col,
        search_radius_m=search_radius_m,
        percent_inside_min=percent_inside_min,
    )

    logger.info(f"Built crosswalk with {len(crosswalk)} matches")
    return crosswalk
