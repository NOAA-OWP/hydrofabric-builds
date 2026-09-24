# Settings
set dotenv-load := true
oconus-version := "0.1.8"

alias download := sync
alias download-ak := sync-ak
alias download-hi := sync-hi
alias download-prvi := sync-prvi

quickstart: sync build-conus

build CONFIG:
    uv sync --all-extras
    uv run python scripts/hf_runner.py --config {{ CONFIG }}

# CONUS
sync:
    # Reference Fabric
    aws s3 sync s3://edfs-data/reference/super_conus/ ./data/reference/ --exclude "*" --include "reference_divides.parquet" --include "reference_flowpaths.parquet"
    # Gages
    aws s3 sync s3://edfs-data/gages/ ./data/gages
    # Flowpath Attributes
    aws s3 sync s3://edfs-data/reference/super_conus/ ./data/flowpath-attributes/ --exclude "*" --include "Y_bf_predictions.parquet" --include "TW_bf_predictions.parquet" --include "r_predictions.parquet"
    # Divide Attributes
    aws s3 sync s3://edfs-data/attributes/5070/ ./data/divide-attributes
    aws s3 sync s3://edfs-data/attributes/gw/conus/ ./data/divide-attributes/gw --exclude "deprecated/*"
    aws s3 sync s3://edfs-data/attributes/glaciers/ ./data/divide-attributes/glaciers
    # NHD
    aws s3 sync s3://edfs-data/nhd/ ./data/reference/ --exclude="*" --include "nwm_flows.gpkg"
    # Lakes
    aws s3 sync s3://edfs-data/lakes/sconus ./data/sconus/lakes

build-conus: (build "configs/example_config.yaml")

# AK
sync-ak:
    # Reference Fabric
    aws s3 sync s3://edfs-data/reference-builds/ak/ ./data/reference/ --exclude "*" --include "ak_{{oconus-version}}_reference_divides.parquet" --include "ak_{{oconus-version}}_reference_flowpaths.parquet"
    # Gages - same as CONUS
    aws s3 sync s3://edfs-data/gages/ ./data/gages
    # Flowpath Attributes
    # None needed
    # Divide Attributes
    aws s3 sync s3://edfs-data/attributes/3338/ ./data/ak/divide-attributes
    aws s3 sync s3://edfs-data/attributes/gw/ak/ ./data/ak/divide-attributes/gw --exclude "deprecated/*"
    aws s3 sync s3://edfs-data/attributes/glaciers/ ./data/ak/divide-attributes/glaciers
    # NHD
    aws s3 cp s3://edfs-data/nhd/nwm_flows_alaska_nwmv3_ID_v2.gpkg ./data/reference/nwm_flows_alaska_nwmv3_ID_v2.gpkg
    # Lakes
    aws s3 sync s3://edfs-data/lakes/ak ./data/ak/lakes

build-ak: (build "configs/example_ak_config.yaml")

# HI
sync-hi:
    # Reference Fabric
    aws s3 sync s3://edfs-data/reference-builds/hi/ ./data/reference/ --exclude "*" --include "hi_{{oconus-version}}_reference_divides.parquet" --include "hi_{{oconus-version}}_reference_flowpaths.parquet"
    # Gages - same as CONUS
    aws s3 sync s3://edfs-data/gages/ ./data/gages
    # Flowpath Attributes
    # None needed
    # Divide Attributes
    aws s3 sync s3://edfs-data/attributes/32604/ ./data/hi/divide-attributes
    aws s3 sync s3://edfs-data/attributes/gw/hi/ ./data/hi/divide-attributes/gw --exclude "deprecated/*"
    # NHD
    aws s3 sync s3://edfs-data/nhd/ ./data/reference/ --exclude="*" --include "nwm_flows.gpkg"
    # Lakes
    aws s3 sync s3://edfs-data/lakes/hi ./data/hi/lakes


build-hi: (build "configs/example_hi_config.yaml")

# PRVI
sync-prvi:
    # Reference Fabric
    aws s3 sync s3://edfs-data/reference-builds/prvi/ ./data/reference/ --exclude "*" --include "prvi_{{oconus-version}}_reference_divides.parquet" --include "prvi_{{oconus-version}}_reference_flowpaths.parquet"
    # Gages - same as CONUS
    aws s3 sync s3://edfs-data/gages/ ./data/gages
    # Reference Reservoirs
    # TBD
    # Flowpath Attributes
    # None needed
    # Divide Attributes
    aws s3 sync s3://edfs-data/attributes/6566/ ./data/prvi/divide-attributes
    aws s3 sync s3://edfs-data/attributes/gw/prvi/ ./data/prvi/divide-attributes/gw --exclude "deprecated/*"
    # NHD
    aws s3 sync s3://edfs-data/nhd/ ./data/reference/ --exclude="*" --include "nwm_flows.gpkg"
    # Lakes
    aws s3 sync s3://edfs-data/lakes/prvi ./data/prvi/lakes

build-prvi: (build "configs/example_prvi_config.yaml")
