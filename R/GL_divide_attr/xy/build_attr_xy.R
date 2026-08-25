library(raster)
library(zonal)
library(sf)
library(terra)
library(tidyverse)

crs <- '+proj=lcc +lat_0=40.0000076293945 +lon_0=-97 +lat_1=30 +lat_2=60 +x_0=0 +y_0=0 +R=6370000 +units=m +no_defs'

div <- st_read('/Hydrofabric/data/hydrofabric/v2.2/nextgen/CONUS/gl_nextgen.gpkg',layer='divides')

div <- st_transform(div,crs)

# Get xy coordinates for divide centroids
print('xy')
xy <- st_point_on_surface(div)
xy <- xy %>% select(c('divide_id','geom')) %>% mutate(coords = st_coordinates(geom))
xy <- data.frame(xy)
xy <- select(xy,c('divide_id','coords'))
#xy %>% select(geom) %>% mutate(geom = NULL)
print(xy)
write.csv(xy,'xy.csv',row.names=FALSE)
