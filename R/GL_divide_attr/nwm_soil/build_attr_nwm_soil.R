library(raster)
library(zonal)
library(sf)
library(terra)
library(tidyverse)

div <- st_read('/Hydrofabric/data/hydrofabric/v2.2/nextgen/CONUS/gl_nextgen.gpkg',layer='divides')

crs <- '+proj=lcc +lat_0=40.0000076293945 +lon_0=-97 +lat_1=30 +lat_2=60 +x_0=0 +y_0=0 +R=6370000 +units=m +no_defs'

div <- st_transform(div,crs)

# Get NWM Soil attributes

nwm_soil_names <- c('bexp', 'dksat', 'psisat', 'cwpvt', 'mfsno', 'mp', 'refkdt', 'slope_1km', 'smcmax', 'smcwlt', 'vcmx25')
nwm_soil_func <- c('mode', 'geom_mean', 'geom_mean', 'mean', 'mean', 'mean', 'mean', 'mean', 'mean', 'mean', 'mean')
nwm_soil_layers <-c(4,4,4,1,1,1,1,1,4,4,1)

soil <- data.frame()

for (x in 1:length(nwm_soil_names)){

    name <- nwm_soil_names[x]
    func <- nwm_soil_func[x]
    layers <- nwm_soil_layers[x]

    for (layer in 1:layers){

        if (layers == 1) {
            rasterfile <- paste(name,'tif',sep='.')

        }   else if (layers > 1) {
            rasterfile <- paste(name,layer,sep='_')
            rasterfile <- paste(rasterfile,'tif',sep='.')
        }

        print(paste('processing:',rasterfile,sep=' '))
        r <- rast(rasterfile)
        #r <- project(r,crs)

        if (func == 'mean') {attr_zonal <- execute_zonal(r,div,ID='divide_id',join=FALSE)}
        if (func == 'mode') {attr_zonal <- execute_zonal(r,div,ID='divide_id',join=FALSE, fun=mode)}
        if (func == 'geom_mean') {attr_zonal <- execute_zonal(r,div,ID='divide_id',join=FALSE, fun=geometric_mean)}

        if (layers == 1) {
           col_name <- paste(func,name,sep='.')
        } else if (layers > 1) {
           soil_layers <- paste('soil_layers_stag=',layer,sep='')
           col_name <- paste(func,name,sep='.')
           col_name <- paste(col_name,soil_layers,sep='_')
        }

        names(attr_zonal) <- c('divide_id',col_name)
        print(x)
        print(layer)

        if ((x == 1) & (layer == 1)) {
            soil <- attr_zonal
            print('first')
        } else {
        soil <- merge(soil,attr_zonal,by='divide_id')
        print('next')
        }

    }

}
write.csv(soil,'nwm_soil.csv',row.names=FALSE)
