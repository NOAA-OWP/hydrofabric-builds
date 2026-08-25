from __future__ import annotations

import logging
import os
import time
from typing import Any, Final
from urllib.parse import urljoin

import geopandas as gpd
import httpx
from shapely.errors import GEOSException
from shapely.ops import unary_union

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# ---- NLDI endpoints (single source of truth) ----
NLDI_API_BASE: Final[str] = os.getenv(
    "NLDI_API_BASE",
    "https://api.water.usgs.gov/nldi/linked-data/",
)
NLDI_LABS_BASE: Final[str] = os.getenv(
    "NLDI_LABS_BASE",
    "https://labs.waterdata.usgs.gov/api/nldi/linked-data/",
)

# Path templates (no leading slash so urljoin works predictably)
NLDI_BASIN_BY_SITE_TPL: Final[str] = "nwissite/USGS-{site_no}/basin"
NLDI_POSITION_TPL: Final[str] = "position?coords=POINT({lon} {lat})"
NLDI_BASIN_BY_SNAPPED_TPL: Final[str] = "{source}/{identifier}/basin"


def _nldi_url(base: str, path: str) -> str:
    # Ensure base ends with '/' exactly once; path has no leading '/'
    if not base.endswith("/"):
        base = base + "/"
    return urljoin(base, path)


# ---------------------------
# NLDI helpers
# ---------------------------
def _safe_union_one(gdf: gpd.GeoDataFrame, grid_size: float | None = 0.0) -> gpd.GeoDataFrame:
    """
    Return a single-row GeoDataFrame whose geometry is the union of gdf.geometry.

    Repairs invalid geometries before union. Uses grid_size to stabilize.
    """
    if gdf.empty:
        return gpd.GeoDataFrame(geometry=[], crs=gdf.crs)

    geom = gdf.geometry

    # 1) Repair invalids: make_valid (Shapely 2) or buffer(0) fallback
    try:
        # GeoPandas >= 0.14 exposes .make_valid() vectorized
        geom = geom.make_valid()
    except AttributeError:
        try:
            geom = geom.buffer(0)
        except (GEOSException, ValueError, TypeError):
            # If even buffer(0) fails, keep original and let later steps try
            pass

    # 2) Try robust vectorized union_all first (GeoPandas/Shapely 2)
    try:
        # grid_size helps snap nearly-coincident vertices
        uni = geom.union_all(grid_size=grid_size)
        return gpd.GeoDataFrame(geometry=[uni], crs=gdf.crs)
    except (GEOSException, ValueError, TypeError):
        # 3) Fall back to unary_union (older Shapely path)
        try:
            uni = unary_union(list(geom))
            return gpd.GeoDataFrame(geometry=[uni], crs=gdf.crs)
        except (GEOSException, ValueError, TypeError):
            # 4) Absolute fallback: just take the largest valid polygon
            geom_valid = geom[geom.is_valid]
            if geom_valid.empty:
                # return empties with CRS preserved
                return gpd.GeoDataFrame(geometry=[], crs=gdf.crs)
            largest = max(geom_valid, key=lambda g: g.area)
            return gpd.GeoDataFrame(geometry=[largest], crs=gdf.crs)


def _nldi_get(
    url: str, *, timeout: int = 30, retries: int = 3, backoff: float = 0.75
) -> dict[str, Any] | None:
    """
    GET a URL expected to return JSON. Returns dict on success, None on any failure.

    Retries a few times with backoff for transient errors.
    """
    # Reuse a client for connection pooling; follow redirects to be resilient.
    with httpx.Client(
        timeout=timeout, follow_redirects=True, headers={"Accept": "application/json"}
    ) as client:
        for attempt in range(1, retries + 1):
            try:
                r = client.get(url)
            except (httpx.TimeoutException, httpx.RequestError):
                # Network / DNS / SSL / timeout
                if attempt == retries:
                    return None
                time.sleep(backoff * attempt)
                continue

            # Handle 404 quietly (no retries)
            if r.status_code == 404:
                return None

            # Basic status checks
            # Retry a bit on transient server/client-throttle statuses
            if r.status_code in (429, 500, 502, 503, 504):
                if attempt < retries:
                    time.sleep(backoff * attempt)
                    continue
                return None

            # Any other non-200 -> fail without retry
            if r.status_code != 200:
                return None

            # Content-type sanity check
            ctype = r.headers.get("Content-Type", "")
            if "json" not in ctype.lower():
                # Some endpoints send JSON without correct content-type; try anyway
                try:
                    return r.json()
                except ValueError:
                    return None

            # Parse JSON
            try:
                return r.json()
            except ValueError:
                # Empty or malformed body
                if attempt < retries:
                    time.sleep(backoff * attempt)
                    continue
                return None

    return None


def nldi_basin_by_site(site_no: str) -> gpd.GeoDataFrame | None:
    """
    Get upstream basin GeoJSON from NLDI by USGS gage site number.

    Returns GeoDataFrame in EPSG:4326 or None if not found.
    https://api.water.usgs.gov/nldi/linked-data/nwissite/USGS-08279500/basin .
    """
    path = NLDI_BASIN_BY_SITE_TPL.format(site_no=site_no)
    url = _nldi_url(NLDI_API_BASE, path)
    js = _nldi_get(url)
    if not js:
        return None
    gdf = gpd.GeoDataFrame.from_features(js["features"], crs="EPSG:4326")
    # Some basins come as multipart; dissolve to one row for area calc
    if gdf.empty:
        return None
        # replace: gdf = gdf.dissolve().reset_index(drop=True)
    gdf = _safe_union_one(gdf, grid_size=1e-4).reset_index(drop=True)
    return gdf


def nldi_basin_by_position(lon: float, lat: float) -> gpd.GeoDataFrame | None:
    """
    Use NLDI 'position' to snap (lon,lat) to network, then fetch basin.

    Returns GeoDataFrame in EPSG:4326 or None if not found.
    """
    # 1) Snap to a network feature
    pos_url = _nldi_url(NLDI_LABS_BASE, NLDI_POSITION_TPL.format(lon=lon, lat=lat))
    pos = _nldi_get(pos_url)
    if not pos or "features" not in pos or not pos["features"]:
        return None

    feat = pos["features"][0]  # Take the closest
    props = feat.get("properties", {})
    src = props.get("source")
    fid = props.get("identifier")
    if not (src and fid):
        return None

    # 2) Basin of that snapped network id
    basin_url = f"https://labs.waterdata.usgs.gov/api/nldi/linked-data/{src}/{fid}/basin"
    js = _nldi_get(basin_url)
    if not js:
        return None
    gdf = gpd.GeoDataFrame.from_features(js["features"], crs="EPSG:4326")
    if gdf.empty:
        return None
    gdf = _safe_union_one(gdf, grid_size=1e-4).reset_index(drop=True)
    return gdf
