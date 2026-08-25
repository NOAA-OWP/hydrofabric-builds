### Quickstart

Below are the data files needed for running a full build of the NHF dataset and their locations. Each of the `aws` commands should be run from the NGWPC Test account

1. Reference Fabric
- `aws s3 cp s3://edfs-data/reference/super_conus/reference_divides.parquet ./data/reference/reference_divides.parquet`
- `aws s3 cp s3://edfs-data/reference/super_conus/reference_flowpaths.parquet ./data/reference/reference_flowpaths.parquet`

2. Gages
- `aws s3 sync s3://edfs-data/gages/ ./data/gages`

3. Reference Reservoirs
- `aws s3 sync s3://edfs-data/reservoirs/ ./data/reservoirs`

4. Flowpath Attributes
- `aws s3 cp s3://edfs-data/reference/super_conus/Y_bf_predictions.parquet ./data/flowpath-attributes/Y_bf_predictions.parquet `
- `aws s3 cp s3://edfs-data/reference/super_conus/TW_bf_predictions.parquet ./data/flowpath-attributes/TW_bf_predictions.parquet`
- `aws s3 cp s3://edfs-data/reference/super_conus/r_predictions.parquet ./data/flowpath-attributes/r_predictions.parquet`

5. Divide Attributes
- `aws s3 sync s3://edfs-data/attributes/5070/ ./data/divide-attributes`
- `aws s3 sync s3://edfs-data/attributes/gw/ ./data/divide-attributes/gw`
- `aws s3 sync s3://edfs-data/attributes/glaciers/ ./data/divide-attributes/glaciers`

6. NHD
- `aws s3 cp s3://edfs-data/nhd/nwm_flows.gpkg ./data/reference/nwm_flows.gpkg`
- `aws s3 sync s3://edfs-data/nhd-crosswalk ./data/nhd-crosswalk`

To run the NHF build, you can use the example config, or make your own based on it. The full run commands are:
```sh
uv sync --all-extras
uv run python scripts/hf_runner --config configs/example_config.yaml
```
