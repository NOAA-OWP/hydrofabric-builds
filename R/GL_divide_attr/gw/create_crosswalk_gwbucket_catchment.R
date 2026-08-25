# create a crosswalk table to match the NWM groudwater bucket ID (ComID) to the catchments of selected basins
# in order to properly transfer the NWM ground water parameters

create_crosswalk_gwbucket_catchment = function(data_dir){

#rm(list=ls())

library(raster)
library(data.table)
library(rwrfhydro)

# mask file of the basins (created by rasterize_basins.R)
run1 <- "basins_group_1"
maskFile <- paste0(data_dir,run1,".tif")

# NWM spatial weights file
wtFile <- paste0(data_dir,"spatialweights_1km_LongRange_NWMv3.0.nc")

# Read rasters
mask1 <- raster(maskFile)
mask1[mask1<=0] <- NA

# Output file (the crosswalk)
gwOutFile <- paste0(data_dir,"gwbuck_to_maskid_",run1,".txt")

# GW Buckets
wts <- ReadWtFile(wtFile)
wts <- wts[[1]]
ids <- unique(wts$IDmask)
dimy <- dim(mask1)[1]

# Convert matrix to data frame of indices and values
bas4join <- data.frame(which(!is.na(as.matrix(mask1)), arr.ind=TRUE))
bas4join$catid <- c(as.matrix(mask1)[!is.na(as.matrix(mask1))])
# Assign ij, referenced from (1,1) at lower left corner to match spatial weight file
bas4join$i_index <- bas4join$col
bas4join$j_index <- as.integer(dimy+1-bas4join$row)

# Join to weights table
bas4join <- data.table(bas4join)
wts <- data.table(wts)
setkey(bas4join, i_index, j_index)
setkey(wts, i_index, j_index)
wts <- merge(wts, bas4join, by=c("i_index", "j_index"), all.x=TRUE, all.y=FALSE)

# Aggregate weights
setkey(wts, IDmask, catid)
wts.sum <- wts[, list(sumwt=sum(weight)), by=c("IDmask", "catid")]
#Slower: wts.sum.max <- wts.sum[, ':=' (whichMax = sumwt == max(.SD$sumwt)), by="IDmask"]
wts.sum.max <- wts.sum[wts.sum[, .I[sumwt == max(sumwt)], by=IDmask]$V1]
gwOut <- data.frame(wts.sum.max[!is.na(catid),])
names(gwOut) <- c("ComID", "cat_id", "sumwt")

write.table(gwOut, file=gwOutFile, sep="\t", row.names=FALSE)
}
