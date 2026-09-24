import argparse
from pathlib import Path

import numpy as np
import xarray as xr

from tools.builds.attributes.schemas.attributes import (
    HydrofabricCRS,
    NWMProjectionAK,
    NWMProjectionCONUS,
    NWMProjectionHI,
    NWMProjectionPRVI,
)


def build_nwm_soil(data_dir: Path, soilproperties_file: Path, crs: int) -> None:
    """Create rasters for each of the three groundwater parameters

    Parameters
    ----------
    data_dir : Path
        Path to directory containing the NWM soil properties netCDF file and where the
        rasters will be written to.
    weights_file: Path
        Filename of the NWM soil properties full routing NetCDF file.
    crs: int
        EPSG code for the NHF domain CRS used for the output raster, e.g., 5070 for CONUS.


    Returns
    -------
    None
    """
    # set up the NWM soil data CRS based on the NHF domain CRS
    domain_crs: (
        type[NWMProjectionCONUS] | type[NWMProjectionAK] | type[NWMProjectionHI] | type[NWMProjectionPRVI]
    )

    crs = int(crs)

    if crs == HydrofabricCRS.CONUS.value:
        domain_crs = NWMProjectionCONUS
    elif crs == HydrofabricCRS.AK.value:
        domain_crs = NWMProjectionAK
    elif crs == HydrofabricCRS.HI.value:
        domain_crs = NWMProjectionHI
    elif crs == HydrofabricCRS.PRVI.value:
        domain_crs = NWMProjectionPRVI
    else:
        error_str = {"Error": f"Groundwater CRS {crs} not supported. Groundwater will not be calculated"}
        print(error_str)
        return

    # Soil properties variables
    nwm_soil_vars = [
        "AXAJ",
        "bexp",
        "BXAJ",
        "cwpvt",
        "dksat",
        "imperv",
        "mfsno",
        "mp",
        "psisat",
        "quartz",
        "refkdt",
        "slope",
        "smcmax",
        "smcwlt",
        "vcmx25",
        "XXAJ",
    ]

    # imperv is only in CONUS.  Remove for all other domains
    if not crs == 5070:
        nwm_soil_vars.remove("imperv")

    # Create coordinate value array for xarray
    x_coords = np.arange(domain_crs.XMIN.value, domain_crs.XMAX.value, domain_crs.DX.value)
    y_coords = np.arange(domain_crs.YMIN.value, domain_crs.YMAX.value, domain_crs.DY.value)

    # Read soilproperties file
    try:
        nwm_soil = xr.open_dataset(Path.joinpath(data_dir, soilproperties_file))
    except FileNotFoundError:
        error_str = {"Error": f"The file {soilproperties_file} was not found. Skipping groundwater"}
        print(error_str)
        return

    # These parameters have zeros for water where there is no value.  Change zeros
    # to NA so that those pixels will be ignored by zonal statistics.
    no_zeros = ["bexp", "dksat", "psisat", "smcwlt", "vcmx25", "BXAJ", "XXAJ"]

    # SMCMAX has ones for water.  Reset to NA as well.
    no_ones = ["smcmax"]

    # Loop through variables and create rasters
    for name in nwm_soil_vars:
        print(f"processing {name}")
        var = nwm_soil[name]

        # If variable is 2d -- most fields have a time dimension with a single value, so
        # dimensions are time * west_east * south_north, imperv is just  west_east * south_north
        if len(var.dims) <= 3:
            # change zeros to NA
            if name in no_zeros:
                var = var.where(var != 0)
            # change ones to NA
            if name in no_ones:
                var = var.where(var != 1)
            var = var.rio.write_crs(domain_crs.PROJ4.value)
            var = var.assign_coords(west_east=x_coords)
            var = var.assign_coords(south_north=y_coords)
            var = var.rio.set_spatial_dims("west_east", "south_north")
            var = var.rio.reproject(f"EPSG:{crs}")

            output_filepath = Path.joinpath(data_dir, f"{name}.tif")
            var.rio.to_raster(output_filepath, tiled=True, compress="deflate")

        # For variables with layers, processes each layer
        elif len(var.dims) == 4:
            num_layers = var.sizes["soil_layers_stag"]
            for i in range(0, num_layers):
                layer = var.sel(soil_layers_stag=i)
                # change zeros to NA
                if name in no_zeros:
                    layer = layer.where(layer != 0)
                # change ones to NA
                elif name in no_ones:
                    layer = layer.where(layer != 1)
                layer = layer.rio.write_crs(domain_crs.PROJ4.value)
                layer = layer.assign_coords(west_east=x_coords)
                layer = layer.assign_coords(south_north=y_coords)
                layer = layer.rio.set_spatial_dims("west_east", "south_north")
                layer = layer.rio.reproject(f"EPSG:{crs}")

                output_filepath = Path.joinpath(data_dir, f"{name}_{i}.tif")
                layer.rio.to_raster(output_filepath, tiled=True, compress="deflate")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A script to create rasters for groundwater attributes")
    parser.add_argument(
        "--data_dir",
        type=str,
        help="Path to directory containing the NWM soil properties NetCDF file and where the output rasters will be written.",
    )
    parser.add_argument(
        "--soilproperties_file",
        type=str,
        help="filename of the NWM soilproperties full routing netCDF file",
    )
    parser.add_argument(
        "--crs",
        type=str,
        help="domain crs EPSG number, e.g, 5070",
    )

args = parser.parse_args()

build_nwm_soil(data_dir=Path(args.data_dir), soilproperties_file=Path(args.soilproperties_file), crs=args.crs)
