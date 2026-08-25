library(sf)

gpkg_in <- '/Hydrofabric/data/hydrofabric/v2.2/nextgen/CONUS/gl_nextgen.gpkg'
gpkg_out <- 'gl_nextgen_divide_attr.gpkg'

flowpaths <- st_read(gpkg_in, layer='flowpaths')
divides <- st_read(gpkg_in, layer='divides')
nexus <- st_read(gpkg_in, layer='nexus')
pois <- st_read(gpkg_in, layer='pois')
hydrolocations <- st_read(gpkg_in, layer='hydrolocations')
network <- st_read(gpkg_in, layer='network')
print('*******')
flowpath_attributes <- st_read(gpkg_in, layer='flowpath-attributes')

print('read divide attributes')
divide_attributes <- read.csv('all.csv')

crs <- '+proj=lcc +lat_0=40.0000076293945 +lon_0=-97 +lat_1=30 +lat_2=60 +x_0=0 +y_0=0 +R=6370000 +units=m +no_defs'

print('project layer')

flowpaths <- st_transform(flowpaths, crs)
divides <- st_transform(divides, crs)
nexus <- st_transform(nexus, crs)
pois <- st_transform(pois, crs)
hydrolocations <- st_transform(hydrolocations, crs)

st_write(flowpaths, gpkg_out, 'flowpaths')
st_write(divides, gpkg_out, 'divides', append=TRUE)
st_write(nexus, gpkg_out, 'nexus', append=TRUE)
st_write(pois, gpkg_out, 'pois', append=TRUE)
st_write(hydrolocations, gpkg_out, 'hydrolocations', append=TRUE)
st_write(network, gpkg_out, 'network', append=TRUE)
st_write(flowpath_attributes, gpkg_out, 'flowpath-attributes', append=TRUE)
st_write(divide_attributes, gpkg_out, 'divide-attributes', append=TRUE)
