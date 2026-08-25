from __future__ import annotations

import concurrent.futures as cf
import logging
import os
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

# logging.getLogger().setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# ---------------------------
# Helpers for distributing tasks
# ---------------------------


def _prepare_tasks(
    gages: gpd.GeoDataFrame,
    flowpaths_path: Path,
    flowpaths_layer: str,
    flow_id_col: str,
    buffer_m: float | int,
    work_crs: str,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame, pd.DataFrame]:
    """
    Preparing the tasks for the run

    Returns
    -------
      out (copy of gages in EPSG:4326 with result cols),
      fwd (flowpaths in WORK_CRS),
      gages_w (gages in WORK_CRS),
      hits DataFrame with columns: __gage_row__, __fp_row__, total_da_sqkm
    :param gages: gages geodataframe
    :param flowpaths_path: hydrofabric gpkg
    :param flowpaths_layer: laayer name in hydrofabric for flowpaths
    :param buffer_m: buffer for gages points to find flowpaths with intersections
    :param work_crs: area-equal crs
    :return: gages:  with additional columns,
            fwd: flowpath geoFataFrame,
            gages_wgages in work crs,
            hits: flowpath intersections
    """
    # Flowpaths → WORK_CRS
    flowpaths = (
        gpd.read_parquet(flowpaths_path)
        if "parquet" in flowpaths_path.name
        else gpd.read_file(flowpaths_path, layer=flowpaths_layer)
    )
    if not flowpaths.crs:
        raise ValueError("flowpaths GeoDataFrame must have a valid CRS.")
    to_work = work_crs if flowpaths.crs.to_string().upper() != work_crs else flowpaths.crs
    fwd = flowpaths.to_crs(to_work).copy()

    # Gages → keep EPSG:4326 copy (out) and a WORK_CRS copy for buffering & distances
    if gages.crs is None:
        raise ValueError("`gages` must have a CRS (expected EPSG:4326).")
    g4326 = gages if gages.crs.to_string().upper() == "EPSG:4326" else gages.to_crs("EPSG:4326")
    gages_w = g4326.to_crs(to_work).copy()
    gages_w["__gid__"] = np.arange(len(gages_w))

    # buffer & join (vectorized)
    gages_buf = gages_w.copy()
    gages_buf.set_geometry(gages_w.geometry.buffer(float(buffer_m)), inplace=True)
    hits_gdf = gpd.sjoin(
        fwd.reset_index(names="__fp_row__"),
        gages_buf[["__gid__", "geometry"]],
        predicate="intersects",
        how="inner",
    ).rename(columns={"__gid__": "__gage_row__"})

    # slim hits
    if "total_da_sqkm" not in hits_gdf.columns:
        try:
            hits_gdf = hits_gdf.rename(columns={"totdasqkm": "total_da_sqkm"})
        except Exception as e:
            raise ValueError("'total_da_sqkm' missing from flowpaths; adjust code/column.") from e
    hits = hits_gdf[["__gage_row__", "__fp_row__", "total_da_sqkm"]].copy()

    # Output frame gets result columns (do NOT overwrite pre-attached USGS_basin_km2)
    out = g4326.copy()
    if flow_id_col not in out.columns:
        out[flow_id_col] = None
    if "method_fp_to_gage" not in out.columns:
        out["method_fp_to_gage"] = None

    return out, fwd, gages_w, hits


def _vectorized_pick_by_area(
    hits: pd.DataFrame,  # cols: __gage_row__, __fp_row__, total_da_sqkm
    target_area: pd.Series,  # gages['USGS_basin_km2']; index aligns to gage rows
    tol: float,  # relative tolerance, e.g., 0.15
) -> pd.Series:
    """
    For each gage row, choose fp with min relative error within tol.

    Returns Series 'best_fp_row' indexed by gage row, values = chosen __fp_row__ or NaN.
    :param hits: flowpath intersections
    :param target_area: area pandas series from gages column
    :param tol: tolerance, comes from rel_err in config file
    :return: pandas series with rows's number for matched gages and flowpaths using USGS API area comparison
    """
    df = hits.join(target_area.rename("__target__"), on="__gage_row__")
    valid = df["__target__"].notna() & (df["__target__"] > 0)
    df = df[valid].copy()
    if df.empty:
        return pd.Series(index=target_area.index, dtype="float64")

    df["__rel_err__"] = (df["total_da_sqkm"] - df["__target__"]).abs() / df["__target__"]
    df = df[df["__rel_err__"] <= tol]
    if df.empty:
        return pd.Series(index=target_area.index, dtype="float64")

    winners = (
        df.sort_values(["__gage_row__", "__rel_err__", "__fp_row__"], kind="mergesort")
        .drop_duplicates(subset="__gage_row__", keep="first")
        .loc[:, ["__gage_row__", "__fp_row__"]]
    )

    # build full-length output series; assign only winners
    # create output with nullable integer dtype
    out = pd.Series(pd.NA, index=target_area.index, dtype="Int64")

    # assign winners (also cast to nullable int to match)
    out.loc[winners["__gage_row__"].to_numpy()] = winners["__fp_row__"].astype("Int64").to_numpy()
    return out


def _vectorized_nearest_fallback(
    unresolved_rows: np.ndarray,  # gage row ids needing fallback
    fwd: gpd.GeoDataFrame,  # flowpaths in WORK_CRS, has geometry + fp_id
    gages_w: gpd.GeoDataFrame,  # gages in WORK_CRS, has geometry
    hits: pd.DataFrame,  # __gage_row__, __fp_row__
    flow_id_col: str,
) -> pd.Series:
    """
    For each unresolved gage row, pick fp with smallest distance to the gage point.

    Returns Series indexed by gage row, values = chosen fp_id (string) or NaN.
    :param unresolved_rows: rows that were not found in USGS API, and are NaN
    :param fwd: flowpaths geoFataFrame
    :param gages_w: gages geoFataFrame in work crs
    :param hits: flowpath intersections
    :param flow_id_col: column name of flowpaths
    :return: pd series with gages rows as indices and fwd rows as column
    """
    if len(unresolved_rows) == 0:
        return pd.Series(dtype=object)

    hits_sub = hits[hits["__gage_row__"].isin(unresolved_rows)].copy()
    if hits_sub.empty:
        return pd.Series(index=unresolved_rows, dtype=object)

    fp_geoms = fwd.geometry.values
    g_geoms = gages_w.geometry.values

    ## here calculating the distance between gages and candidate flowpaths and selecting the minimum distance
    out: dict[int, str] = {}
    for g_row, sub in hits_sub.groupby("__gage_row__"):
        g_pt = g_geoms[g_row]
        cand_rows = sub["__fp_row__"].to_numpy()
        if cand_rows.size == 0:
            continue
        cand_geoms = fp_geoms[cand_rows]
        dists = np.fromiter((g_pt.distance(geom) for geom in cand_geoms), dtype=float, count=len(cand_geoms))
        best_fp_row = int(cand_rows[int(dists.argmin())])
        out[g_row] = str(fwd.iloc[best_fp_row][flow_id_col])

    return pd.Series(out, dtype=object)


def _assign_batch_vectorized(
    out: gpd.GeoDataFrame,
    fwd: gpd.GeoDataFrame,
    gages_w: gpd.GeoDataFrame,
    hits: pd.DataFrame,
    flow_id_col: str,
    tol: float = 0.15,
) -> gpd.GeoDataFrame:
    """
    Use pre-attached out['USGS_basin_km2'] to pick by area, then nearest fallback.

    :param out: gages geodataframe
    :param fwd: flowpaths geoFataFrame
    :param gages_w: gages geoFataFrame in work crs
    :param hits: flowpath intersections
    :param tol: tolerance for nearest fallback
    :return: gages geoFataFrame
    """
    if "USGS_basin_km2" not in out.columns:
        raise ValueError("`out` (gages) must already have 'USGS_basin_km2' attached.")

    targets = out["USGS_basin_km2"]  # may contain NaNs
    best_fp_row = _vectorized_pick_by_area(hits, targets, tol=tol)

    # Assign where area match succeeded
    mask_have = best_fp_row.notna()
    if mask_have.any():
        chosen_rows = best_fp_row[mask_have].astype(int).values
        chosen_ids = fwd.iloc[chosen_rows][flow_id_col].astype(str).values
        out.loc[mask_have, flow_id_col] = chosen_ids
        out.loc[mask_have, "method_fp_to_gage"] = "nldi_area"

    # Nearest fallback for unresolved gages
    unresolved = out.index[out[flow_id_col].isna()].to_numpy()
    if unresolved.size:
        nearest_ids = _vectorized_nearest_fallback(unresolved, fwd, gages_w, hits, flow_id_col=flow_id_col)
        mask_near = nearest_ids.notna()
        if mask_near.any():
            out.loc[nearest_ids.index[mask_near], flow_id_col] = nearest_ids[mask_near].astype(str).values
            out.loc[nearest_ids.index[mask_near], "method_fp_to_gage"] = "nearest_fp"

    out = out.rename(columns={flow_id_col: "ref_fp_id"})
    return out


def _crosswalk_fp_id(gdf_gages: gpd.GeoDataFrame, hf_path: Path) -> gpd.GeoDataFrame:
    """Cross walk gages from ref flowpaths to NHF flowpaths

    Parameters
    ----------
    gdf_gages : gpd.GeoDataFrame
        gages
    hf_path : Path
        hydrofabric

    Returns
    -------
    gpd.GeoDataFrame
        gages with fp_id and virtual_fp_id
    """
    hf_ref = gpd.read_file(hf_path, layer="reference_flowpaths")
    gdf_gages = gdf_gages.merge(hf_ref[["ref_fp_id", "fp_id", "virtual_fp_id"]], on="ref_fp_id", how="left")
    gdf_gages = gdf_gages.loc[(~gdf_gages["fp_id"].isnull() | ~gdf_gages["virtual_fp_id"].isnull()), :].copy()
    del hf_ref
    return gdf_gages


# ---------------------------
# Public entry point
# ---------------------------
def run_assignment(
    gages: gpd.GeoDataFrame,
    flowpaths_path: Path,
    flowpaths_layer: str = "reference_flowpaths",
    flow_id_col: str = "flowpath_id",
    buffer_m: float | int = 500.0,
    work_crs: str = "EPSG:5070",
    parallel: bool = False,
    max_workers: int | None = None,
    tol: float = 0.15,
) -> gpd.GeoDataFrame:
    """
    Uses pre-attached gages['USGS_basin_km2'].

    Steps:
      1) spatial join to get candidates per gage,
      2) vectorized best-by-area within tol,
      3) vectorized nearest fallback for the rest.

    If parallel=True, splits gages into chunks and runs the same vectorized pipeline per chunk in processes.
    :param gages: gages geoFataFrame
    :param flowpaths_path: path to flowpaths geoFataFrame
    :param flowpaths_layer: layer to use for flowpaths
    :param buffer_m: buffer m
    :param work_crs: work crs
    :param parallel: use parallel processing
    :param max_workers: maximum number of workers
    :param tol: tolerance for comparing USGS API area and total_da_sqkm
    :return: gages geoFataFrame
    """
    out, fwd, gages_w, hits = _prepare_tasks(
        gages, flowpaths_path, flowpaths_layer, flow_id_col, buffer_m, work_crs
    )

    if not parallel:
        return _assign_batch_vectorized(out, fwd, gages_w, hits, flow_id_col, tol=tol)

    # parallel: chunk gages, shared hits accordingly
    nproc = max_workers or max(1, os.cpu_count() or 1)
    idx_chunks = np.array_split(out.index.values, nproc)

    def _one_chunk(idx_subset: np.ndarray) -> gpd.GeoDataFrame:
        out_sub = out.loc[idx_subset].copy()
        gages_w_sub = gages_w.loc[idx_subset].copy()
        hits_sub = hits[hits["__gage_row__"].isin(idx_subset)].copy()

        # remap gage rows to 0..k-1 for local vector ops
        remap = {old: i for i, old in enumerate(out_sub.index)}
        out_sub.index = np.arange(len(out_sub))
        gages_w_sub.index = out_sub.index
        hits_sub["__gage_row__"] = hits_sub["__gage_row__"].map(remap)

        res = _assign_batch_vectorized(out_sub, fwd, gages_w_sub, hits_sub, flow_id_col, tol=tol)
        # map back
        res.index = np.array(list(remap.keys()))
        return res

    with cf.ProcessPoolExecutor(max_workers=nproc) as ex:
        parts = list(ex.map(_one_chunk, idx_chunks))

    result = pd.concat(parts).loc[out.index]
    return gpd.GeoDataFrame(result, geometry="geometry", crs=gages.crs)
