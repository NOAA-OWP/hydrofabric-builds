# tests/test_hydraulics.py

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import Point, Polygon

from hydrofabric_builds.reservoirs.data_prep.DEM_helper import extract_elev_at_points, mean_dem_over_polygon
from hydrofabric_builds.reservoirs.data_prep.hydraulics import populate_hydraulics


def test_populate_hydraulics_single_row_example() -> None:
    """Replicate the example from the original R docstring."""
    df = pd.DataFrame(
        {
            "dam_id": ["ls-1025"],
            "nidid": ["NID123"],
            "dam_type": ["Concrete"],
            "spillway_type": ["Ogee gated"],
            "structural_height": [22.0],
            "dam_length": [85.0],
            "ref_area_sqkm": [0.50],
            "surface_area": [np.nan],
            "ref_elev": [1515.0],
            "osm_wb_elev": [np.nan],
            "dam_elev": [1500.0],
            # add storage so storage_m3 has correct length
            "nid_storage": [1000.0],  # acre-ft (value arbitrary for this test)
            "normal_storage": [np.nan],
            "max_storage": [np.nan],
            # optional extras, should be safely ignored if missing
            "osm_ww_poly": [None],
            "ref_fab_wb": [None],
        }
    )

    out = populate_hydraulics(df)

    assert len(out) == 1
    row = out.iloc[0]

    # effective height (H_m) = structural_height
    assert pytest.approx(row["H_m"], rel=1e-6) == 22.0

    # area: ref_area_sqkm * 1e6
    assert pytest.approx(row["LkArea"], rel=1e-6) == 0.5 * 1_000_000.0

    # WeirL (and Dam_Length) should fall back to dam_length
    assert pytest.approx(row["WeirL"], rel=1e-6) == 85.0
    assert pytest.approx(row["Dam_Length"], rel=1e-6) == 85.0

    # WeirC: "ogee" → 1.7
    assert pytest.approx(row["WeirC"], rel=1e-6) == 1.7

    # DEM-based elevations:
    # wb = ref_elev = 1515, base = dam_elev = 1500, H = 22
    # WeirE = wb (first coalesce)
    assert pytest.approx(row["WeirE"], rel=1e-6) == 1515.0
    # LkMxE = wb + 0.10 * H = 1517.2
    assert pytest.approx(row["LkMxE"], rel=1e-6) == 1515.0 + 0.10 * 22.0
    # OrficeE = base + 0.15 * H = 1503.3
    assert pytest.approx(row["OrficeE"], rel=1e-6) == 1500.0 + 0.15 * 22.0

    # OrficeA uses height bin: 10 ≤ H < 30 → orficeA_med (default 0.9)
    assert pytest.approx(row["OrficeA"], rel=1e-6) == 0.9

    # ifd constant
    assert pytest.approx(row["ifd"], rel=1e-6) == 0.899


def test_populate_hydraulics_hazard_adjustment() -> None:
    """Check that use_hazard=True nudges parameters for high/significant hazard."""
    df = pd.DataFrame(
        {
            "dam_id": ["h1", "s1", "l1"],
            "nidid": ["N1", "N2", "N3"],
            "dam_type": ["Earth", "Earth", "Earth"],
            "spillway_type": ["", "", ""],
            "hazard": ["H", "S", "L"],
            "structural_height": [20.0, 20.0, 20.0],
            "dam_length": [100.0, 100.0, 100.0],
            "ref_area_sqkm": [1.0, 1.0, 1.0],
            "surface_area": [np.nan, np.nan, np.nan],
            "ref_elev": [1000.0, 1000.0, 1000.0],
            "osm_wb_elev": [np.nan, np.nan, np.nan],
            "dam_elev": [990.0, 990.0, 990.0],
            # add storage so storage_m3 has correct shape
            "nid_storage": [500.0, 500.0, 500.0],
            "normal_storage": [np.nan, np.nan, np.nan],
            "max_storage": [np.nan, np.nan, np.nan],
            "osm_ww_poly": [None, None, None],
            "ref_fab_wb": [None, None, None],
        }
    )

    out_no_hazard = populate_hydraulics(df, use_hazard=False)
    out_hazard = populate_hydraulics(df, use_hazard=True)

    # WeirL should increase slightly for High (H) and Significant (S), unchanged for Low
    base_L = out_no_hazard["WeirL"].iloc[0]
    L_H = out_hazard.loc[out_hazard["dam_id"] == "h1", "WeirL"].iloc[0]
    L_S = out_hazard.loc[out_hazard["dam_id"] == "s1", "WeirL"].iloc[0]
    L_L = out_hazard.loc[out_hazard["dam_id"] == "l1", "WeirL"].iloc[0]

    assert L_H > base_L
    assert L_S > base_L
    assert pytest.approx(L_L, rel=1e-6) == base_L

    # OrficeA should similarly be nudged up for H/S
    base_A = out_no_hazard["OrficeA"].iloc[0]
    A_H = out_hazard.loc[out_hazard["dam_id"] == "h1", "OrficeA"].iloc[0]
    A_S = out_hazard.loc[out_hazard["dam_id"] == "s1", "OrficeA"].iloc[0]
    A_L = out_hazard.loc[out_hazard["dam_id"] == "l1", "OrficeA"].iloc[0]

    assert A_H > base_A
    assert A_S > base_A
    assert pytest.approx(A_L, rel=1e-6) == base_A


def _make_test_raster(tmp_path: str | Path, data: float = 100.0, crs: str = "EPSG:5070") -> Path:
    """Create a tiny 2x2 GeoTIFF with constant or array data."""
    raster_path = Path(tmp_path) / "dem.tif"
    if np.isscalar(data):
        arr = np.full((1, 2, 2), float(data), dtype="float32")
    else:
        data_arr = np.asarray(data, dtype="float32")
        assert data_arr.shape == (2, 2)
        arr = data_arr.reshape(1, 2, 2)

    transform = from_origin(0.0, 2.0, 1.0, 1.0)  # origin (x0,y0), pixel size (1x1)

    with rasterio.open(
        raster_path,
        "w",
        driver="GTiff",
        height=arr.shape[1],
        width=arr.shape[2],
        count=1,
        dtype=arr.dtype,
        crs=crs,
        transform=transform,
    ) as dst:
        dst.write(arr)

    return raster_path


def test_mean_dem_over_polygon_constant(tmp_path: str | Path) -> None:
    """mean_dem_over_polygon should recover the constant DEM value over the polygon."""
    dem_path = _make_test_raster(tmp_path, data=123.0)

    # a polygon that fully covers the DEM extent
    poly = Polygon([(0, 0), (0, 2), (2, 2), (2, 0)])
    gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[poly], crs="EPSG:5070")

    with rasterio.open(dem_path) as src:
        val = mean_dem_over_polygon(src, gdf.geometry.iloc[0])

    assert pytest.approx(val, rel=1e-6) == 123.0


def test_extract_elev_at_points(tmp_path: str | Path) -> None:
    """extract_elev_at_points should sample correct DEM values at point locations."""
    # 2x2 raster with distinct values
    arr = np.array([[10.0, 20.0], [30.0, 40.0]], dtype="float32")
    dem_path = _make_test_raster(tmp_path, data=arr)

    # coordinates align to pixel centers (0.5,1.5), (1.5,1.5), etc.
    pts = gpd.GeoDataFrame(
        geometry=[
            Point(0.5, 1.5),  # top-left (10)
            Point(1.5, 1.5),  # top-right (20)
            Point(0.5, 0.5),  # bottom-left (30)
            Point(1.5, 0.5),  # bottom-right (40)
        ],
        crs="EPSG:5070",
    )

    vals = extract_elev_at_points(dem_path, pts)
    assert vals.shape == (4,)
    assert pytest.approx(vals[0], rel=1e-6) == 10.0
    assert pytest.approx(vals[1], rel=1e-6) == 20.0
    assert pytest.approx(vals[2], rel=1e-6) == 30.0
    assert pytest.approx(vals[3], rel=1e-6) == 40.0
