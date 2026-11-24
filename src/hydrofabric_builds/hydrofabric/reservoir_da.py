import logging

import geopandas as gpd
import pandas as pd
import xarray as xr
from pyogrio.errors import DataLayerError, DataSourceError

from hydrofabric_builds.config import HFConfig
from hydrofabric_builds.lakes.da import (
    _add_great_lakes,
    _all_level_pool,
    _check_gages_exist,
    _generate_additional_crosswalk,
    _merge,
    _read_adhoc,
    _read_res_index,
    _read_run_of_river,
    _read_usace,
    _read_usbr,
)

logger = logging.getLogger(__name__)


def res_da_pipeline(cfg: HFConfig) -> pd.DataFrame:
    """Runs the reservoir data assimilation pipeline

    Reads from reservoir index, adhoc lakes file, and creates additional gage:lake crosswalk.
    If reservoir index is not available, returns all level pool.
    If lakes layer is not available, returns empty table.
    If gages are not available when additional gage:lake crosswalk is requested, crosswalk will not be run.

    Read each additional crosswalk file if present and add to working dataframe list
    Concatenate all dataframes at end to be single reservoir DA table

    Parameters
    ----------
    cfg : HFConfig
        HF Config

    Returns
    -------
    pd.DataFrame
        Res DA dataframe
    """
    # Trry reading the lakes layer. If there is no lake layer, return empty
    try:
        lakes = gpd.read_file(cfg.output_file_path, layer="lakes")
    except (DataLayerError, DataSourceError):
        logger.info("Lakes layer not available for Reservoir DA. Returning empty dataframe.")
        return pd.DataFrame(
            columns=[
                "nhf_lake_id",
                cfg.res_da.lake_id_field,
                cfg.res_da.gage_id_field,
                cfg.res_da.da_type_field,
            ]
        )

    # Try reading the gage layer. If there is no gage layer, there will be no gage gage crosswalk
    try:
        gages = gpd.read_file(cfg.output_file_path, layer="gages")
    except (DataLayerError, DataSourceError):
        logger.info(
            "Gages layer not available for Reservoir DA. Skipping additional crosswalking and gage check."
        )
        gages = gpd.GeoDataFrame(columns=[cfg.res_da.gage_id_field])

    # If cfg requests all level pool, set all level pool
    if cfg.res_da.all_level_pool:
        logger.info("Setting all reservoir DA to level pool")
        return _all_level_pool(
            df_lakes=lakes,
            gage_id_field=cfg.res_da.gage_id_field,
            lake_id_field=cfg.res_da.lake_id_field,
            res_da_field=cfg.res_da.da_type_field,
        )

    # iteratively built list of dataframes with reservoir data
    df_list = []

    # get NWM v3 reservoir index crosswalk and append to working list
    logger.info("Retrieving reservoirs from crosswalk")
    ds = xr.open_dataset(cfg.res_da.res_crosswalk.path)
    df_active_rfc = pd.read_csv(cfg.res_da.active_rfc.path) if cfg.res_da.active_rfc.path.exists() else None
    df_list.append(
        _read_res_index(
            ds=ds,
            active_rfc=df_active_rfc,
            active_gage_id=cfg.res_da.active_rfc.id_field,
            output_gage_field=cfg.res_da.gage_id_field,
            usgs_fix_list=cfg.res_da.usgs_fix_list,
            **cfg.res_da.res_crosswalk.fields.model_dump(),
        )
    )
    del ds

    # Add Great Lakes if requested and append to working list
    if cfg.res_da.great_lakes:
        logger.info("Adding Great Lakes")
        df_list.append(_add_great_lakes(mapping=cfg.lakes.great_lakes))

    # Add adhoc RFC reservoirs if requested and append to working list
    if cfg.res_da.adhoc.run:
        logger.info("Retrieving reservoirs from adhoc table")
        gdf = gpd.read_file(cfg.res_da.adhoc.path, layer=cfg.res_da.adhoc.layer)
        df_list.append(
            _read_adhoc(
                gdf=gdf,
                rfc_field=cfg.res_da.adhoc.rfc_field,
                gage_id_field=cfg.res_da.gage_id_field,
                lake_id_field=cfg.res_da.lake_id_field,
                res_da_field=cfg.res_da.da_type_field,
                null_value=cfg.res_da.adhoc.null_value,
            )
        )
        del gdf

    # Add additional USACE reservoirs if requested and append to working list
    if cfg.res_da.usace.run:
        logger.info("Retrieving reservoirs from USACE crosswalk table")
        gdf = gpd.read_file(cfg.res_da.usace.path)
        df_list.append(
            _read_usace(
                gdf,
                id_field=cfg.res_da.usace.id_field,
                gage_id_field=cfg.res_da.gage_id_field,
                lake_id_field=cfg.res_da.lake_id_field,
                res_da_field=cfg.res_da.da_type_field,
            )
        )
        del gdf

    # Add USBR reservoirs if requested and append to working list
    if cfg.res_da.usbr.run:
        logger.info("Retrieving reservoirs from USBR crosswalk table")
        gdf = gpd.read_file(cfg.res_da.usbr.path)
        df_list.append(
            _read_usbr(
                gdf,
                id_field=cfg.res_da.usbr.id_field,
                gage_id_field=cfg.res_da.gage_id_field,
                lake_id_field=cfg.res_da.lake_id_field,
                res_da_field=cfg.res_da.da_type_field,
            )
        )
        del gdf

    # Add run of river RFC 'reservoirs' if requested and append to working list
    # Adds a run_of_river columns and sets to True
    if cfg.res_da.run_of_river.run:
        logger.info("Retrieving run of river dams from run of river crosswalk table")
        gdf = gpd.read_file(cfg.res_da.run_of_river.path, layer=cfg.res_da.run_of_river.layer)
        df_list.append(
            _read_run_of_river(
                gdf,
                id_field=cfg.res_da.run_of_river.id_field,
                gage_id_field=cfg.res_da.gage_id_field,
                lake_id_field=cfg.res_da.lake_id_field,
                res_da_field=cfg.res_da.da_type_field,
            )
        )
        del gdf

    # TODO: Unfinished feature, but leaving as nugget for future development:
    # Create crosswalk between more gages and reservoirs and append to working list
    if cfg.res_da.generate_additional_crosswalk:
        logger.info("Generating reservoir:gage crosswalks from data")

        if gages.any():
            fp = gpd.read_file(cfg.output_file_path, layer="flowpaths")
            df_list.append(_generate_additional_crosswalk(fp, gages, lakes))
            del fp

    # Merge working list of dataframes and handle duplicates
    # Will add run_of_river column to other dataframes and set to false
    logger.info("Merging reservoir DA tables")
    df_res_da = _merge(
        lakes,
        df_list,
        res_da_field=cfg.res_da.da_type_field,
        lake_id_field=cfg.res_da.lake_id_field,
        gage_id_field=cfg.res_da.gage_id_field,
        nhf_lake_id_field="nhf_lake_id",
    )
    # Check gages exist and report out if not
    _check_gages_exist(gdf_gages=gages, df_res_da=df_res_da, gage_id_field=cfg.res_da.gage_id_field)

    return df_res_da
