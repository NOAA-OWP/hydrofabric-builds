from pathlib import Path

import geopandas as gpd
import pandas as pd


def txdot_read_file(path: Path | str, src_crs: str = "EPSG:4326") -> gpd.GeoDataFrame:
    """
    Reading  TXDOT txt file

    :param path: the path to TXDOT file downloaded from USGS website
    :return: geo dataframe file
    """
    # 1) Read tab-delimited RDB, skipping comment lines
    df = pd.read_csv(
        path,
        sep="\t",
        comment="#",
        dtype=str,
        engine="python",
        na_values=["", " ", "."],
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

    # 6) Build GeoDataFrame (WGS84 lon/lat)
    gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df["dec_long_va"], df["dec_lat_va"]),
        crs=src_crs,
    )
    return gdf
