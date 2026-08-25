from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import pandas as pd

from hydrofabric_builds.streamflow_gauges.usgs_NLDI_API import (
    nldi_basin_by_position,
    nldi_basin_by_site,
)

logger = logging.getLogger(__name__)


@dataclass
class NLDIRecord:
    """a dataclass for organizing the area values and geometries we get fro USGS API"""

    site_no: str
    area_km2: float | None
    source: str  # 'nldi_site' | 'nldi_position' | 'none'
    geom: object | None  # polygon/multipolygon or None (in EPSG:4326)


def _compute_area_km2(basin_4326: gpd.GeoDataFrame, work_crs: str, usgs_crs: str) -> float:
    if basin_4326.empty:
        return 0.0
    if basin_4326.crs is None:
        basin_4326 = basin_4326.set_crs(usgs_crs)
    dissolved = basin_4326.to_crs(work_crs).dissolve().geometry.iloc[0]
    return float(dissolved.area / 1_000_000.0)


def _fetch_one(site_no: str | None, lon: float, lat: float, work_crs: str, usgs_crs: str) -> NLDIRecord:
    # Try by site_no first
    source = "none"
    gdf = None
    if site_no:
        gdf = nldi_basin_by_site(site_no)
        if gdf is not None:
            source = "nldi_site"
    if gdf is None:
        gdf = nldi_basin_by_position(lon, lat)
        if gdf is not None:
            source = "nldi_position"

    area = None
    geom = None
    if gdf is not None and not gdf.empty:
        # dissolve to a single basin geometry in EPSG:4326
        if gdf.crs is None:
            gdf = gdf.set_crs(usgs_crs)
        geom = gdf.dissolve().to_crs(usgs_crs).geometry.iloc[0]
        area = _compute_area_km2(gdf, work_crs, usgs_crs)

    return NLDIRecord(
        site_no=str(site_no) if site_no is not None else "", area_km2=area, source=source, geom=geom
    )


def build_nldi_cache(
    gages: gpd.GeoDataFrame,
    out_gpkg: str | Path,
    status_col: str = "status",
    site_no_col: str = "site_no",
    keep_status: tuple | list = ("USGS-active", "USGS-discontinued", "TXDOT"),
    work_crs: str = "EPSG:5070",
    usgs_crs: str = "EPSG:4326",
    use_threads: bool = True,
    max_workers: int = 24,  # if 1 → serial
    layer_polys: str = "NLDI_upstream_basins",
    layer_points: str = "NLDI_sites",  # <-- NEW: point layer name
) -> None:
    """
    Building nldi_upstream_area file by asking USGS API for the upstream area of gages.

    :param gages: gages gpkg
    :param out_gpkg: output file path
    :param status_col: shows status of the gages ex: UAGS-active, TXDOT, etc.
    :param site_no_col: columns name for site number
    :param keep_status: calling API for certain statuses. for instance we do not do it for CADWR
    :param work_crs: and area-equal crs
    :param usgs_crs: usgs crs.
    :param use_threads: for parallel run (True). otherwise (False)
    :param max_workers: number of wirkers if use_threads==True
    :param layer_polys: layer name for the polygons
    :param layer_points: layer names for the points
    :return: modified gages gpkg
    """
    if gages.crs is None:
        raise ValueError("`gages` must have a CRS (expected EPSG:4326).")

    # filter candidates we’ll query
    cand = gages[gages[status_col].isin(keep_status)].copy()
    g4326 = cand if cand.crs.to_string().upper() == usgs_crs else cand.to_crs(usgs_crs)
    if g4326.empty:
        # create empty layers for both polygons and points
        empty_polys = gpd.GeoDataFrame(
            {"site_no": [], "USGS_basin_km2": [], "source": []},
            geometry=gpd.GeoSeries([], crs=usgs_crs),
            crs=usgs_crs,
        )
        empty_polys.to_file(out_gpkg, layer=layer_polys, driver="GPKG")
        empty_points = gpd.GeoDataFrame(
            {"site_no": [], "status": []},
            geometry=gpd.GeoSeries([], crs=usgs_crs),
            crs=usgs_crs,
        )
        empty_points.to_file(out_gpkg, layer=layer_points, driver="GPKG", mode="a")
        return

    # normalize IDs as strings
    g4326[site_no_col] = g4326[site_no_col].astype(str).str.strip()

    # unique jobs (site_no, lon, lat) we’ll fetch
    jobs = g4326.assign(lon=g4326.geometry.x, lat=g4326.geometry.y)[
        [site_no_col, "lon", "lat"]
    ].drop_duplicates(subset=[site_no_col, "lon", "lat"])

    records: list[NLDIRecord] = []

    # --------- SERIAL ----------
    if (not use_threads) or max_workers <= 1:
        for _, row in jobs.iterrows():
            try:
                rec = _fetch_one(
                    row[site_no_col] if row[site_no_col] else None,
                    float(row["lon"]),
                    float(row["lat"]),
                    work_crs,
                    usgs_crs,
                )
            except (TypeError, ValueError) as e:
                logger.error(f"nothing found to serialize the gages API run as the error is: {e}")
                rec = NLDIRecord(site_no=str(row[site_no_col]), area_km2=None, source="none", geom=None)
            records.append(rec)

    # --------- THREADED ----------
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futs = {
                pool.submit(
                    _fetch_one,
                    row[site_no_col] if row[site_no_col] else None,
                    float(row["lon"]),
                    float(row["lat"]),
                    work_crs,
                    usgs_crs,
                ): str(row[site_no_col])
                for _, row in jobs.iterrows()
            }
            for fut in as_completed(futs):
                try:
                    rec = fut.result()
                except (TypeError, ValueError) as e:
                    logger.error(f"nothing found to prallelize the gages API run as the error is: {e}")
                    rec = NLDIRecord(site_no=futs[fut], area_km2=None, source="none", geom=None)
                records.append(rec)

    # ----- Build polygon layer (basins) -----
    df_polys = pd.DataFrame(
        {
            "site_no": [r.site_no for r in records],
            "USGS_basin_km2": [r.area_km2 for r in records],
            "source": [r.source for r in records],
        }
    )
    # polygons can be None; GeoPandas supports missing geometry
    geom_polys = gpd.GeoSeries([r.geom for r in records], crs=usgs_crs, name="geometry")
    gdf_polys = gpd.GeoDataFrame(df_polys, geometry=geom_polys, crs=usgs_crs)

    # Write (new file or overwrite layer); we’ll append the points layer next
    gdf_polys.to_file(out_gpkg, layer=layer_polys, driver="GPKG", overwrite=True)

    # ----- Build point layer (sites) -----
    # one point per site_no (use the filtered g4326 so we keep only relevant statuses)
    # keep one geometry per site_no (first occurrence if duplicates)
    sites_unique = (
        g4326[[site_no_col, status_col, "geometry"]]
        .drop_duplicates(subset=[site_no_col])
        .rename(columns={site_no_col: "site_no", status_col: "status"})
    )
    # attach fetched area/source if you want them on the points too:
    sites_unique = sites_unique.merge(
        df_polys[["site_no", "USGS_basin_km2", "source"]],
        on="site_no",
        how="left",
    )
    gdf_points = gpd.GeoDataFrame(sites_unique, geometry="geometry", crs=usgs_crs)

    # Append the points layer to the same GPKG
    gdf_points.to_file(out_gpkg, layer=layer_points, driver="GPKG", mode="a")


def attach_nldi_cache(
    gages: gpd.GeoDataFrame,
    cache_gpkg: str | Path,
    layer_polys: str = "basin_polys",
    site_no_col: str = "site_no",
) -> gpd.GeoDataFrame:
    """
    If the nldi file (cache) exists, attach 'USGS_basin_km2' to gages.

    - Keeps gages' original geometry.
    - Adds columns: 'USGS_basin_km2' and 'nldi_source'.
    """
    p = Path(cache_gpkg)
    if not p.exists():
        return gages

    cache = gpd.read_file(p, layer=layer_polys)  # geometry = basin (may be null)
    cache = cache[[site_no_col, "USGS_basin_km2", "source", "geometry"]].rename(
        columns={"geometry": "nldi_basin_geom"}
    )

    # Merge attributes only; I don't overwrite gages geometry
    g = gages.copy()
    g[site_no_col] = g[site_no_col].astype(str).str.strip()
    cache[site_no_col] = cache[site_no_col].astype(str).str.strip()

    g = g.merge(cache.drop(columns=["nldi_basin_geom"]), on=site_no_col, how="left")

    return g
