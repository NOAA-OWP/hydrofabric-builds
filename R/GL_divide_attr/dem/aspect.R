library(raster)
library(terra)
library(zonal)
library(sf)

crs <- '+proj=lcc +lat_0=40.0000076293945 +lon_0=-97 +lat_1=30 +lat_2=60 +x_0=0 +y_0=0 +R=6370000 +units=m +no_defs'

div <- st_read('/Hydrofabric/data/hydrofabric/v2.2/nextgen/CONUS/gl_nextgen.gpkg',layer='divides')
div <- st_transform(div,crs)

aspect_r <- rast('usgs_250m_aspect_crop.tif')

aspect_r <- project(aspect_r, crs)

aspect <- execute_zonal(aspect_r,div,ID='divide_id',join=FALSE,fun=circular_mean)

names(aspect) <- c('divide_id','circ_mean.aspect')

write.csv(aspect, 'aspect.csv', row.names=FALSE)
