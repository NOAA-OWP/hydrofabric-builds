import logging
from pathlib import Path

import geopandas as gpd
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
        .drop(columns=["hydroseq"])
    )

    gdf_lakes.to_file(output, layer="lakes", driver="GPKG")
    logger.info(f"Wrote NWM/HF2.2 lakes to {output}")
