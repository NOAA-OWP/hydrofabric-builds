library(sf)
library(terra)
library(zonal)

div <- st_read('/Hydrofabric/data/hydrofabric/v2.2/nextgen/CONUS/gl_nextgen.gpkg',layer='divides')

crs <- '+proj=lcc +lat_0=40.0000076293945 +lon_0=-97 +lat_1=30 +lat_2=60 +x_0=0 +y_0=0 +R=6370000 +units=m +no_defs'

div <- st_transform(div,crs)

r <- rast('twi.tiff')

r <- project(r,crs)

twi <- execute_zonal(r,div,ID='divide_id',join=FALSE,fun=equal_population_distribution)

write.csv(twi,'twi.csv',row.names=FALSE)
_
