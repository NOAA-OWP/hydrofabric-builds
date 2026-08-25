library(ncdf4)
library(raster)
library(sf)
library(terra)

#div <- st_read('/Hydrofabric/data/hydrofabric/v2.2/nextgen/CONUS/conus_nextgen.gpkg', layer='divides')
#div_unique <- div[!duplicated(div$divide_id),]

#div_unique = st_read('hf2.2_divides.gpkg',layer='hf2.2_divides')

filename <- 'wrfinput_CONUS.nc'
nwm_nc <- nc_open(filename)
nc_names <- names(nwm_nc$var)

ext <- extent(363197.3,2006436.2,144063.1,1810826.8)

mask <- raster('final_combined_calib_v3.tif')

nc_vars <- c('ISLTYP', 'IVGTYP')

for (var in nc_vars){
    var_nc <- ncvar_get(nwm_nc,var)
    var_nc[var_nc == -9999] <- NA
    var_nc <- t(var_nc)[nrow(t(var_nc)):1,]
    var_r <- raster(var_nc)
    crs(var_r) <- crs(mask)
    extent(var_r) <- extent(mask)
    res(var_r) <- res(mask)
    var_r <- crop(var_r,ext)
    raster_file <- paste(var,'tif',sep='.')
    writeRaster(var_r, raster_file, overwrite=TRUE)
}
