"""Creates a new SNOW17 raster combining NWS CONUS and source rasters to compute.

Sample call:
python tools/builds/attributes/snow17/snow17_mf.py --data_dir [your dir] --irr_march irr_0321_4km_5070.tif
 --irr_march_flat irr_0321_flat_4km_5070.tif --irr_december irr_1221_4km_5070.tif
 --forest forest_4km_5070.tif --wind wind_4km_5070.tif --conus_mfmax MFMAX.tif
 --conus_mfmin MFMIN.tif --conus_uadj UADJ.tif --irr_june irr_0622_4km_5070.tif
Input data:
Inputs in EPSG:5070 and 4km resolution are in s3://edfs-data/attributes/snow17-inputs/
Data sources:
irradiance files are created using the USGS 250m DEM, slope and aspect files downscaled to 4km and run
in QGIS using the GRASS r.sun.insoltime tool for March 21st (Julian day 80), December 21st (Julian day 355),
and June 22nd (Julian day 173).  irr_0321_flat_4km_5070 is March 21st irradiance with no topography and
was created using a flat DEM.  The forest percentage data is from https://data.niaid.nih.gov/resources?id=zenodo_10589729
Tiles covering North America were merged in QGIS and resampled to 4m and reprojected to EPSG:5070.
Wind data is from NCEP North American Regional Reanalysis (NARR) monthly long-term 10 meter average windspeed (wspd.10m.mon.ltm.nc) from https://psl.noaa.gov/data/gridded/data.narr.html
Data for March was used.  This data is 32km resolution and was resampled to 4km to match the other grids and reprojected to EPSG:5070.
"""

import argparse
import os
from pathlib import Path

import numpy as np
import rasterio
import rioxarray
import xarray as xr
from rasterio.enums import Resampling
from rasterio.transform import from_bounds


def melt_factors(
    data_dir: Path,
    irr_mar_file: Path,
    irr_dec_file: Path,
    irr_jun_file: Path,
    irr_mar_flat_file: Path,
    wind_file: Path,
    forest_file: Path,
) -> None:
    """Computes melt factors MFMAX and MFMIN

    Compute mfmax and mfmin using methodology in https://www.weather.gov/media/owp/oh/rfcdev/docs/Snow-17_A_priori_parm_estimates.pdf

    Parameters
    ----------
    data_ir : Path
        Path directory containing input rasters
    irr_mar_file: Path
        Filename of March irradiance raster
    irr_dec_file: Path
        Filename of December irradiance raster
    irr_jun_file: Path
        Filename of June irradiance raster
    irr_mar_flat_file: Path
        Filename of March irradiance with no topography raster
    wind_file: Path
        Filename of wind raster
    forest_file: Path
        Filename of forest percentage raster

    Returns
    -------
    None

    """
    # read rasters
    irr_mar = rioxarray.open_rasterio(Path.joinpath(data_dir, irr_mar_file))
    irr_march_flat = rioxarray.open_rasterio(Path.joinpath(data_dir, irr_mar_flat_file))
    irr_dec = rioxarray.open_rasterio(Path.joinpath(data_dir, irr_dec_file))
    irr_jun = rioxarray.open_rasterio(Path.joinpath(data_dir, irr_jun_file))
    forest = rioxarray.open_rasterio(Path.joinpath(data_dir, forest_file))
    wind = rioxarray.open_rasterio(Path.joinpath(data_dir, wind_file))

    # align forest and wind rasters to the irradiance rasters
    forest = forest.rio.reproject_match(irr_mar)
    wind = wind.rio.reproject_match(irr_mar)

    # get raster size info and create transform for new mfmax and mfmin rasters
    width = irr_mar.rio.width
    height = irr_mar.rio.height
    crs = irr_mar.rio.crs
    bounds = irr_mar.rio.bounds()
    transform = from_bounds(*bounds, width, height)

    # remove 3rd dimension
    irr_mar = np.squeeze(irr_mar.values)
    irr_dec = np.squeeze(irr_dec.values)
    irr_march_flat = np.squeeze(irr_march_flat.values)
    irr_jun = np.squeeze(irr_jun.values)
    forest = np.squeeze(forest.values)
    wind = np.squeeze(wind.values)

    # set any zeros in the irradiance arrays to nan
    irr_mar[irr_mar == 0] = np.nan
    irr_dec[irr_dec == 0] = np.nan
    irr_march_flat[irr_march_flat == 0] = np.nan
    irr_jun[irr_jun == 0] = np.nan
    # set -3.4e38 fill values in wind to nan
    wind[wind < 0] = np.nan

    # convert forest percent to decimal
    forest = forest / 100

    # get the terrain influence on incoming solar irradiation by taking the ratio of irradition with terrain
    # to irradiation with a flat surface for march 21 when snowmelt typically starts.
    rdb = irr_mar / irr_march_flat
    # computer the ratio of minimum annual irradiance to maximum annual irradiance.
    r = irr_dec / irr_jun
    # compute mfmax
    mfmax = ((1.03 * (1 - forest) * rdb) + 2.04 + (0.42 * wind)) / (2 * (r + 1))
    mfmax = np.squeeze(mfmax)
    # computer mfmin
    mfmin = mfmax * r

    # create mfmax raster
    mfmax_file = Path.joinpath(data_dir, "mfmax_temp.tif")
    with rasterio.open(
        mfmax_file,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype=mfmax.dtype,
        crs=crs,
        transform=transform,
    ) as dst:
        dst.write(mfmax, 1)

    # create mfmin raster
    mfmin_file = Path.joinpath(data_dir, "mfmin_temp.tif")
    with rasterio.open(
        mfmin_file,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype=mfmax.dtype,
        crs=crs,
        transform=transform,
    ) as dst:
        dst.write(mfmin, 1)


def uadj(data_dir: Path, wind_file: Path, forest_file: Path, irr_mar_file: Path) -> None:
    """Computes UADJ

    UADJ computed using parameterization described in https://www.weather.gov/media/owp/oh/rfcdev/docs/Snow-17_A_priori_parm_estimates.pdf
    UADJ = 0.002 * U, where U is the 6hr wind travel (distance wind travels in 6 hours) at 1 meter above the surface.
    I could not find the reference used in the NWS presentation for the wind speed adjustment, so I used the power law method,
    u_1m = u_10m * (1m/10m)^shear_exponent.  The shear exponent accounts for surface roughness and ranges from 0.1 for open
    land to 0.25 for dense forest(https://www.engineeringtoolbox.com/wind-shear-d_1215.html#:~:text=Wind%20slowed%20down%20at%20surface,=%207.1%20m/s).
    Using the forest fraction, I linearly mapped the values between 0% forest to 0.1 and 100% forest to
    0.25.  This might need to be revisited later.

    Parameters
    ----------
    data_ir : Path
        Path directory containing input rasters
    wind_file: Path
        Filename of wind raster
    forest_file: Path
        Filename of forest percentage raster
    irr_march_file: Path
        Filename of march irradiance file, used to align forest and wind rasters

    Returns
    -------
    None

    """
    # read rasters
    forest = rioxarray.open_rasterio(Path.joinpath(data_dir, forest_file))
    wind = rioxarray.open_rasterio(Path.joinpath(data_dir, wind_file))
    irr_march = rioxarray.open_rasterio(Path.joinpath(data_dir, irr_mar_file))

    # align forest and wind rasters to the irradiance rasters
    forest = forest.rio.reproject_match(irr_march)
    wind = wind.rio.reproject_match(irr_march)

    forest = np.squeeze(forest.values) / 100
    wind = np.squeeze(wind.values)
    wind[wind < 0] = np.nan

    # get information about the rasters and create a transform the new UADJ raster.
    width = irr_march.rio.width
    height = irr_march.rio.height
    crs = irr_march.rio.crs
    bounds = irr_march.rio.bounds()
    transform = from_bounds(*bounds, width, height)

    # compute uadj starting with the wind adjustment
    exp_min = 0.1
    exp_max = 0.25
    wind_adj = wind * (1 / 10) ** (exp_min + (exp_max - exp_min) * forest)
    # convert m/s to km/hr
    wind_adj_kmh = wind_adj * 3.6
    # get wind travel over 6 hours
    wind_travel = wind_adj_kmh * 6
    # compute uadj
    uadj = wind_travel * 0.002

    # create new uadj raster
    uadj_file = Path.joinpath(data_dir, "uadj_temp.tif")
    with rasterio.open(
        uadj_file,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype=uadj.dtype,
        crs=crs,
        transform=transform,
    ) as dst:
        dst.write(uadj, 1)


def combine_rasters(data_dir: Path, superconus_raster: Path, conus_raster: Path) -> None:
    """Combines the NWS CONUS raster and the superconus temporary raster

    Parameters
    ----------
    data_dir : Path
        Path to directory containing the superconus parameter raster and conus NWS raster
    superconus_raster: Path
        Filename of the superconus parameter raster
    conus_raster: list[float]
        Filename of the conus NWS paramter raster

    Returns
    -------
    None

    """
    # Read temporary superconus raster and NWM conus raster
    superconus = rioxarray.open_rasterio(os.path.join(data_dir, superconus_raster)).squeeze()
    conus = rioxarray.open_rasterio(os.path.join(data_dir, conus_raster)).squeeze()

    # resample superconus raster to the resolution of the NWS raster
    superconus = superconus.rio.reproject(
        conus.rio.crs, resolution=conus.rio.resolution(), resampling=Resampling.bilinear
    )

    # Expand the extent of the conus raster to match that of the superconus raster
    conus_matched = conus.rio.reproject_match(superconus)
    conus_matched_data = conus_matched.to_numpy()
    superconus_data = superconus.to_numpy()
    no_data = conus.rio.nodata

    # Create a numpy array where pixels with in CONUS contain the NWS value and those outside contain
    # the superconus value.
    new_array = np.where(conus_matched_data != no_data, conus_matched_data, superconus_data)

    # create new raster
    new_raster = xr.DataArray(
        new_array, coords=superconus.coords, dims=superconus.dims, attrs=superconus.attrs
    )

    # create results directory and save raster
    Path(f"{data_dir}/combined").mkdir(parents=True, exist_ok=True)
    new_filename = Path(f"{data_dir}/combined/{conus_raster}")
    new_raster.rio.to_raster(new_filename, tiled=True, compress="deflate")

    # remove temporary file
    Path(data_dir, superconus_raster).unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="A script to create rasters for Snow17 MFMAX, MFMIN and UADJ"
    )
    parser.add_argument(
        "--data_dir",
        type=str,
        help="Path to directory containing input rasters where the output rasters will be written.",
    )
    parser.add_argument(
        "--irr_march",
        type=str,
        help="Filename for March irradiance raster",
    )
    parser.add_argument(
        "--irr_march_flat", type=str, help="Filename for March irradiance raster with no topography"
    )
    parser.add_argument(
        "--irr_december",
        type=str,
        help="Filename for December irradiance raster",
    )
    parser.add_argument("--irr_june", type=str, help="Filename for June irradiance raster")
    parser.add_argument("--forest", type=str, help="Filename for forest percentage raster")
    parser.add_argument("--wind", type=str, help="Filename for wind raster")
    parser.add_argument("--conus_mfmax", type=str, help="Filename for conus mfmax raster")
    parser.add_argument("--conus_mfmin", type=str, help="Filename for conus mfmin raster")
    parser.add_argument("--conus_uadj", type=str, help="Filename for conus uadj raster")

    args = parser.parse_args()

    melt_factors(
        data_dir=Path(args.data_dir),
        irr_mar_file=Path(args.irr_march),
        irr_dec_file=Path(args.irr_december),
        irr_jun_file=Path(args.irr_june),
        irr_mar_flat_file=Path(args.irr_march_flat),
        wind_file=Path(args.wind),
        forest_file=Path(args.forest),
    )

    uadj(
        data_dir=Path(args.data_dir),
        wind_file=Path(args.wind),
        forest_file=Path(args.forest),
        irr_mar_file=Path(args.irr_march),
    )

    combine_rasters(
        data_dir=Path(args.data_dir),
        superconus_raster=Path("mfmax_temp.tif"),
        conus_raster=Path(args.conus_mfmax),
    )
    combine_rasters(
        data_dir=Path(args.data_dir),
        superconus_raster=Path("mfmin_temp.tif"),
        conus_raster=Path(args.conus_mfmin),
    )
    combine_rasters(
        data_dir=Path(args.data_dir),
        superconus_raster=Path("uadj_temp.tif"),
        conus_raster=Path(args.conus_uadj),
    )
