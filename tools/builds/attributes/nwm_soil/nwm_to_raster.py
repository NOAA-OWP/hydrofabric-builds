import numpy as np
import xarray as xr
from pyproj import CRS

nc_filename = "soilproperties_CONUS_FullRouting.nc"

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

# Set CRS, extent and resolution
proj4str = "+proj=lcc +lat_0=40.0000076293945 +lon_0=-97 +lat_1=30 +lat_2=60 +x_0=0 +y_0=0 +R=6370000 +units=m +no_defs"
crs = CRS.from_proj4(proj4str)
xmin = -2304000
xmax = 2304000
ymin = -1920001
ymax = 1919999
dx = 1000
dy = 1000

# Create coordinate value array for xarray
x_coords = np.arange(xmin, xmax, dx)
y_coords = np.arange(ymin, ymax, dy)

nwm_soil = xr.open_dataset(nc_filename)

for name in nwm_soil_vars:
    print(f"processing {name}")
    var = nwm_soil[name]

    # If variable is 2d -- most fields have a time dimension with a single value, so
    # dimensions are time * west_east * south_north, imperv is just  west_east * south_north
    if len(var.dims) <= 3:
        var = var.rio.write_crs(crs)
        var = var.assign_coords(west_east=x_coords)
        var = var.assign_coords(south_north=y_coords)
        var = var.rio.set_spatial_dims("west_east", "south_north")
        var = var.rio.reproject("EPSG:5070")

        output_filepath = f"{name}.tif"
        var.rio.to_raster(output_filepath, tiled=True, compress="deflate")

    # For variables with layers, processes each layer
    elif len(var.dims) == 4:
        num_layers = var.sizes["soil_layers_stag"]
        for i in range(0, num_layers):
            layer = var.sel(soil_layers_stag=i)
            layer = layer.rio.write_crs(crs)
            layer = layer.assign_coords(west_east=x_coords)
            layer = layer.assign_coords(south_north=y_coords)
            layer = layer.rio.set_spatial_dims("west_east", "south_north")
            layer = layer.rio.reproject("EPSG:5070")

            output_filepath = f"{name}_{i}.tif"
            layer.rio.to_raster(output_filepath, tiled=True, compress="deflate")
