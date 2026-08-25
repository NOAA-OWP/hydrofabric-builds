library(tidyverse)

xy <- read.csv('xy/xy.csv')
soil_veg_type <- read.csv('soil_veg_type/soil_veg_type.csv')
nwm_soil <- read.csv('nwm_soil/nwm_soil.csv')
twi <- read.csv('twi/twi.csv')
elev <- read.csv('dem/elevation.csv')
slope <- read.csv('dem/slope.csv')
aspect <- read.csv('dem/aspect.csv')
gw <- read.csv('gw/gw.csv')

gw <- gw %>% select(-gage)

all_dfs <- list(xy, soil_veg_type, nwm_soil, twi, elev, slope, aspect, gw)

all <- all_dfs %>% reduce(full_join, by='divide_id')

all$mean.Coeff[is.na(all$mean.Coeff)] <- 0.05
all$mean.Zmax[is.na(all$mean.Zmax)] <- 1
all$mode.Expon[is.na(all$mode.Expon)] <- 1

all <- all %>% mutate(vpuid='gl')
all <- all %>% mutate(mean.impervious=0)

all <- all %>% select(-X)

print(str(all))

write.csv(all,'all.csv', row.names=FALSE)
