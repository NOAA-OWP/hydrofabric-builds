#An interface between the Rscript command line arguments and the R functions
#args = commandArgs(trailingOnly = TRUE)

source('create_cfe_init_bmi_config.R')
source('rasterize_basins.R')
source('create_crosswalk_gwbucket_catchment.R')

#gage_ids <- eval(parse(text=args[1]))
#data_dir <- args[2]

gage_ids <- '02AD010'
data_dir <- '/workspace/GL_attr/GW/data/'

print("Running rasterize_basins")
rasterize_basins(gage_ids, data_dir)
print("Running create_crosswalk_gwbucket_catchment")
create_crosswalk_gwbucket_catchment(data_dir)
print("Running create_cfe_init_bmi_config")
create_cfe_init_bmi_config(gage_ids, data_dir)
