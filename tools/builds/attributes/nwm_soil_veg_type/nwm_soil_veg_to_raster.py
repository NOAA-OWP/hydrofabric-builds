import numpy as np
import xarray as xr
from pyproj import CRS

nc_filename = "wrfinput_CONUS.nc"

nwm_soil_vars = ["ISLTYP", "IVGTYP"]

# Set CRS, extent and resolution
proj4str = "+proj=lcc +lat_0=40.0000076293945 +lon_0=-97 +lat_1=30 +lat_2=60 +x_0=0 +y_0=0 +R=6370000 +units=m +no_defs"
crs = CRS.from_proj4(proj4str)
xmin = -2304000
xmax = 2304000
ymin = -1920001
ymax = 1919999
dx = 1000
dy = 1000

# Create coordinate value arrays for xarray
x_coords = np.arange(xmin, xmax, dx)
y_coords = np.arange(ymin, ymax, dy)

nwm_soil = xr.open_dataset(nc_filename)

for name in nwm_soil_vars:
    print(f"processing {name}")
    var = nwm_soil[name]

    var = var.rio.write_crs(crs)
    var = var.assign_coords(west_east=x_coords)
    var = var.assign_coords(south_north=y_coords)
    var = var.rio.set_spatial_dims("west_east", "south_north")
    var = var.rio.reproject("EPSG:5070")

    output_filepath = f"{name}.tif"
    var.rio.to_raster(output_filepath, tiled=True, compress="deflate")
