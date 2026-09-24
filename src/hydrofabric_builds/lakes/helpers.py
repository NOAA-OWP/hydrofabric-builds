from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterstats import zonal_stats


def polygon_elevation(dem_path: str | Path, polygons: gpd.GeoDataFrame, field_name: str) -> gpd.GeoDataFrame:
    """Zonal stats of a DEM with column appended to input dataframe with field_name specified"""
    with rasterio.open(dem_path) as src:
        if src.crs is None:
            raise ValueError("DEM has no CRS.")
        if polygons.crs is None:
            raise ValueError("ref_wbs has no CRS; cannot reproject.")
        if polygons.crs.to_string().upper() != src.crs.to_string().upper():
            polygons = polygons.to_crs(src.crs)

        stats = zonal_stats(
            vectors=polygons,  # GeoDataFrame or shapes
            raster=str(dem_path),  # path to your DEM
            stats="mean",
            nodata=src.nodata if src.nodata is not None else None,
            all_touched=False,  # or True if you want a more inclusive mask
        )
        polygons[field_name] = [s["mean"] for s in stats]

    return polygons


def point_elevation(dem_path: str | Path, gdf: gpd.GeoDataFrame) -> np.ndarray:
    """Sample DEM at point locations; returns 1D array of elevations."""
    gdf = gdf.copy()
    with rasterio.open(dem_path) as src:
        if gdf.crs is None:
            raise ValueError("points must have a CRS")
        if src.crs is not None and gdf.crs.to_string().upper() != src.crs.to_string().upper():
            gdf = gdf.to_crs(src.crs)
        coords = [(geom.x, geom.y) for geom in gdf.geometry]
        samples = list(src.sample(coords))
        arr = np.array([s[0] if len(s) else np.nan for s in samples], dtype=float)
        if src.nodata is not None:
            arr[arr == src.nodata] = np.nan
    return arr
