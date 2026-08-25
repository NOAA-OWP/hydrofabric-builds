create_cfe_init_bmi_config = function(basins, data_dir){

# Derive initial parameters for CFE based on NWM v3 parameter files and
# create the BMI config files for each catchment in the selected basins

#rm(list=ls())

library(terra)
library(zonal)
library(data.table)
library(sf)
library(ncdf4)
library(raster)
library(rwrfhydro)

group <- 1

# Groundwater parameters
message("processing groundwater parameters ...")
gw_file <- paste0(data_dir, "GWBUCKPARM_CONUS_FullRouting.nc")
gwparm <- GetNcdfFile(gw_file,quiet=TRUE)
gwvars <- names(gwparm)
gwparm <- data.table(gwparm)
setkey(gwparm, ComID)

# bucket/catchment mapping
gwWeightFile <- paste0(data_dir,"gwbuck_to_maskid_basins_group_",group,".txt")
dtWgt <- read.table(gwWeightFile, header=TRUE, sep="\t", stringsAsFactors=FALSE)
dtWgt <- data.table(dtWgt)
setkey(dtWgt, ComID)
gwparm <- merge(gwparm, dtWgt, all.x=TRUE, all.y=FALSE, suffixes=c("", ".cat"), by="ComID")
dtGwPars <- subset(gwparm, !is.na(cat_id))

# add divide_id from the crosswalk table
cwt <- read.table(paste0(data_dir,"raster_id_crosswalk_basins_group_1.csv"), header=TRUE, sep=",",
    colClasses=rep("character",7),stringsAsFactors=FALSE)
cwt$cat_id <- as.integer(cwt$cat_id)
dtGwPars <- merge(dtGwPars,cwt[,c("divide_id","gage","cat_id")],by="cat_id")
dtGwPars <- dtGwPars[,c("Coeff","Expon","Zmax","sumwt","divide_id","gage"),with=FALSE]

# compute weighted mean
dtGwPars1 <- dtGwPars[,.(Coeff=sum(Coeff*sumwt)/sum(sumwt),
                              Expon=sum(Expon*sumwt)/sum(sumwt),
                              #Zmax=sum(Zmax*sumwt)/sum(sumwt)),
                              #Zmax=sum(Zmax*sumwt)/sum(sumwt)/1000*10),
                              Zmax=sum(Zmax*sumwt)/sum(sumwt)/1000), #to be confirmed, Zmax for NMW is in mm (but m for CFE)
                              by=.(divide_id,gage)]
names(dtGwPars1) <- c("divide_id","gage","Cgw","expon","max_gw_storage")

write.csv(dtGwPars1, 'gw.csv')
}
