from __future__ import annotations

import logging
from pathlib import Path
from time import perf_counter

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterstats import zonal_stats

logger = logging.getLogger(__name__)


def build_osm_wb_elevs(
    dem_path: str | Path,
    ref_reservoirs_path: str | Path,
    osm_ref_wb_path: str | Path,
    out_gpkg: str | Path,
    max_waterbody_nearest_dist_m: float,
    min_area_sqkm: float,
    work_crs: str = "EPSG:5070",
    elev_calc: bool = True,
    res_keep: list[str] | None = None,
) -> pd.DataFrame:
    """
    Python equivalent of the OSM waterbody chunk:

      - find OSM WBs used by candidate dams,
      - compute mean DEM elevation per WB,
      - compute area in km²,
      - write `osm_wb_elevs.gpkg`.

    :param dem_path: .vrt dem path
    :param ref_reservoirs_path: Path to the file "reference_reservoirs"
    :param osm_ref_wb_path: path to the file "osm_dams_all"
    :param out_gpkg: output directory
    :param max_waterbody_nearest_dist_m: maximum distance between points and waterbodies
    :param min_area_sqkm: minimum waterbody area to be considered
    :param elev_calc: flag to whether calculate elevation
    :param res_keep: list of reference reservoir `dam_id`s to keep
    :return: modified osm dams file `osm_wb_elevs.gpkg`
    """
    res = gpd.read_file(ref_reservoirs_path)
    res_keep = res_keep if res_keep is not None else []
    da = res[
        (res["distance_to_fp_m"] < max_waterbody_nearest_dist_m) & (res["wb_areasqkm"] >= min_area_sqkm)
        | (res["dam_id"].isin(res_keep))
    ].copy()

    # ids = da["osm_ww_poly"].dropna().unique().tolist()  # R code has this line but does not match with osm dam
    ids = da["osm_dam_lines"].dropna().unique().tolist()
    if not ids:
        raise ValueError("No osm_ww_poly IDs found in candidate reservoirs.")

    osm_wbs = gpd.read_file(osm_ref_wb_path)
    osm_wbs = osm_wbs[osm_wbs["osm_id"].isin(ids)].copy()
    if osm_wbs.empty:
        raise ValueError("No OSM waterbodies matched candidate osm_id.")

    logger.info("Calculating zonal stats for OSM waterbodies")
    t0 = perf_counter()
    with rasterio.open(dem_path) as src:
        dem_crs = src.crs
        if dem_crs is None:
            raise ValueError("DEM has no CRS.")
        if osm_wbs.crs is None:
            raise ValueError("osm_wbs has no CRS; cannot reproject.")

        if osm_wbs.crs.to_string().upper() != dem_crs.to_string().upper():
            osm_wbs = osm_wbs.to_crs(dem_crs)

        if elev_calc:
            stats = zonal_stats(
                vectors=osm_wbs,  # GeoDataFrame or shapes
                raster=str(dem_path),  # path to your DEM
                stats="mean",
                nodata=src.nodata if src.nodata is not None else None,
                all_touched=False,  # or True if you want a more inclusive mask
            )
            osm_wbs["osm_wb_elev"] = [s["mean"] for s in stats]
        else:
            osm_wbs["osm_wb_elev"] = np.nan
    logger.info(f"Zonal stats for OSM waterbodies took {round((perf_counter() - t0) / 60, 2)} min")

    # Compute area in km² in 5070 (default
    osm_work_crs = osm_wbs.to_crs(work_crs)
    osm_area_sqkm = osm_work_crs.geometry.area.to_numpy() / 1e6

    out = gpd.GeoDataFrame(
        {
            "osm_id": osm_wbs["osm_id"].to_numpy(),
            "osm_wb_elev": osm_wbs["osm_wb_elev"].to_numpy(),
            "osm_area_sqkm": osm_area_sqkm,
        },
        geometry=osm_wbs.geometry,
        crs=osm_wbs.crs,
    )

    out_path = Path(out_gpkg)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_file(out_path, driver="GPKG")
    return out
