import xarray as xr
from pyproj import CRS

nc_filename = "ga2.nc"

crs = CRS.from_epsg(4326)

nc = xr.open_dataset(nc_filename)

twi = nc["Band1"]

twi = twi.rio.write_crs(crs)
twi = twi.rio.set_spatial_dims("lat", "lon")

# crop global dataset to CONUS
twi = twi.sel(lon=slice(-130, -60), lat=slice(24, 55))
twi = twi.rio.reproject("EPSG:5070")

output_filepath = "twi.tif"
twi.rio.to_raster(output_filepath, tiled=True, compress="deflate")
