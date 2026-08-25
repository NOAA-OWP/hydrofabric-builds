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


def build_ref_wb_elevs(
    dem_path: str | Path,
    ref_reservoirs_path: str | Path,
    ref_wb_path: str | Path,
    out_gpkg: str | Path,
    max_waterbody_nearest_dist_m: float,
    min_area_sqkm: float,
    work_crs: str = "EPSG:5070",
    elev_calc: bool = True,
) -> pd.DataFrame:
    """
    Python equivalent of the first chunk:

      - filter res to RFC-DA candidates,
      - get associated reference waterbodies,
      - compute mean DEM elevation per WB,
      - write `ref_wb_elevs.parquet`.

    :param dem_path: path to .vrt dem
    :param ref_reservoirs_path: pathto the file "reference_reservoirs"
    :param ref_wb_path: path to the file "reference_waterbodies"
    :param out_gpkg: output path to save new file
    :param max_waterbody_nearest_dist_m: maximum distance between points and waterbodies
    :param min_area_sqkm: minimum waterbody area to be considered
    :param elev_calc: flag to whether calculate elevation
    :return: modified reference_waterbodies file
    """
    # 1) Load reference reservoirs (res) and filter to RFC-DA candidates
    res = gpd.read_file(ref_reservoirs_path)
    da = res[
        (res["distance_to_fp_m"] < max_waterbody_nearest_dist_m) & (res["wb_areasqkm"] >= min_area_sqkm)
    ].copy()

    ids = da["ref_fab_wb"].dropna().unique().tolist()
    if not ids:
        raise ValueError("No ref_fab_wb IDs found in candidate reservoirs.")

    # 2) Read reference waterbodies and subset
    ref_wbs = gpd.read_file(ref_wb_path, layer="reference_waterbodies")
    ref_wbs = ref_wbs[ref_wbs["comid"].isin(ids)].copy()
    if ref_wbs.empty:
        raise ValueError("No reference waterbodies matched candidate comid IDs.")

    # 3) Reproject to DEM CRS if needed and compute mean elevation
    logger.info("Calculating zonal stats for reference reservoirs")
    t0 = perf_counter()
    with rasterio.open(dem_path) as src:
        dem_crs = src.crs
        if dem_crs is None:
            raise ValueError("DEM has no CRS.")
        if ref_wbs.crs is None:
            raise ValueError("ref_wbs has no CRS; cannot reproject.")

        if ref_wbs.crs.to_string().upper() != dem_crs.to_string().upper():
            ref_wbs = ref_wbs.to_crs(dem_crs)

        if elev_calc:
            stats = zonal_stats(
                vectors=ref_wbs,  # GeoDataFrame or shapes
                raster=str(dem_path),  # path to your DEM
                stats="mean",
                nodata=src.nodata if src.nodata is not None else None,
                all_touched=False,  # or True if you want a more inclusive mask
            )
            ref_wbs["ref_elev"] = [s["mean"] for s in stats]
        else:
            ref_wbs["ref_elev"] = np.nan
    logger.info(f"Zonal stats for reference reservoirs took {round((perf_counter() - t0) / 60, 2)} min")

    # area_sqkm assumed present; if not, compute in 5070 in default
    if "area_sqkm" in ref_wbs.columns:
        ref_area_sqkm = ref_wbs["area_sqkm"].astype("float64")
    else:
        ref_wbs_work_crs = ref_wbs.to_crs(work_crs)
        ref_area_sqkm = ref_wbs_work_crs.geometry.area.to_numpy() / 1e6

    out = gpd.GeoDataFrame(
        {
            "comid": ref_wbs["comid"].to_numpy(),
            "ref_area_sqkm": ref_area_sqkm,
            "ref_elev": ref_wbs["ref_elev"].to_numpy(),
        },
        geometry=ref_wbs.geometry,
        crs=ref_wbs.crs,
    )

    out_path = Path(out_gpkg)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_file(out_path, driver="GPKG")
    return out
