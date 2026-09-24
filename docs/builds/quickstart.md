### Quickstart

TODO: Replace s3 paths with unpacking file

## CONUS
Below are the data files needed for running a full build of the NHF dataset and their locations. Each of the `aws` commands should be run from the NGWPC Test account

1. Reference Fabric
- `aws s3 cp s3://edfs-data/reference/super_conus/reference_divides.parquet ./data/reference/reference_divides.parquet`
- `aws s3 cp s3://edfs-data/reference/super_conus/reference_flowpaths.parquet ./data/reference/reference_flowpaths.parquet`

2. Gages
- `aws s3 sync s3://edfs-data/gages/ ./data/gages`

3. Lakes
- `aws s3 sync s3://edfs-data/lakes/sconus ./data/sconus/lakes`

4. Flowpath Attributes
- `aws s3 cp s3://edfs-data/reference/super_conus/Y_bf_predictions.parquet ./data/flowpath-attributes/Y_bf_predictions.parquet `
- `aws s3 cp s3://edfs-data/reference/super_conus/TW_bf_predictions.parquet ./data/flowpath-attributes/TW_bf_predictions.parquet`
- `aws s3 cp s3://edfs-data/reference/super_conus/r_predictions.parquet ./data/flowpath-attributes/r_predictions.parquet`

5. Divide Attributes
- `aws s3 sync s3://edfs-data/attributes/5070/ ./data/divide-attributes`
- `aws s3 sync s3://edfs-data/attributes/gw/conus/ ./data/divide-attributes/gw --exclude "deprecated/*"`
- `aws s3 sync s3://edfs-data/attributes/glaciers/ ./data/divide-attributes/glaciers`

6. NHD
- `aws s3 cp s3://edfs-data/nhd/nwm_flows.gpkg ./data/reference/nwm_flows.gpkg`
- `aws s3 sync s3://edfs-data/nhd-crosswalk ./data/nhd-crosswalk`



To run the NHF build, you can use the example config, or make your own based on it. The full run commands are:
```sh
uv sync --all-extras
uv run python scripts/hf_runner.py --config configs/example_config.yaml
```

## Alaska
1. Reference Fabric
- `aws s3 cp s3://edfs-data/reference-builds/ak/ak_0.1.7_reference_divides.parquet ./data/reference/ak_0.1.5_reference_divides.parquet`
- `aws s3 cp s3://edfs-data/reference-builds/ak/ak_0.1.7_reference_flowpaths.parquet ./data/reference/ak_0.1.5_reference_flowpaths.parquet`

2. Gages - same as CONUS
- `aws s3 sync s3://edfs-data/gages/ ./data/gages`

3. Lakes
- `aws s3 sync s3://edfs-data/lakes/ak ./data/ak/lakes`

4. Flowpath Attributes
- None needed

5. Divide Attributes
- `aws s3 sync s3://edfs-data/attributes/3338/ ./data/ak/divide-attributes`
- `aws s3 sync s3://edfs-data/attributes/gw/ak/ ./data/ak/divide-attributes/gw --exclude "deprecated/*"`
- `aws s3 sync s3://edfs-data/attributes/glaciers/ ./data/ak/divide-attributes/glaciers`

6. NHD
- `aws s3 cp s3://edfs-data/nhd/nwm_flows_alaska_nwmv3_ID_v2.gpkg ./data/reference/nwm_flows_alaska_nwmv3_ID_v2.gpkg`

To run the NHF build, you can use the example config, or make your own based on it. The full run commands are:
```sh
uv sync --all-extras
uv run python scripts/hf_runner.py --config configs/example_ak_config.yaml
```

## Hawaii
1. Reference Fabric
- `aws s3 cp s3://edfs-data/reference-builds/hi/hi_0.1.7_reference_divides.parquet ./data/reference/hi_0.1.5_reference_divides.parquet`
- `aws s3 cp s3://edfs-data/reference-builds/hi/hi_0.1.7_reference_flowpaths.parquet ./data/reference/hi_0.1.5_reference_flowpaths.parquet`

2. Gages - same as CONUS
- `aws s3 sync s3://edfs-data/gages/ ./data/gages`

3. Lakes
- `aws s3 sync s3://edfs-data/lakes/hi ./data/hi/lakes`

4. Flowpath Attributes
- None needed

5. Divide Attributes
- `aws s3 sync s3://edfs-data/attributes/32604/ ./data/hi/divide-attributes`
- `aws s3 sync s3://edfs-data/attributes/gw/hi/ ./data/hi/divide-attributes/gw  --exclude "deprecated/*"`

6. NHD
- `aws s3 cp s3://edfs-data/nhd/nwm_flows.gpkg ./data/reference/nwm_flows.gpkg`


To run the NHF build, you can use the example config, or make your own based on it. The full run commands are:
```sh
uv sync --all-extras
uv run python scripts/hf_runner.py --config configs/example_hi_config.yaml
```

## Puerto Rico/Virgin Islands
1. Reference Fabric
- `aws s3 cp s3://edfs-data/reference-builds/prvi/prvi_0.1.7_reference_divides.parquet ./data/reference/prvi_0.1.7_reference_divides.parquet`
- `aws s3 cp s3://edfs-data/reference-builds/prvi/prvi_0.1.7_reference_flowpaths.parquet ./data/reference/prvi_0.1.7_reference_flowpaths.parquet`

2. Gages - same as CONUS
- `aws s3 sync s3://edfs-data/gages/ ./data/gages`

3. Lakes
- `aws s3 sync s3://edfs-data/lakes/prvi ./data/prvi/lakes`

4. Flowpath Attributes
- None needed

5. Divide Attributes
- `aws s3 sync s3://edfs-data/attributes/6566/ ./data/prvi/divide-attributes`
- `aws s3 sync s3://edfs-data/attributes/gw/prvi/ ./data/prvi/divide-attributes/gw  --exclude "deprecated/*"`

6. NHD
- `aws s3 cp s3://edfs-data/nhd/nwm_flows.gpkg ./data/reference/nwm_flows.gpkg`


To run the NHF build, you can use the example config, or make your own based on it. The full run commands are:
```sh
uv sync --all-extras
uv run python scripts/hf_runner.py --config configs/example_prvi_config.yaml
```
