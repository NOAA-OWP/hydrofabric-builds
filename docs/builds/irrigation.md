# Building Irrigation

## Introduction
Here, irrigation is considered a list of crops that may be irrigated. This build downloads the USDA Cropland Data Layer for requested years, converts to a mask at native resolution (30 meter), and optionally regrids to a coarser grid and resolution where the new raster represents percent of cell irrigated.

USDA CDL reference as of 8/20/25: https://www.nass.usda.gov/Research_and_Science/Cropland/SARS1a.php

CDL Crops considered irrigation:
- 1 - corn,
- 3 - rice
- 5 - soybean
- 12 - sweet corn
- 13 - pop or orn corn
- 92 - aquaculture
- 250 - cranberry

`tools/builds/run_irrigation.py` can perform a full pipeline of:
- download CDL year(s)
- unzip files
- classify to binary raster of irrigated crops
- aggregate temporally to create one raster where any cell with irrigation in requested year range is irrigated will be considered irrigated
- resample and re-align to a coarser resolution grid where each cell represents percent irrigated

## Running

!!! warning
    Running at the CONUS scale consumes significant memory even when using dask arrays. If classifying and aggregating, **~80 GB RAM** may be used. If regridding at 250 meter resolution, **~100 GB** may be used.

!!! warning
    Downloading and unzipping 10 years of CDL will use **~80 GB** disk space. You can safely delete `.ovr` files (5 GB) to reduce disk space.

!!! note
    Running the full pipeline including re-gridding may create memory leaks. It is recommended to re-grid in a separate call after downloading, unzipping, classifying, and aggregating. This can be done using the `--no-download`, `--no-unzip`, `--no-classify` `--no-aggreegate` flags discussed below. Each layer can take 10+ minutes to regrid.

#### Pipeline Demo
Run an example of the pipeline (excluding re-gridding) for two years to demonstrate processes:

1. Create or update virtual environment: `uv sync`

2. Run
```sh
python tools/builds/irrigation/run_irrigation.py -w ./data --min_yr 2015 --max_yr 2016
```

#### Full pipeline used for the f1 Trainer CONUS CNN:

1. Download CONUS grid file from s3 (Currently located on `data` account at  `s3://fim-services-data/f1/data/conus.tif`) and save to `./data`

2. Create or update virtual environment: `uv sync`

3. First download, classify, and aggregate layers.
```sh
python tools/builds/irrigation/run_irrigation.py -w ./data --min_yr 2015 --max_yr 2024
```

4. Then, use skip flags to only do regridding. Regridding can be done with the other steps; however, it can have even higher memory requirements.
```sh
python tools/builds/irrigation/run_irrigation.py -w ./data --min_yr 2015 --max_yr 2024 -g ./data/conus.tif --no_download --no_unzip --no_classify --no_aggregate
```

!!! note
    To "pick up where you left off": use flags `--no-download`, `--no-unzip`, `--no-classify`, `--no-aggregate` as necessary. Regridding will only be done when `--grid` / `-g` is specified.


#### Example: If you already processed all years but only need to regrid, run:
```sh
python tools/builds/irrigation/run_irrigation.py -w ./data --my_yr 2015 --max_yr 2016 -g ./data/conus.tif --no_download --no_unzip --no_classify --no-aggregate
```

#### Example: If you already processed all years and only need to regrid **one** layer, run:
```sh
python tools/builds/irrigation/run_irrigation.py -w ./data --my_yr 2015 --max_yr 2016 -g ./data/conus.tif --grid_yr 2015_2016 --no_download --no_unzip --no_classify --no-aggregate
```
Note that the temporally aggregated "20xx_20xx" string is accepted. Otherwise, use a single year number.

#### Example: If you already download and unzipped all layers but need to classify and aggregate, run:
```sh
python tools/builds/irrigation/run_irrigation.py -w -./data --min_yr 2015 --max_yr 2025 --no-download --no-unzip
```
