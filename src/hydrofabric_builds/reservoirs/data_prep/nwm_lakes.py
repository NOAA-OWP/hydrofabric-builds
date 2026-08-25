import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def prep_nwm_lakes(
    lakes_path: Path,
    lakes_layer: str,
    ref_wb_path: Path | str,
    ref_fp_path: Path | str,
    buffer_size_m: int | float,
    output: Path | str,
) -> None:
    """Prepare NWM lakes to be included in RFCDA file.

    NWM/HF 2.2 lakes are joined to buffered waterbodies to obtain the most downstream flowpath ID via hydrosequence.
    This method keeps the lakes points at their HF 2.2 location.
    There will be dupelicates for a waterbody due to changed position in reference reservoirs.
    Future work will update to remove dupelicates and harmonize locations.

    Parameters
    ----------
    lakes_path : Path
        NWM lakes input - likely a full HF 2.2 gpkg
    lakes_layer : str
        NWM lakes GPKG layer
    ref_wb_path : Path
        reference waterbodies
    ref_fp_path : Path
        reference flowpaths
    buffer_size_m : int | float
        buffer size for waterbodies (meters) This helps with joins to lakes
    output : Path
        output file path - will be used by RFCDA
    """
    gdf_lakes = gpd.read_file(lakes_path, layer=lakes_layer)
    gdf_wb = gpd.read_file(ref_wb_path)
    gdf_ref = gpd.read_parquet(ref_fp_path)

    # buffer waterbodies
    gdf_wb["geometry"] = gdf_wb["geometry"].buffer(buffer_size_m)

    # spatial join intersect waterbodies to lakes
    gdf_lakes = gdf_lakes.sjoin(gdf_wb, how="left")

    # locate comids that are in lakes and join those wb to ref
    gdf_wb = gdf_wb.loc[gdf_wb["comid"].isin(gdf_lakes["comid"].unique()), :].copy()
    gdf_wb = gdf_wb.sjoin(gdf_ref, how="left")[["comid", "flowpath_id", "hydroseq"]]

    # merge waterbodies in again on comid
    gdf_lakes = gdf_lakes.merge(gdf_wb, on="comid", how="left")

    # get minimum hydrosequence per lake
    gdf_lakes["hydroseq"] = pd.to_numeric(gdf_lakes["hydroseq"]).astype(float)
    gdf_min_fp = gdf_lakes[["lake_id", "hydroseq"]].groupby(["lake_id"]).min()
    gdf_lakes = (
        gdf_lakes.merge(gdf_min_fp, on=["lake_id", "hydroseq"], how="inner")
        .drop_duplicates(subset=["lake_id", "hydroseq", "flowpath_id"])
        .rename(columns={"flowpath_id": "ref_fab_fp"})
        .drop(columns=["hydroseq", "index_right"])
    )

    gdf_lakes.to_file(output, layer="lakes", driver="GPKG")
    logger.info(f"Wrote NWM/HF2.2 lakes to {output}")


def find_lake_duplicates(
    lakes_path: str | Path,
    lakes_layer: str,
    ref_wb_path: Path | str,
    ref_res_path: Path | str,
    ref_fp_path: Path | str,
    wb_buffer: int | float,
    lakes_tmp_path: str | Path,
    ref_res_tmp_path: str | Path,
    lakes_keep: list[int] | None = None,
) -> None:
    """Search through NWM lakes and find duplicates with reference reservoirs. Prefer reference reservoirs.

    Outputs to two temp files to use in rest of RFC-DA pipeline.
    lakes_tmp_path is a pruned lakes
    ref_res_tmp_path includes flag for keeping if it is replacing a lake

    Specify lake_ids to keep with `1akes_keep`

    Parameters
    ----------
    lakes_path : str | Path
        NWM/HF 2.2 lakes path
    lakes_layer : str
        NWM/HF 2.2 lakes layer name
    ref_wb_path : Path | str
        reference waterbodies path
    ref_res_path : Path | str
        reference reservoirs path
    ref_fp_path : Path | str
       reference flowpath path (parquet)
    wb_buffer : int | float
        meters to buffer waterbodies for spatial joining to ref reservoirs
    lakes_tmp_path : str | Path
        temporary path for filtered lakes
    ref_res_tmp_path : str | Path
        temporary path for reference reservoirs including flag if lake is present
    lakes_keep: list[int]
        list of lake IDs to keep regardless of join criteria
    """
    gdf_rr = gpd.read_file(ref_res_path)
    gdf_wb = gpd.read_file(ref_wb_path)
    gdf_lks = gpd.read_file(lakes_path, layer=lakes_layer)
    gdf_ref = gpd.read_parquet(ref_fp_path)

    gdf_ref["flowpath_id"] = gdf_ref["flowpath_id"].astype(float)

    gdf_wb_b = gdf_wb.copy()
    gdf_wb_b["geometry"] = gdf_wb["geometry"].buffer(wb_buffer)

    lk_wb = gdf_lks.copy()
    rr_wb = gdf_rr.sjoin(gdf_wb_b, how="left")

    rr_wb["drop"] = False
    lk_wb["drop"] = False
    rr_wb["lk"] = False

    logger.info("Searching for duplicate COMIDs in HF 2.2 lakes and ref reservoirs")
    # if comid is in both lakes and ref reservoirs, drop it from lakes and insure it is kept in ref res
    for i in lk_wb["comid"].unique():
        rr = bool(rr_wb.loc[rr_wb["comid"] == i].shape[0])
        lk = bool(lk_wb.loc[lk_wb["comid"] == i].shape[0])
        if rr and lk:
            lk_wb.loc[lk_wb["comid"] == i, "drop"] = True
            rr_wb.loc[rr_wb["comid"] == i, "lk"] = True

    # insure we keep requested lakes
    lakes_keep = lakes_keep if lakes_keep is not None else []
    lk_wb["drop"] = np.where(lk_wb["lake_id"].isin(lakes_keep), False, lk_wb["drop"])

    lk_wb_keep = lk_wb.loc[lk_wb["drop"] == False, :].copy()  # noqa: E712
    lk_wb_keep.to_file(lakes_tmp_path, layer=lakes_layer)

    # crosswalk rr to flowpaths to hydroseq to keep the most downstream only
    logger.info("Crosswalking reference reservoirs to reference fabric")
    gdf_ref = gdf_ref[["flowpath_id", "hydroseq"]]
    rr_wb_lk = rr_wb.loc[rr_wb["lk"] == True, :].copy()  # noqa: E712
    rr_wb_lk["ref_fab_fp"] = pd.to_numeric(rr_wb_lk["ref_fab_fp"]).astype(float)
    rr_wb_lk = rr_wb_lk.merge(gdf_ref, left_on="ref_fab_fp", right_on="flowpath_id", how="left")
    rr_wb_lk["hydroseq"] = pd.to_numeric(rr_wb_lk["hydroseq"]).astype(float)
    gdf_min_fp = rr_wb_lk[["comid", "hydroseq"]].groupby(["comid"]).min()
    rr_wb_lk = (
        rr_wb_lk.merge(gdf_min_fp, on=["comid", "hydroseq"], how="inner")
        .drop_duplicates(subset=["comid", "hydroseq", "flowpath_id"])
        .drop(columns=["hydroseq", "index_right", "flowpath_id"])
        .reset_index(drop=True)
    )
    # remove lake trues from main ref reservoirs. Add in filtered lakes
    rr_wb = rr_wb.loc[rr_wb["lk"] == False, :].copy().drop(columns=["index_right"]).reset_index(drop=True)  # noqa: E712
    rr_wb = pd.concat([rr_wb, rr_wb_lk], ignore_index=True).reset_index(drop=True)

    rr_wb.to_file(ref_res_tmp_path)
    logger.info("Finished finding HF 2.2 lake duplicates")
