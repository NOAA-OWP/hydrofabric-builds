# Divide Attributes Build

The divide attributes task calculates zonal statistics for divides from a number of rasters.

To run:

1. Download source rasters from AWS test account and maintain folder structure:
```
aws s3 sync s3://edfs-data/attributes/5070/ ./data/divide-attributes
aws s3 cp s3://edfs-data/glaciers/glims_20250624.parquet ./data/divide-attributes
aws s3 cp s3://edfs-data/attributes/gw/gw.csv ./data/divide-attributes
```

2. Example config settings shown in `configs/example_config.yaml` > `divide-attributes`. Note that attribute list is required.

3. Multiprocessing:
The default setting is to use total CPU cores. To specify another core number:
- Set the `processes` in `divide_attributes` to desired cores.

Then, in your config do one of the following:
- set `split_vpu` to `True`: This will automatically split your domain file by VPU and create temp GPKGs
- add a `divides_path_list` key where the value is a list of paths: `['divides_1.gpkg', divides_2.gpkg']`

Final output will always merge all divides.

!!! note
    A number of temporary files will be created during the process. If multiprocessing, these will be copies of rasters and parquets with zonal statistics outputs for each process. By default these are always deleted when the run ends, even if it errors. If you are debugging and want to see the temporary outputs, set `debug: True` in `example_divide_attributes_config.yaml`

!!! note
    For small test cases, using a single process is recommended to avoid overhead of copying rasters. Multiprocessing is recommended for full pipeline runs.

4. Run using the `hf_runner.py` script: `python scripts/hf_runner.py --config ./configs/example_config.yaml`

Config file notes:
- For attributes, the `file_name` will be appended to `data_dir` during pipeline. Thus, `data_dir` should be root data folder and subfolders for attributes should be included in the `file_name`. e.g. `data_dir: "data/divide_attributes"` and `file_name: "nwm/bexp_0.tif"` will be concatenated in the pipeline to the full path `data/divide_attributes/nwm/bexp_0.tif`.

- Aggregation type (`agg_type`) must be found in `hydrofabric_builds.schemas.hydrofabric.AggTypeEnum` options
