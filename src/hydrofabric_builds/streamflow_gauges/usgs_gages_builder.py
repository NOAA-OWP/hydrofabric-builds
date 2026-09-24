from __future__ import annotations

import logging
import pathlib
import re
import tempfile
import zipfile
from collections.abc import Iterable
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr

from hydrofabric_builds.schemas.hydrofabric import GreatLakesMapping

logger = logging.getLogger(__name__)


# --- helpers to parse Name field ---
_site_href_re = re.compile(r"site_no=(\d+)", re.I)
_digits_re = re.compile(r"\b(\d{5,15})\b")
_strip_tags_re = re.compile(r"<[^>]+>")


def extract_site_no(name_html: str | None) -> str | None:
    """
    Extract the site number based on name_html in column "Name"

    :param name_html: http adress in column "Name"
    :return: site number extracted from http address
    """
    if not name_html:
        return None
    m = _site_href_re.search(name_html)
    if m:
        return m.group(1)
    m = _digits_re.search(name_html)
    return m.group(1) if m else None


def strip_html(name_html: str | None) -> str | None:
    """Breaking down (splitting) htmll address to be able to take out site number"""
    if not name_html:
        return None
    return _strip_tags_re.sub("", name_html).strip()


# --- infer state from 'streamgages_statename.kmz' ---
def infer_state_from_filename(path: Path) -> str:
    """Finds state''s name in abbreviation from the file's name"""
    stem = path.stem.lower()
    state_part = (
        stem.split("streamgages_", 1)[-1]
        if "streamgages_" in stem
        else stem.split("_")[-1]
    )
    state_clean = state_part.replace("_", " ").replace("-", " ").strip()
    return " ".join(w.capitalize() for w in state_clean.split())


# --- KMZ -> GeoDataFrame (points only) ---
def read_kmz_points(kmz_file: str | Path, layer: str | None = None) -> gpd.GeoDataFrame:
    """
    Reads KMZ file

    :param kmz_file: file address
    :param layer: None
    :return: geo dataframe that is read
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        with zipfile.ZipFile(kmz_file) as z:
            z.extractall(tmp)
        kml = next(tmp.rglob("*.kml"))
        # layers = fiona.listlayers(kml)
        lyr = layer  # or layers[0]

        gdf = gpd.read_file(kml, driver="KML", layer=lyr)
        if gdf.empty:
            return gdf
        gdf = gdf[gdf.geometry.notna()].copy()
        # explode MultiPoint if any, then keep Points
        if "MultiPoint" in set(gdf.geometry.geom_type):
            gdf = gdf.explode(index_parts=False, ignore_index=True)
        gdf = gdf[gdf.geometry.geom_type == "Point"]
        return gdf


# --- main: scan directory, read all streamgages_*.kmz, merge ---
def build_usgs_gages_from_kmz(
    folder: str | Path, state_col: str = "state", src_crs: str = "EPSG:4326"
) -> gpd.GeoDataFrame:
    """
    Reads all gages from all regions and merges them

    :param folder: directory containing all kmz files
    :param state_col: column's name that contains the state's name
    :return: merged geo dataframe file
    """
    folder = Path(folder)
    files = sorted(folder.glob("*.kmz"))
    if not files:
        raise FileNotFoundError(f"No KMZ files found in {folder}")

    frames: list[gpd.GeoDataFrame] = []
    for kmz in files:
        if "streamgages_" not in kmz.stem.lower():
            continue  # skip non-matching files

        gdf = read_kmz_points(kmz)
        if gdf.empty:
            continue

        gdf["site_no"] = gdf["Name"].map(extract_site_no)
        gdf["name_plain"] = gdf["Name"].map(strip_html)
        gdf[state_col] = infer_state_from_filename(kmz)

        try:
            keep = [
                "geometry",
                state_col,
                "site_no",
                "name_plain",
                "Name",
                "Description",
            ]
            gdf = gdf[keep].rename(
                columns={"Name": "name_raw", "Description": "description"}
            )
        except KeyError:
            keep = [
                "geometry",
                state_col,
                "site_no",
                "name_plain",
                "Name",
                "description",
            ]
            gdf = gdf[keep].rename(columns={"Name": "name_raw"})
        ## adding a column to show the status
        gdf["status"] = "USGS-discontinued"
        frames.append(gdf)

    if not frames:
        return gpd.GeoDataFrame(
            columns=[
                "geometry",
                state_col,
                "site_no",
                "name_plain",
                "name_raw",
                "description",
                "status",
            ],
            geometry="geometry",
            crs=src_crs,
        )

    all_gdf = pd.concat(frames, ignore_index=True)
    return gpd.GeoDataFrame(all_gdf, geometry="geometry", crs=src_crs)


def merge_minimal_gages(
    gages: gpd.GeoDataFrame,
    source: gpd.GeoDataFrame,
    *,
    update_existing: bool = False,
    fill_value: str = "-",
) -> gpd.GeoDataFrame:
    """
    Merge `source` into `gages` using ONLY these mappings:

      - geometry   -> geometry
      - site_no    -> site_no
      - station_nm -> name_plain

    All other columns in `gages` are filled with '-' for new rows.

    Parameters
    ----------
    gages : GeoDataFrame
        Must contain at least: ['geometry','site_no','name_plain'].
        (Any other columns will be filled with '-' for new rows.)
    source : GeoDataFrame
        Must contain: ['geometry','site_no','station_nm'].
    update_existing : bool, default False
        If True, for matching site_no: overwrite geometry and name_raw in `gages`.
    fill_value : str, default '-'
        Value used to fill non-mapped columns for new rows.

    Returns
    -------
    GeoDataFrame
        Updated `gages`.
    """
    required_in_gages = {"geometry", "site_no", "name_plain", "status"}
    missing = required_in_gages - set(gages.columns)
    if missing:
        raise ValueError(f"`gages` missing required columns: {sorted(missing)}")

    required_in_source = {"geometry", "site_no", "station_nm"}
    missing_src = required_in_source - set(source.columns)
    if missing_src:
        raise ValueError(f"`source` missing required columns: {sorted(missing_src)}")

    out = gages.copy()
    src = source.copy()

    # Normalize types
    out["site_no"] = out["site_no"].astype(str)
    src["site_no"] = src["site_no"].astype(str)

    # Reproject source to match gages CRS if needed
    if out.crs is not None and src.crs is not None and out.crs != src.crs:
        src = src.to_crs(out.crs)

    # Build a minimal incoming frame matching the gages schema (only mapped fields)
    incoming = gpd.GeoDataFrame(
        {
            "geometry": src["geometry"],
            "site_no": src["site_no"],
            "name_plain": src["station_nm"].fillna(fill_value).astype(str),
            "status": "TXDOT",
        },
        geometry="geometry",
        crs=out.crs or src.crs,
    )

    # Split new vs existing by site_no
    is_existing = incoming["site_no"].isin(out["site_no"])
    to_add = incoming.loc[~is_existing].copy()
    to_upd = incoming.loc[is_existing].copy()

    # For NEW rows: create all gages columns, fill non-mapped with '-'
    if not to_add.empty:
        # Start with all gages columns
        add = gpd.GeoDataFrame(columns=out.columns, crs=out.crs)
        # Put the mapped fields in place
        add = pd.concat(
            [
                add,
                to_add[out.columns.intersection(["geometry", "site_no", "name_plain"])],
            ],
            ignore_index=True,
        )

        # Fill everything else with '-'
        for col in add.columns:
            if col not in {"geometry", "site_no", "name_plain"}:
                add[col] = add[col].fillna(fill_value)

        # Ensure geometry column is correct dtype
        add = gpd.GeoDataFrame(add, geometry="geometry", crs=out.crs)
        out = pd.concat([out, add], ignore_index=True)

    # For EXISTING rows: optionally overwrite geometry & name_plain
    if update_existing and not to_upd.empty:
        upd = to_upd.set_index("site_no")
        out_idx = out.set_index("site_no")

        # Overwrite geometry and name_plain where present in `to_upd`
        out_idx.loc[upd.index, "name_plain"] = upd["name_plain"]
        out_idx.loc[upd.index, "status"] = upd["status"]
        out_idx.loc[upd.index, "geometry"] = upd["geometry"]

        out = out_idx.reset_index()
        out = gpd.GeoDataFrame(out, geometry="geometry", crs=gages.crs)

    return out


def merge_gage_xy_into_gages(
    gages: gpd.GeoDataFrame,
    gage_xy_csv: str | Path,
    src_crs: str = "EPSG:4326",
    update_existing: bool = True,
    exclude_ids: list[str | int] | tuple[str, ...] | None = None,
    fill_value: str = "-",
) -> gpd.GeoDataFrame:
    """
    Merge gauge_xy CSV (columns: gageid, lon, lat) into `gages`.

    Mapping:
      - gageid  -> site_no
      - (lon, lat) -> geometry
    All other columns in `gages` are filled with '-'.

    Parameters
    ----------
    gages : GeoDataFrame
        Master table with columns like:
        ['geometry','state','site_no','name_plain','name_raw','description', ...]
    gage_xy_csv : path-like
        CSV with columns at least: ['gageid','lon','lat'].
    update_existing : bool
        If True, overwrite geometry for matching site_no.
    exclude_ids : tuple[str,...]
        Site numbers to drop before merging (outside domain, etc.)
    fill_value : str
        Fill for non-mapped columns in new rows.

    Returns
    -------
    GeoDataFrame
        Updated `gages`.
    """
    # --- Read and prep the XY CSV ---
    df = pd.read_csv(gage_xy_csv, dtype={"gageid": str})
    # normalize types
    df["gageid"] = df["gageid"].astype(str).str.strip()
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df = df.dropna(subset=["gageid", "lon", "lat"]).copy()

    # Exclude specific IDs
    if exclude_ids:
        df = df[~df["gageid"].isin(exclude_ids)].copy()

    # Build GeoDataFrame from lon/lat (WGS84)
    incoming = gpd.GeoDataFrame(
        {
            "site_no": df["gageid"].astype(str),
            "geometry": gpd.points_from_xy(df["lon"], df["lat"]),
            "status": "CADWR_ENVCA",
        },
        geometry="geometry",
        crs=src_crs,
    )

    # Reproject to match gages CRS if defined
    if gages.crs is not None and incoming.crs != gages.crs:
        incoming = incoming.to_crs(gages.crs)

    # Ensure site_no is string on both sides
    out = gages.copy()
    out["site_no"] = out["site_no"].astype(str)
    incoming["site_no"] = incoming["site_no"].astype(str)

    # Split: existing vs new
    is_existing = incoming["site_no"].isin(out["site_no"])
    to_upd = incoming.loc[is_existing].copy()
    to_add = incoming.loc[~is_existing].copy()

    # --- Append NEW rows: create all gages columns; fill others with '-' ---
    if not to_add.empty:
        # start with all columns gages has
        add = gpd.GeoDataFrame(columns=out.columns, crs=out.crs)
        # put mapped fields in place
        add = pd.concat(
            [add, to_add.reindex(columns=["geometry", "site_no", "status"])],
            ignore_index=True,
        )

        # fill every other column with '-'
        for col in add.columns:
            if col not in {"geometry", "site_no", "status"}:
                add[col] = add[col].fillna(fill_value)

        add = gpd.GeoDataFrame(add, geometry="geometry", crs=out.crs)
        out = pd.concat([out, add], ignore_index=True)

    # --- Update EXISTING rows (geometry only) ---
    if update_existing and not to_upd.empty:
        out_idx = out.set_index("site_no")
        upd_idx = to_upd.set_index("site_no")
        # Note: I don't update the status here for the existing ones as the list is a mix of OCONUS gages.
        # therefore it doesn't make sense to have them under "CADWR_ENVCA"
        # out_idx.loc[upd_idx.index, "status"] = upd_idx["status"]
        out_idx.loc[upd_idx.index, "geometry"] = upd_idx["geometry"]
        out = out_idx.reset_index()
        out = gpd.GeoDataFrame(out, geometry="geometry", crs=gages.crs)

    return out


def add_missing_usgs_sites(
    gages: gpd.GeoDataFrame, missed_ids: Iterable[str], src_crs: str = "EPSG:4326"
) -> tuple[gpd.GeoDataFrame, list[str], list[str], pd.DataFrame]:
    """
    - Split missed_ids into USGS-style (digits only) and non-USGS (contain letters)

    - Fetch USGS site metadata for USGS IDs via NWIS Site Service
    - Build GeoDataFrame of fetched sites and append the ones not already in gages['site_no']
    - Fill other columns in gages with '-' for newly added rows
    Returns:
      (updated_gages, usgs_ids, non_usgs_ids, fetched_df_raw)
    """
    # 1) Normalize IDs to strings and split lists
    missed_ids = [str(x).strip() for x in missed_ids if str(x).strip()]
    usgs_ids = [x for x in missed_ids if x.isdigit()]
    non_usgs = [x for x in missed_ids if not x.isdigit()]

    # 2) If no USGS IDs, just return what we have
    if not usgs_ids:
        return gages.copy(), usgs_ids, non_usgs, pd.DataFrame()

    # 3) Query NWIS Site Service for USGS sites
    sites_param = ",".join(usgs_ids)
    url = f"https://waterservices.usgs.gov/nwis/site/?format=rdb&siteStatus=all&sites={sites_param}"

    df = pd.read_csv(
        url,
        sep="\t",
        comment="#",
        dtype=str,
        engine="python",
    )
    # 2) Strip whitespace from all string cells
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].str.strip()

    # 3) Drop the column-width row (e.g., '5s 15s 50s ...')
    #    Heuristic: 'agency_cd' equals something like '5s' OR any value endswith('s')
    if not df.empty:
        mask_width_row = df["agency_cd"].str.endswith("s", na=False)
        # Occasionally multiple such rows—drop all that match this pattern
        df = df[~mask_width_row].copy()

    # 4) Coerce numeric columns (lat/lon/alt)
    for num_col in ["dec_lat_va", "dec_long_va", "alt_va", "alt_acy_va"]:
        if num_col in df.columns:
            df[num_col] = pd.to_numeric(df[num_col], errors="coerce")

    # 5) Drop rows without valid coordinates
    df = df.dropna(subset=["dec_lat_va", "dec_long_va"]).copy()
    df["status"] = "USGS-active"
    # 6) Build GeoDataFrame (WGS84 lon/lat)
    gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df["dec_long_va"], df["dec_lat_va"]),
        crs=src_crs,
    )

    # Append only missing site_no rows; fill other gages columns with '-'
    gages_updated = merge_minimal_gages(
        gages=gages,
        source=gdf,
        update_existing=True,  # set True if you also want to overwrite geometry/name_raw for matches
    )

    return gages_updated, usgs_ids, non_usgs, df


def merge_usgs_shapefile_into_gages(
    gages: gpd.GeoDataFrame,
    shp_path: str | bytes | Path,
    update_existing: bool = False,
    fill_value: str = "-",
) -> gpd.GeoDataFrame:
    """
    Read a USGS gage shapefile and merge into `gages`.

    Expected shapefile columns:
      - STAID   -> site_no
      - STANAME -> name_plain
      - ST      -> state
      - URL     -> name_raw
      - geometry (Point)

    Behavior:
      - Adds only *missing* site_no rows by default.
      - For new rows, fills any other `gages` columns with `fill_value` (default "-").
      - If update_existing=True, also overwrites geometry/name_plain/name_raw/state for matches.

    Parameters
    ----------
    gages : GeoDataFrame
        Master table with schema like:
        ['geometry','state','site_no','name_plain','name_raw','description', ...]
    shp_path : path-like
        Path to the shapefile (.shp) or any vector supported by GeoPandas.
    update_existing : bool
        If True, update geometry/name_plain/name_raw/state for existing site_no rows.
    fill_value : str
        Value to fill non-mapped columns for new rows.

    Returns
    -------
    GeoDataFrame
        Updated `gages`.
    """
    # Load shapefile
    src = gpd.read_file(shp_path)

    # Check required source columns
    required_src = {"STAID", "STANAME", "ST", "URL", "geometry"}
    missing_src = required_src - set(src.columns)
    if missing_src:
        raise ValueError(f"Shapefile missing required columns: {sorted(missing_src)}")

    # Normalize ID to string
    src = src.copy()
    src["STAID"] = src["STAID"].astype(str).str.strip()

    # Build minimal incoming frame mapped to gages schema
    incoming = gpd.GeoDataFrame(
        {
            "site_no": src["STAID"],
            "name_plain": src["STANAME"].fillna(fill_value).astype(str),
            "state": src["ST"].fillna(fill_value).astype(str),
            "name_raw": src["URL"].fillna(fill_value).astype(str),
            "status": "USGS-active",
            "geometry": src["geometry"],
        },
        geometry="geometry",
        crs=src.crs,
    )

    # Reproject to match gages CRS if needed
    out = gages.copy()
    if out.crs is not None and incoming.crs is not None and incoming.crs != out.crs:
        incoming = incoming.to_crs(out.crs)

    # Ensure site_no is string on both sides
    out["site_no"] = out["site_no"].astype(str)
    incoming["site_no"] = incoming["site_no"].astype(str)

    # Split new vs existing
    is_existing = incoming["site_no"].isin(out["site_no"])
    to_add = incoming.loc[~is_existing].copy()
    to_upd = incoming.loc[is_existing].copy()

    # Prepare NEW rows with full gages schema; fill others with '-'
    if not to_add.empty:
        # Start with all columns from gages
        add = gpd.GeoDataFrame(columns=incoming.columns, crs=out.crs)

        # Place mapped fields (use intersection to avoid KeyErrors if gages lacks some)
        for col, mapped in {
            "geometry": "geometry",
            "site_no": "site_no",
            "name_plain": "name_plain",
            "name_raw": "name_raw",
            "state": "state",
            "status": "status",
        }.items():
            if col in add.columns:
                add[col] = to_add[mapped].values

        # Fill every other column with fill_value
        for col in add.columns:
            if col not in {
                "geometry",
                "site_no",
                "name_plain",
                "name_raw",
                "state",
                "status",
            }:
                add[col] = add[col].fillna(fill_value)

        add = gpd.GeoDataFrame(add, geometry="geometry", crs=out.crs)
        out = pd.concat([out, add], ignore_index=True)

    # UPDATE existing rows (geometry/name/state)
    if update_existing and not to_upd.empty:
        out_idx = out.set_index("site_no")
        upd_idx = to_upd.set_index("site_no")

        def _maybe_set(col_out: str, col_in: str) -> None:
            """Checks if the columns names match, then uses them"""
            if col_out in out_idx.columns and col_in in upd_idx.columns:
                out_idx.loc[upd_idx.index, col_out] = upd_idx[col_in]

        _maybe_set("geometry", "geometry")
        _maybe_set("name_plain", "name_plain")
        _maybe_set("name_raw", "name_raw")
        _maybe_set("state", "state")
        _maybe_set("status", "status")

        out = out_idx.reset_index()
        out = gpd.GeoDataFrame(out, geometry="geometry", crs=gages.crs)

    return out


def merge_rfc_gages(
    gages: gpd.GeoDataFrame,
    rfc_path: Path,
    nwm_rfc_path: Path,
    rfc_id_col: str = "nws shef id",
    status_col: str | None = "forecast status",
    nwm_rfc_id: str = "rfc_gage_id",
    x_col: str | None = "longitude",
    y_col: str | None = "latitude",
    rfc_crs: str | int = "EPSG:4326",
) -> gpd.GeoDataFrame:
    """Adds RFC gages. Retains RFC gages from NWM v3 reservoir index if provided.

    Set to retain only gages with all year forecasts. If NWM v3 reservoir index is provided, any matching
    gages will be retained.

    Parameters
    ----------
    gages : gpd.GeoDataFrame
        Master table
    rfc_path : Path
        Path to RFC station data
    nwm_rfc_path : Path
        Path to NWM reservoir index. If this does not exist, function will pass over
    rfc_id_col : str, optional
        Gage ID column in RFC data, by default "nws shef id"
    status_col : str | None, optional
        Forecast status column used to filter gages, by default "forecast status"
    nwm_rfc_id : str, optional
        Gage ID in NWM reservoir index file, by default "rfc_gage_id"
    x_col : str | None, optional
        x geometry, by default "longitude"
    y_col : str | None, optional
        y geometry, by default "latitude"
    rfc_crs : _type_, optional
        CRS of RFC table, by default "EPSG:4326"

    Returns
    -------
    gpd.GeoDataFrame
        Updated gages
    """
    df_rfc = pd.read_csv(rfc_path)
    df_rfc.loc[
        df_rfc[status_col] == "Forecasts are issued routinely year-round.", "priority"
    ] = 1

    if nwm_rfc_path.exists():
        if ".nc" not in nwm_rfc_path.name:
            logger.info(
                "NWM reservoir index path with RFC gages is not type netcdf. Cannot be read. Filtering RFC gages by NWM reservoirs will be skipped."
            )

        else:
            ds = xr.open_dataset(nwm_rfc_path)
            df_nwm = (
                ds[nwm_rfc_id]
                .to_pandas()
                .apply(lambda x: x.decode("utf-8"))
                .str.strip()
            )
            df_rfc.loc[df_rfc[rfc_id_col].isin(df_nwm.values), "priority"] = 1

    df_rfc = df_rfc.loc[df_rfc["priority"] == 1, [rfc_id_col, x_col, y_col]].copy()
    gdf_rfc = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy(x=df_rfc[x_col], y=df_rfc[y_col]),
        data={"site_no": df_rfc[rfc_id_col]},
        crs=rfc_crs,
    )
    gdf_rfc = gdf_rfc.to_crs(gages.crs)
    gdf_rfc["status"] = "RFC"
    gages = pd.concat([gages, gdf_rfc])
    logger.info(
        f"Added {len(gdf_rfc)} RFC gages. Some may be dropped if outside domain."
    )
    return gages


def merge_nid_gages(
    gages: gpd.GeoDataFrame,
    nid_path: Path,
    nwm_rfc_path: Path,
    nwm_usace_id: str = "usace_gage_id",
    nid_id_col: str = "NIDID",
    x_col: str | None = "LONGITUDE",
    y_col: str | None = "LATITUDE",
    nid_crs: str | int = "EPSG:4326",
) -> gpd.GeoDataFrame:
    """Adds USACE gages that are in NWM v3 reservoir index. IDs are extracted from NID (National Inventory of Dams)

    Parameters
    ----------
    gages : gpd.GeoDataFrame
        Master table
    nid_path : Path
        Path to NID csv
    nwm_rfc_path : Path
        Path to NWM reservoir index. If this does not exist, function will pass over_
    nwm_usace_id : str, optional
        Gage ID in NWM reservoir index USACE table, by default "usace_gage_id"
    nid_id_col : str, optional
        Gage ID in NID table, by default 'NIDID'
    x_col : str | None, optional
        x geometry, by default "LONGITUDE"
    y_col : str | None, optional
        y geometry, by default "LATITUDE"
    nid_crs : _str, optional
        CRS of NID table, by default "EPSG:4326"

    Returns
    -------
    gpd.GeoDataFrame
        Updated gages
    """
    df_nid = pd.read_csv(nid_path)

    if nwm_rfc_path.exists():
        if ".nc" not in nwm_rfc_path.name:
            logger.info(
                "NWM reservoir index path with RFC gages is not type netcdf. Cannot be read. Filtering RFC gages by NWM reservoirs will be skipped."
            )
            return gages
        else:
            ds = xr.open_dataset(nwm_rfc_path)

        if nwm_usace_id in ds.variables:
            df_nwm = (
                ds[nwm_usace_id]
                .to_pandas()
                .apply(lambda x: x.decode("utf-8"))
                .str.strip()
            )
            df_nid = df_nid.loc[df_nid[nid_id_col].isin(df_nwm.values)].copy()

            gdf_nid = gpd.GeoDataFrame(
                geometry=gpd.points_from_xy(x=df_nid[x_col], y=df_nid[y_col]),
                data={"site_no": df_nid[nid_id_col]},
                crs=nid_crs,
            )
            gdf_nid = gdf_nid.to_crs(gages.crs)
            gdf_nid["status"] = "USACE"
            gages = pd.concat([gages, gdf_nid])
            logger.info(
                f"Added {len(gdf_nid)} USACE gages from reservoir index. Some may be dropped if outside domain."
            )
        else:
            logger.info(
                "No NID gages added because USACE crosswalk not available in NWM reservoir index."
            )
            return gages
    else:
        logger.info(
            f"No NID gages added because NWM reservoir NetCDF file {nwm_rfc_path} is not available."
        )
        return gages
    return gages


def merge_adhoc_lakes_gages(
    gages: gpd.GeoDataFrame,
    adhoc_path: Path,
    adhoc_gage_id: str = "locationId",
    x_col: str | None = "Lon",
    y_col: str | None = "Lat",
    adhoc_crs: str | int = "EPSG:4326",
) -> gpd.GeoDataFrame:
    """Adds gages from Adhoc Lakes introduced in Lakes Pipeline.

    Parameters
    ----------
    gages : gpd.GeoDataFrame
        Master Table
    adhoc_path : Path
        Path to adhoc lakes GPKG
    adhoc_gage_id : str, optional
        Gage ID in adhoc table, by default "locationId"
    x_col : str | None, optional
        x geometry, by default "Lon"
    y_col : str | None, optional
        y geometry, by default "Lat"
    adhoc_crs : _type_, optional
        CRS of adhoc table, by default "EPSG:4326"

    Returns
    -------
    gpd.GeoDataFrame
        Updated gages
    """
    gdf_adhoc = gpd.read_file(adhoc_path)
    gdf_adhoc = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy(x=gdf_adhoc[x_col], y=gdf_adhoc[y_col]),
        data={"site_no": gdf_adhoc[adhoc_gage_id]},
        crs=adhoc_crs,
    )
    gdf_adhoc = gdf_adhoc.to_crs(gages.crs)
    gdf_adhoc["status"] = "adhoc_lakes"
    # drop OT suffix from Ohio RFC gages
    gdf_adhoc["site_no"] = np.where(
        gdf_adhoc["site_no"].str[-2:] == "OT", gdf_adhoc["site_no"].str[:-2], gdf_adhoc["site_no"]
    )
    gages = pd.concat([gages, gdf_adhoc])
    logger.info(
        f"Added {len(gdf_adhoc)} gages from from adhoc lakes layer. Some may be dropped if outside domain."
    )
    return gages


def merge_canadian_great_lakes(
    gages: gpd.GeoDataFrame, great_lakes_cfg: GreatLakesMapping
) -> gpd.GeoDataFrame:
    """Add Canadian Great Lakes (Erie, Ontario) gages from predefined values in GreatLakesMapping config.

    Parameters
    ----------
    gages : gpd.GeoDataFrame
        Master Table
    great_lakes_cfg : GreatLakesMapping
        GreatLakesMapping config from schemas

    Returns
    -------
    gpd.GeoDataFrame
        Updated gages
    """
    erie = great_lakes_cfg.erie
    ontario = great_lakes_cfg.ontario
    gdf = gpd.GeoDataFrame(
        data={
            "site_no": [erie.site_no, ontario.site_no],
            "status": ["canada_great_lakes", "canada_great_lakes"],
        },
        geometry=gpd.points_from_xy(
            x=[erie.lon, ontario.lon], y=[erie.lat, ontario.lat]
        ),
        crs=4326,
    )
    gdf = gdf.to_crs(gages.crs)
    gages = pd.concat([gages, gdf])
    logger.info("Added Canadian Great Lakes")
    return gages


def merge_usbr(
    gages: gpd.GeoDataFrame, usbr_path: Path, usbr_gage_id: str = "locId"
) -> gpd.GeoDataFrame:
    """Merge USBR gages from gpkg

    Parameters
    ----------
    gages : gpd.GeoDataFrame
        Master table
    usbr_path : Path
        USBR path
    usbr_gage_id : str, optional
        ID col, by default "locId"

    Returns
    -------
    gpd.GeoDataFrame
        Updated gages
    """
    gdf = gpd.read_file(usbr_path)
    gdf = gdf.to_crs(gages.crs)
    gdf = (
        gdf[[usbr_gage_id, "geometry"]].copy().rename(columns={usbr_gage_id: "site_no"})
    )
    gdf["status"] = "USBR"

    # ensure no collisions with usgs
    gdf["site_no"] = "usbr-" + gdf["site_no"].astype(pd.Int64Dtype()).astype(str)

    gages = pd.concat([gages, gdf])
    logger.info(
        f"Added {len(gdf)} gages from from USBR layer. Some may be dropped if outside domain."
    )
    return gages


def merge_usace(
    gages: gpd.GeoDataFrame, usace_path: Path, usace_gage_id: str = "location"
) -> gpd.GeoDataFrame:
    """Adds USACE gages associated with reservoirs.

    Parameters
    ----------
    gages : gpd.GeoDataFrame
        Master table
    usace_path : Path
        USACE path
    usace_gage_id : str, optional
        ID col, by default 'location'

    Returns
    -------
    gpd.GeoDataFrame
        Updated gages
    """
    gdf = gpd.read_file(usace_path)
    gdf = gdf.to_crs(gages.crs)
    gdf = (
        gdf[[usace_gage_id, "geometry"]]
        .copy()
        .rename(columns={usace_gage_id: "site_no"})
    )
    gdf["status"] = "USACE"

    gages = pd.concat([gages, gdf])
    logger.info(
        f"Added {len(gdf)} gages from from USACE layer. Some may be dropped if outside domain."
    )
    return gages
