import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd
from pyogrio.errors import DataLayerError

logger = logging.getLogger(__name__)


def hydrolocations_pipeline(hf_path: Path) -> None:
    """Creates hydrolocations from any subset of gages, lakes, and waterbodies

    Creates a new index for hy_id from present tables
    Overwrites present tables to include hy_id
    Creates new hydrolocations table with hy_id and dn_nex_id
    Hydrolocations table is empty if no layers were present

    Parameters
    ----------
    hf_path : Path
        Path to hydrofabric to use
    """
    # Initialize an empty gdf for each layer to assert correct typing
    # Add new layer names here
    gdf_dict = {"waterbodies": gpd.GeoDataFrame(), "gages": gpd.GeoDataFrame(), "lakes": gpd.GeoDataFrame()}

    # This patterns handles null layers and assigns an incremental hy_id
    # cycle through opening layers and checking if present
    # assign a hy_id based on the index + 1
    # start the next hy_id where you left off (last value + 1)
    # end_hy starts at 1 until overwritten by finding a present layer
    # remove layer from dict if does not exist
    end_hy = 1

    for k in list(gdf_dict.keys()):
        try:
            gdf = gpd.read_file(hf_path, layer=k)
            hy_ids = gdf.index + end_hy
            gdf.insert(2, "hy_id", hy_ids)
            end_hy = gdf["hy_id"].iloc[-1] + 1
            gdf_dict[k] = gdf.copy()
        except DataLayerError:
            gdf_dict.pop(k)

    # if no layers were present, write an empty hydrolocations table with correct columns and return
    if not gdf_dict:
        df_hl = gpd.GeoDataFrame(columns=["hy_id", "dn_nex_id", "dn_virtual_nex_id"], data=[])
        df_hl.to_file(hf_path, layer="hydrolocations", driver="GPKG", overwrite=True)
        logger.info("Wrote empty hydrolocations table beacuse gages, lakes, and waterbodies were not present")
        return

    # concat the present layers for the HL dataframe
    concat_list = [v[["hy_id", "dn_nex_id", "dn_virtual_nex_id"]] for k, v in gdf_dict.items()]
    gdf_hl = gpd.GeoDataFrame(pd.concat(concat_list, ignore_index=True))

    # save with hy_id
    for k, v in gdf_dict.items():
        v.to_file(hf_path, layer=k, driver="GPKG", overwrite=True)
        logger.info(f"Wrote layer {k} with updated with hy_id")

    # save final hl
    gdf_hl.to_file(hf_path, layer="hydrolocations", driver="GPKG", overwrite=True)
    logger.info("Wrote hydrolocations table")
