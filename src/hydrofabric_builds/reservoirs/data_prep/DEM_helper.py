from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.mask import mask
from shapely.geometry import mapping


def mean_dem_over_polygon(src: rasterio.io.DatasetReader, geom: list) -> float:
    """
    Mean DEM value inside a single polygon.

    Parameters
    ----------
    src : open rasterio dataset
    geom : shapely geometry (polygon)

    Returns
    -------
    float (mean elevation) or np.nan if no valid cells.
    """
    shapes = [mapping(geom)]
    try:
        data, _ = mask(src, shapes=shapes, crop=True, filled=True)
    except ValueError:
        # e.g. polygon outside raster
        return float("nan")

    arr = data[0].astype(float)
    nodata = src.nodata
    mask_valid = ~np.isnan(arr)
    if nodata is not None:
        mask_valid &= arr != nodata
    if not np.any(mask_valid):
        return float("nan")
    return float(arr[mask_valid].mean())


def extract_elev_at_points(dem_path: str | Path, pts: gpd.GeoDataFrame) -> np.ndarray:
    """Sample DEM at point locations; returns 1D array of elevations."""
    pts = pts.copy()
    with rasterio.open(dem_path) as src:
        if pts.crs is None:
            raise ValueError("points must have a CRS")
        if src.crs is not None and pts.crs.to_string().upper() != src.crs.to_string().upper():
            pts = pts.to_crs(src.crs)
        coords = [(geom.x, geom.y) for geom in pts.geometry]
        samples = list(src.sample(coords))
        arr = np.array([s[0] if len(s) else np.nan for s in samples], dtype=float)
        if src.nodata is not None:
            arr[arr == src.nodata] = np.nan
        return arr


def _add_area_sqkm(gdf: gpd.GeoDataFrame, area_crs: str = "EPSG:5070") -> pd.Series:
    """Compute area in km² for polygons."""
    if gdf.empty:
        return pd.Series([], dtype="float64")

    if gdf.crs is None:
        raise ValueError("Geometry has no CRS; cannot compute area.")

    g_proj = gdf.to_crs(area_crs)
    # area in m² -> km²
    return g_proj.geometry.area.astype("float64") / 1_000_000.0
