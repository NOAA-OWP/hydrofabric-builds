from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd

logger = logging.getLogger(__name__)


def fill_usgs_basin_from_csv(
    gages: gpd.GeoDataFrame,
    csv_path: str | Path,
    gage_col_csv: str = "gage",
    area_col_csv: str = "area_sqkm",
    site_col_gdf: str = "site_no",
    basin_col_gdf: str = "USGS_basin_km2",
    source_col_gdf: str = "source",
    source_value_for_csv: str = "CIROH_UA",
) -> gpd.GeoDataFrame:
    """
    Fill missing USGS_basin_km2 in `gages` using areas from a CSV.

    - Match `gages[site_col_gdf]` to CSV column `gage_col_csv`
    - If `gages[basin_col_gdf]` is NaN and CSV has `area_col_csv`,
      fill `gages[basin_col_gdf]` with that value.
    - For those rows that are filled from the CSV, set `source` to `source_value_for_csv`.

    Returns a *copy* of the input GeoDataFrame with updated values.
    """
    gages = gages.copy()

    # Read CSV and standardize column names
    csv_df = pd.read_csv(csv_path, dtype={gage_col_csv: "string"})
    csv_df = csv_df[[gage_col_csv, area_col_csv]].rename(
        columns={gage_col_csv: site_col_gdf, area_col_csv: "_area_from_csv"}
    )

    # Make sure join keys are comparable (both as strings)
    gages[site_col_gdf] = gages[site_col_gdf].astype("string")

    # Left join: keep all gages, attach area from CSV where available
    merged = gages.merge(csv_df, on=site_col_gdf, how="left")

    # Mask: where USGS_basin_km2 is missing and CSV has a value
    mask = merged[basin_col_gdf].isna() & merged["_area_from_csv"].notna()

    # Fill the missing values
    merged.loc[mask, basin_col_gdf] = merged.loc[mask, "_area_from_csv"]

    # Update source for filled rows
    merged.loc[mask, source_col_gdf] = source_value_for_csv

    # Drop helper column
    merged = merged.drop(columns=["_area_from_csv"])

    # Keep GeoDataFrame type & geometry
    merged = gpd.GeoDataFrame(merged, geometry="geometry", crs=gages.crs)

    logger.info("gages: CIROH-UA source file for catchment upstream area was implemented")
    return merged
