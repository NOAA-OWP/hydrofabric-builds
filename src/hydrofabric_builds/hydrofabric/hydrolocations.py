import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd

logger = logging.getLogger(__name__)


def hydrolocations_pipeline(hf_path: Path) -> None:
    """Creates hydrolocations from gages and waterbodies table

    Creates a new index for hy_id starting from concatenated waterbodies and gages
    Overwrites waterbodies and gages to include hy_id
    Creates new hydrolocations table with hy_id and dn_nex_id

    Parameters
    ----------
    hf_path : Path
        Path to hydrofabric to use
    """
    gdf_wb = gpd.read_file(hf_path, layer="waterbodies")
    gdf_gages = gpd.read_file(hf_path, layer="gages")

    # set up ID
    len_wb = gdf_wb.shape[0]
    len_gages = gdf_gages.shape[0]

    end_wb = len_wb + 1
    start_gages = end_wb
    end_gages = start_gages + len_gages

    gdf_wb.insert(2, "hy_id", range(1, end_wb))
    gdf_gages.insert(2, "hy_id", range(start_gages, end_gages))

    # save ID
    gdf_wb.to_file(hf_path, layer="waterbodies", driver="GPKG", overwrite=True)
    gdf_gages.to_file(hf_path, layer="gages", driver="GPKG", overwrite=True)
    logger.info("Wrote waterbodies and gages with hy_id")

    # create HL table
    gdf_hl = gpd.GeoDataFrame(data={"hy_id": range(1, end_gages)})

    # join fp_ids to get downstream nex_id
    gdf_fp = gpd.read_file(hf_path, layer="flowpaths")
    gdf_wb = gdf_wb.merge(gdf_fp[["fp_id", "dn_nex_id"]], on="fp_id", how="left")
    gdf_gages = gdf_gages.merge(gdf_fp[["fp_id", "dn_nex_id"]], on="fp_id", how="left")

    # concat nexus
    df_dnnex = pd.concat(
        [gdf_wb[["hy_id", "dn_nex_id"]], gdf_gages[["hy_id", "dn_nex_id"]]], axis=0, ignore_index=True
    )
    gdf_hl = gpd.GeoDataFrame(gdf_hl.merge(df_dnnex, on="hy_id", how="left"))
    gdf_hl.to_file(hf_path, layer="hydrolocations", driver="GPKG", overwrite=True)
    logger.info("Wrote hydrolocations table")
