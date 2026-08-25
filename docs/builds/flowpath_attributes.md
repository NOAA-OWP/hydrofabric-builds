# Flowpath Attributes Build

The flowpath attributes task calculates metrics per flowpath linestring from multiple sources: DEM, WRF defaults, and RiverML outputs.

## To run:

1. Download source data from AWS test account:
- DEM 250 m: s3://edfs-data/attributes/5070/usgs/usgs_250m_dem_5070.tif * TBD: Change to higher resolution
- RiverML Y: s3://edfs-data/reference/super_conus/Y_bf_predictions.parquet
- RiverML TW: s3://edfs-data/reference/super_conus/TW_bf_predictions.parquet
- RiverML r: s3://edfs-data/reference/super_conus/r_predictions.parquet

Save to this `data` folder in this repo. `aws cp [file] [location]` can be used

2. Update HF config as needed: see `example_config.yaml`
- Set `run_flowpath_attribute_task: True`
- All configurable options found under `hydrofabric_builds/schemas/hydrofabric.py` - `FlowpathAttributesModelConfig` are
     - `hf_path`: Path to hydrofabric. This defaults to hydrofabric built in your model. Change to another HF here.
     - `flowpath_id`: Field name for flowpath id, default `fp_id`.
     - `use_stream_order`: Bool to use stream-order derived values for `n`, `chsslp`, and `bw`. See more info below in 'Attribute Notes'. Default to `True`.
     - `dem_path`: Path to DEM, defaut to `data/usgs_250m_dem_5070.tif`. * TBD: Change to higher resolution
     - `tw_path`: Path to topwdth parquet, default to `data/TW_bf_predictions.parquet`.
     - `y_path`: Path to y parquet, default to `data/Y_bf_predictions.parquet`.
     - `r_path`: Path to r parquet, default to `data/r_predictions.parquet`.

3. Run via HF pipeline

## Attributes:

### List of calculated attributes:

Defaults are from [WRF hydro routing defaults](https://github.com/NCAR/wrf_hydro_gis_preprocessor/blob/master/wrfhydro_gis/wrfhydro_functions.py#L128)

- y: Estimated depth associated with TopWdth (m)
- n: Manning's in channel roughness / n. Can be derived from Strahler stream order. Defaults to 0.035 without stream order
- ncc: Compound Channel Top Width (m). 2*n
- btmwdth: Bottom width of channel (meters). Can be derived from Strahler stream order. Defaults to 5 without stream order
- topwdth: Top Width (meters)
- topwdthcc: Compound Channel Top Width (meters)
- chslp: Channel side slope. Can be derived from Strahler stream order. Defaults to 0.05 without stream order.
- mean_elevation: Mean elevation (m) between nodes from 3DEP
- slope: Slope (meters/meters) computed from 3DEP
- musx: Muskingum weighting coefficient. Defaults to 0.2.
- musk: Muskingum routing time (seconds). Defaults to 3600

### Stream Order Derived Attributes
Stream order derived values are from [WRF hydro routing defaults](https://github.com/NCAR/wrf_hydro_gis_preprocessor/blob/master/wrfhydro_gis/wrfhydro_functions.py#L128)
- n: {1: 0.096, 2: 0.076, 3: 0.060, 4: 0.047, 5: 0.037, 6: 0.030, 7: 0.025, 8: 0.021, 9: 0.018,10: 0.022}
- chsslp: {1: 0.03, 2: 0.03, 3: 0.03, 4: 0.04, 5: 0.04, 6: 0.04, 7: 0.04, 8: 0.04, 9: 0.05, 10: 0.10}
- bw: {1: 1.6, 2: 2.4, 3: 3.5, 4: 5.3, 5: 7.4, 6: 11.0, 7: 14.0, 8: 16.0, 9: 26.0, 10: 110.0}
- ncc: 2 * n
- topwdthcc: 3 * topwdth

### RiverML Derived Attributes
`y` (estimated depth) and `topwdth` (top width) are from [RiverML](https://github.com/NOAA-OWP/predict-riverML) model predictions  on the reference fabric. The mean is used if a flowpath includes multiple reference fabric flowpaths.
