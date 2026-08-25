library(raster)
library(zonal)
library(sf)
library(terra)
library(tidyverse)

#crs <- 'EPSG:4326'
crs <- '+proj=lcc +lat_0=40.0000076293945 +lon_0=-97 +lat_1=30 +lat_2=60 +x_0=0 +y_0=0 +R=6370000 +units=m +no_defs'

div <- st_read('/Hydrofabric/data/hydrofabric/v2.2/nextgen/CONUS/gl_nextgen.gpkg',layer='divides')
div <- st_transform(div,crs)


# Get soil and vegetation types
print('soil and veg type')
soil_type_r <- rast('ISLTYP.tif')
veg_type_r <- rast('IVGTYP.tif')
#soil_type_r <- project(soil_type_r,crs)
#veg_type_r <- project(veg_type_r,crs)

print(st_crs(div))
print(crs(veg_type_r))

soil_type <- execute_zonal(soil_type_r,div,ID='divide_id',join=FALSE, fun=mode)
veg_type <- execute_zonal(veg_type_r,div,ID='divide_id',join=FALSE, fun=mode)

soil_veg_type <- merge(soil_type,veg_type,by='divide_id')
names(soil_veg_type) <- c('divide_id','ISLTYP','IVGTYP')
write.csv(soil_veg_type,'soil_veg_type.csv',row.names=FALSE)
