library(raster)
library(terra)
library(zonal)
library(sf)

crs <- '+proj=lcc +lat_0=40.0000076293945 +lon_0=-97 +lat_1=30 +lat_2=60 +x_0=0 +y_0=0 +R=6370000 +units=m +no_defs'

div <- st_read('/Hydrofabric/data/hydrofabric/v2.2/nextgen/CONUS/gl_nextgen.gpkg',layer='divides')
div <- st_transform(div,crs)

elevation_r <- rast('usgs_250m_elev_v1.tif')

elevation_r <- project(elevation_r, crs)

v <- values(elevation_r)*100
values(elevation_r) <- v

elevation <- execute_zonal(elevation_r,div,ID='divide_id',join=FALSE)

write.csv(elevation, 'elevation.csv', row.names=FALSE)
