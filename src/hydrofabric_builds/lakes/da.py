import logging

import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr

from hydrofabric_builds.schemas.hydrofabric import GreatLakesMapping, ResDAMapping

logger = logging.getLogger(__name__)

DA_MAPPING = ResDAMapping()


def _all_level_pool(
    df_lakes: pd.DataFrame,
    lake_id_field: str = "lake_id",
    gage_id_field: str = "site_no",
    res_da_field: str = "da_type",
) -> pd.DataFrame:
    """Set all lakes to level pool"""
    df_lakes = df_lakes[["nhf_lake_id", lake_id_field]].copy()
    df_lakes[gage_id_field] = None
    df_lakes[res_da_field] = DA_MAPPING.level_pool
    return df_lakes


def _read_res_index(
    ds: xr.Dataset,
    active_rfc: pd.DataFrame | None = None,
    active_gage_id: str = "nws shef id",
    res_da_field: str = "da_type",
    lake_id_field: str = "lake_id",
    usgs_gage_id_field: str = "usgs_gage_id",
    usgs_lake_id_field: str = "usgs_lake_id",
    usace_gage_id_field: str = "usace_gage_id",
    usace_lake_id_field: str = "usace_lake_id",
    rfc_gage_id_field: str = "rfc_gage_id",
    rfc_lake_id_field: str = "rfc_lake_id",
    output_gage_field: str = "site_no",
    usgs_fix_list: list[str] | None = None,
) -> pd.DataFrame:
    """Extract crosswalk values and add DA scheme

    active_rfc is a spreadsheet from OWP. This can be used to eliminate gages that are no
    longer active by filtering with `active_gage_id`

    usgs_fix_list: Some USGS IDs are missing a leading 0. Optionally pass a list of gage IDs
    to prepend '0'
    """
    df_list = []

    # Group the configurations for RFC, USGS, and USACE
    res_index_fields = [
        (rfc_gage_id_field, rfc_lake_id_field, DA_MAPPING.rfc_forecast),
        (usgs_gage_id_field, usgs_lake_id_field, DA_MAPPING.usgs_persistence),
        (usace_gage_id_field, usace_lake_id_field, DA_MAPPING.usace_persistence),
    ]

    for gage_field, lake_field, da_mapping in res_index_fields:
        if lake_field in ds.variables:
            crosswalk = pd.DataFrame(
                data={
                    gage_field: ds[gage_field].to_numpy(),
                    lake_field: ds[lake_field].to_numpy(),
                }
            )
            crosswalk[gage_field] = crosswalk[gage_field].apply(lambda x: x.decode("utf-8")).str.strip()
            crosswalk[res_da_field] = da_mapping

            # for RFC gages, filter by active gages if the data is available
            if gage_field == rfc_gage_id_field and active_rfc is not None:
                crosswalk = crosswalk.loc[crosswalk[gage_field].isin(active_rfc[active_gage_id])].copy()

            crosswalk.rename(columns={gage_field: output_gage_field, lake_field: lake_id_field}, inplace=True)
            df_list.append(crosswalk)

    df_out = pd.concat(df_list, ignore_index=True)

    # usgs_fix_list is a list of gages that need 0 prefix
    if usgs_fix_list:
        df_out.loc[df_out[output_gage_field].isin(usgs_fix_list), output_gage_field] = (
            "0" + df_out[output_gage_field]
        )

    return df_out


def _read_adhoc(
    gdf: gpd.GeoDataFrame,
    rfc_field: str = "locationId",
    gage_id_field: str = "site_no",
    lake_id_field: str = "lake_id",
    res_da_field: str = "da_type",
    null_value: int = -99999,
) -> pd.DataFrame:
    """Read adhoc lakes file and give RFC forecast type"""
    df = gdf.loc[gdf[lake_id_field] != null_value, [lake_id_field, rfc_field]].copy()
    df[res_da_field] = DA_MAPPING.rfc_forecast
    df.rename(columns={rfc_field: gage_id_field}, inplace=True)
    # drop OT suffix from Ohio RFC gages
    df[gage_id_field] = np.where(
        df[gage_id_field].str[-2:] == "OT", df[gage_id_field].str[:-2], df[gage_id_field]
    )

    df.reset_index(drop=True, inplace=True)
    return df


def _read_usace(
    gdf: gpd.GeoDataFrame,
    id_field: str = "location",
    gage_id_field: str = "site_no",
    res_da_field: str = "da_type",
    lake_id_field: str = "lake_id",
) -> pd.DataFrame:
    """Read the crosswalked USACE table"""
    df = gdf.loc[~gdf[lake_id_field].isnull(), [lake_id_field, id_field]].copy()
    df[res_da_field] = DA_MAPPING.usace_persistence
    df.rename(columns={id_field: gage_id_field}, inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def _read_usbr(
    gdf: gpd.GeoDataFrame,
    id_field: str = "locId",
    gage_id_field: str = "site_no",
    res_da_field: str = "da_type",
    lake_id_field: str = "lake_id",
) -> pd.DataFrame:
    """Read the crosswalked USACE table"""
    df = gdf.loc[~gdf[lake_id_field].isnull(), [lake_id_field, id_field]].copy()
    df[res_da_field] = DA_MAPPING.usbr_persistence
    df.rename(columns={id_field: gage_id_field}, inplace=True)
    df[gage_id_field] = "usbr-" + df[gage_id_field].astype(pd.Int64Dtype()).astype(str)
    df.reset_index(drop=True, inplace=True)
    return df


def _add_great_lakes(
    mapping: GreatLakesMapping,
    gage_id_field: str = "site_no",
    lake_id_field: str = "lake_id",
    res_da_field: str = "da_type",
) -> pd.DataFrame:
    dict_lakes = mapping.model_dump()

    lake_ids, site_nos = [], []
    for _k, v in dict_lakes.items():
        lake_ids.append(v[lake_id_field])
        site_nos.append(v[gage_id_field])

    return pd.DataFrame(
        data={
            lake_id_field: lake_ids,
            gage_id_field: site_nos,
            res_da_field: [DA_MAPPING.great_lakes] * len(lake_ids),
        }
    )


def _generate_additional_crosswalk(
    fp: gpd.GeoDataFrame, gages: gpd.GeoDataFrame, lakes: gpd.GeoDataFrame
) -> pd.DataFrame:
    """Stub to generate more crosswalk"""
    # TODO
    pass


def _merge(
    df_lakes: gpd.GeoDataFrame,
    df_list: list[pd.DataFrame],
    res_da_field: str = "da_type",
    gid_field: str = "nhf_lake_id",
    lake_id_field: str = "lake_id",
) -> pd.DataFrame:
    """Merge all dataframe sources and de-duplicate lake_id"""
    df_lakes = df_lakes[[gid_field, lake_id_field]].copy()

    if pd.api.types.is_numeric_dtype(df_lakes[lake_id_field].dtype):
        df_lakes[lake_id_field] = df_lakes[lake_id_field].astype(int).astype(str)

    for df in df_list:
        if pd.api.types.is_numeric_dtype(df[lake_id_field].dtype):
            df[lake_id_field] = df[lake_id_field].astype(int).astype(str)

    df_all = pd.concat(df_list)

    # de-dupe 1: drop true duplicates of lake_id and same res type
    df_all = df_all.drop_duplicates(subset=[lake_id_field, res_da_field], keep=False)

    # de-dupe 2: choose the duplicate with greater res_da field (non-level pool) and prefer RFC over all
    dupe = df_all.loc[df_all.duplicated(subset=lake_id_field, keep=False)].copy().reset_index(drop=True)
    df_all = df_all.loc[~df_all.duplicated(subset=lake_id_field, keep=False)].copy().reset_index(drop=True)

    dupe["priority"] = 0
    dupe["priority"] = np.where(dupe[res_da_field] > 1, 1, dupe["priority"])  # anything non-LP
    dupe["priority"] = np.where(dupe[res_da_field] == 4, 2, dupe["priority"])  # RFC
    dupe["priority"] = np.where(dupe[res_da_field] == 6, 3, dupe["priority"])  # Great Lakes
    idx = dupe.groupby(lake_id_field)["priority"].idxmax()
    dupe = dupe.loc[idx].copy().drop(columns=["priority"])

    # if there are still duplicates, take the first as they have same res DA value
    dupe = dupe.drop_duplicates(subset=lake_id_field, keep="first")

    df_all = pd.concat([df_all, dupe], ignore_index=True)
    df_all = df_all.reset_index(drop=True)

    # merge back to lakes
    df_lakes = df_lakes.merge(df_all, how="left", on=lake_id_field)
    df_lakes.reset_index(drop=True, inplace=True)
    df_lakes.loc[df_lakes[res_da_field].isnull(), res_da_field] = DA_MAPPING.level_pool
    df_lakes[res_da_field] = df_lakes[res_da_field].astype(int)

    assert ~df_lakes.duplicated(subset=lake_id_field).any(), f"Duplicate {lake_id_field} detected"
    assert ~df_lakes.duplicated(subset=gid_field).any(), f"Duplicate {gid_field} detected"

    return df_lakes


def _check_gages_exist(
    df_res_da: pd.DataFrame, gdf_gages: gpd.GeoDataFrame, gage_id_field: str = "site_no"
) -> None:
    """Check if reservoir DA gages exist in gages layer."""
    # FIXME: Move to validation script
    if gdf_gages.empty:
        logger.info("Gages layer is not available. Cannot check if reservoir DA gages are available.")
        return
    res_gages = df_res_da.loc[~df_res_da[gage_id_field].isnull()].copy()
    missing_gages = res_gages.loc[~res_gages[gage_id_field].isin(gdf_gages[gage_id_field])].copy()
    len_missing = len(missing_gages)

    if len_missing > 0:
        logger.warning(
            f"{len_missing} ({round(len_missing / len(res_gages) * 100)}%) gages for reservoir DA missing from gages layer"
        )
    else:
        logger.info("All gages for reservoir DA lake:gage crosswalk present in gages file.")
    return
