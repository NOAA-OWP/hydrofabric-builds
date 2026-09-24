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

    Parameters
    ----------
    cfg : HFConfig
        HF Config

    Returns
    -------
    pd.DataFrame
        Res DA dataframe
    """
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

    try:
        gages = gpd.read_file(cfg.output_file_path, layer="gages")
    except (DataLayerError, DataSourceError):
        logger.info(
            "Gages layer not available for Reservoir DA. Skipping additional crosswalking and gage check."
        )
        gages = gpd.GeoDataFrame(columns=[cfg.res_da.gage_id_field])

    if cfg.res_da.all_level_pool:
        logger.info("Setting all reservoir DA to level pool")
        return _all_level_pool(
            df_lakes=lakes,
            gage_id_field=cfg.res_da.gage_id_field,
            lake_id_field=cfg.res_da.lake_id_field,
            res_da_field=cfg.res_da.da_type_field,
        )

    df_list = []

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

    if cfg.res_da.great_lakes:
        logger.info("Adding Great Lakes")
        df_list.append(_add_great_lakes(mapping=cfg.lakes.great_lakes))

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

    if cfg.res_da.generate_additional_crosswalk:
        logger.info("Generating reservoir:gage crosswalks from data")

        if gages.any():
            fp = gpd.read_file(cfg.output_file_path, layer="flowpaths")
            df_list.append(_generate_additional_crosswalk(fp, gages, lakes))
            del fp

    logger.info("Merging reservoir DA tables")
    df_res_da = _merge(
        lakes, df_list, res_da_field=cfg.res_da.da_type_field, lake_id_field=cfg.res_da.lake_id_field
    )
    _check_gages_exist(gdf_gages=gages, df_res_da=df_res_da, gage_id_field=cfg.res_da.gage_id_field)

    return df_res_da
