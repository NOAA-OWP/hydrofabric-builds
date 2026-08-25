# rasterize selected basins given the gpkg (GDAL needs to be installed)

rasterize_basins = function(basins, data_dir){

#rm(list=ls())

#library(rgdal)
library(raster)
library(sf)

# NWM domain projection
prjstr <- "+proj=lcc +lat_1=30 +lat_2=60 +lat_0=40.0000076293945 +lon_0=-97 +x_0=0 +y_0=0 +a=6370000 +b=6370000 +units=m +no_defs"

# basin group
group <- 1
#basins <- c("01123000","01350080","14141500","14187000")
str1 <- paste0("basins_group_",group)

# hydrofabric file for the basins (all catchments together)
sf1 <- data.frame()
for (gage1 in basins) {
#    str_gage1 <- ifelse(substr(gage1,1,1)=="0",substr(gage1,2,nchar(gage1)),gage1)
    str_gage1 <- gage1
#    hydro_file <- paste0(data_dir,"gauge_",str_gage1,".gpkg")
    hydro_file <- 'GL_all.gpkg'
    sf0 <- read_sf(hydro_file, "GL_all")
    sf0$gage <- gage1
    sf1 <- rbind(sf1,sf0)
}
sf1$cat_id <- 1:nrow(sf1)

# transform projection
sf1 <- st_transform(sf1,crs(prjstr))

# write to shapefile
shp_file <- paste0(data_dir,str1,".shp")
st_write(sf1,shp_file,append=FALSE)

# create raster using gdal_rasterize
file1 <- paste0(data_dir,str1,".tif")
system(paste0("cp ",data_dir,"geogrid_1km_blank.tif ",file1))
while(!file.exists(file1)) Sys.sleep(1)

system(paste0("gdal_rasterize -a cat_id -l ",str1," ",shp_file," ", file1))

# plot the raster to check
png(paste0(str1,".png"))
r1 <- raster(file1)
plot(r1)
dev.off()

# save the raster_id / catchment id crosswalk table for later use in transferring NWM groundwater parameters
sf1$geom <- NULL
write.csv(sf1,file=paste0(data_dir,"raster_id_crosswalk_",str1,".csv"),quote=FALSE, row.names=FALSE)

}
