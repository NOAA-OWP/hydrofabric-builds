"""Creates separate raster files for groundwater expon, coeff, and zmax attributes.

Inputs are NWM Full Routing spatial weights and GWBUCKPARM netCDF files.

Sample call:
python gw2.py --data_dir /home/daniel.cumpton/nhf-builds/data/divide-attributes/gw
--weights_file spatialweights_CONUS_FullRouting.nc
--gwbuckparm_file GWBUCKPARM_CONUS_FullRouting.nc
--crs 5070
"""

import argparse
import gc
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from tools.builds.attributes.schemas.attributes import (
    GroundWaterProjectionAK,
    GroundWaterProjectionCONUS,
    GroundWaterProjectionHI,
    GroundWaterProjectionPRVI,
    HydrofabricCRS,
)


def build_groundwater(data_dir: Path, weights_file: Path, gwbuckparm_file: Path, crs: int) -> None:
    """Create rasters for each of the three groundwater parameters

    Parameters
    ----------
    data_dir : Path
        Path to directory containing the superconus parameter raster and conus NWS raster
    weights_file: Path
        Filename of the NWM spatial weights netCDF file
    gwbuckparm_file: Path
        Filename of the NWM gwbuckparm netCDF file
    crs: int
        EPSG code for the NHF domain CRS used for the output raster, e.g., 5070 for CONUS


    Returns
    -------
    None

    """
    # set up GW data CRS based on the NHF domain CRS
    domain_crs: (
        type[GroundWaterProjectionCONUS]
        | type[GroundWaterProjectionAK]
        | type[GroundWaterProjectionHI]
        | type[GroundWaterProjectionPRVI]
    )

    crs = int(crs)

    if crs == HydrofabricCRS.CONUS.value:
        domain_crs = GroundWaterProjectionCONUS
    elif crs == HydrofabricCRS.AK.value:
        domain_crs = GroundWaterProjectionAK
    elif crs == HydrofabricCRS.HI.value:
        domain_crs = GroundWaterProjectionHI
    elif crs == HydrofabricCRS.PRVI.value:
        domain_crs = GroundWaterProjectionPRVI
    else:
        error_str = {"Error": f"Groundwater CRS {crs} not supported. Groundwater will not be calculated"}
        print(error_str)
        return

    # Read spatial weights netCDF file
    try:
        wts = xr.open_dataset(Path.joinpath(data_dir, weights_file))
    except FileNotFoundError:
        error_str = {"Error": f"The file {weights_file} was not found. Skipping groundwater"}
        print(error_str)
        return
    wts = wts[["IDmask", "weight", "i_index", "j_index"]].to_dataframe()
    wts = wts.rename(columns={"IDmask": "ComID"})

    # Read gwbuckparm netCDF file
    try:
        gwbuckparm = xr.open_dataset(Path.joinpath(data_dir, gwbuckparm_file))
    except FileNotFoundError:
        error_str = {"Error": f"The file {gwbuckparm_file} was not found. Skipping groundwater."}
        print(error_str)
        return
    gwbuckparm = gwbuckparm[["ComID", "Expon", "Zmax", "Coeff"]].to_dataframe()

    # Merge weights table to gwbuckparm on the ComID column
    full_df = pd.merge(wts, gwbuckparm, on="ComID", how="left")
    # clean up large no longer used arrays
    del wts, gwbuckparm
    gc.collect()

    # Sort values by i_index, j_index, and weight to drop duplicates keeping the largest weight
    full_df = full_df.sort_values(["i_index", "j_index", "weight"], ascending=False)
    full_df = full_df.drop_duplicates(subset=["i_index", "j_index"], keep="first")

    # set up x and y coordinate arrays for rioxarray.  The origin is the top left corner
    y_coords = np.arange(
        domain_crs.Y_ORIGIN.value,
        domain_crs.Y_ORIGIN.value + (domain_crs.DY.value * domain_crs.HEIGHT.value),
        domain_crs.DY.value,
    )
    x_coords = np.arange(
        domain_crs.X_ORIGIN.value,
        domain_crs.X_ORIGIN.value + (domain_crs.DX.value * domain_crs.WIDTH.value),
        domain_crs.DX.value,
    )

    col = full_df["i_index"].to_numpy()
    row = full_df["j_index"].to_numpy()

    parameters = ["Expon", "Zmax", "Coeff"]

    # loop through parameters and create rasters
    for parameter in parameters:
        data = np.full((domain_crs.HEIGHT.value, domain_crs.WIDTH.value), np.nan)
        data[row, col] = full_df[parameter].to_numpy()

        # convert zmax from mm to m
        if parameter == "Zmax":
            data = data / 1000.0

        data = xr.DataArray(data, coords={"y": y_coords, "x": x_coords}, dims=("y", "x"))

        # Create raster in the NWM GW data native CRS
        data.rio.write_crs(domain_crs.PROJ4.value, inplace=True)
        # Reproject to NHF CRS
        data = data.rio.reproject(f"EPSG:{crs}")

        # Write raster to file
        data.rio.to_raster(
            Path.joinpath(data_dir, f"{parameter.lower()}.tif"), tiled=True, compress="deflate"
        )

        # remove large no longer used array
        del data
        gc.collect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A script to create rasters for groundwater attributes")
    parser.add_argument(
        "--data_dir",
        type=str,
        help="Path to directory containing input rasters where the output rasters will be written.",
    )
    parser.add_argument(
        "--weights_file",
        type=str,
        help="filename of the full domain spatial weights file",
    )
    parser.add_argument(
        "--gwbuckparm_file",
        type=str,
        help="filename of the full domain gwbuckparm file",
    )
    parser.add_argument(
        "--crs",
        type=str,
        help="domain crs EPSG number, e.g, 5070",
    )

args = parser.parse_args()

build_groundwater(
    data_dir=Path(args.data_dir),
    weights_file=Path(args.weights_file),
    gwbuckparm_file=Path(args.gwbuckparm_file),
    crs=args.crs,
)
