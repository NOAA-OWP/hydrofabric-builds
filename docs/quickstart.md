# Quickstart

## Install all dependencies

This repo is managed through [UV](https://docs.astral.sh/uv/getting-started/installation/)
The following command installs the project's base dependencies, the `docs` optional extra, and all dependency groups (`dev`, `examples`, and `tests`):

```bash
uv sync --all-extras --all-groups
```

## Unpack Data
Extract the `hydrofabric_builds_data.tar` archive to the `data` folder. This archive includes all data for running the canonical NHF for all domains.

## Run the hydrofabric build

Run the main build script directly with the CONUS example configuration:

```bash
uv run python scripts/hf_runner.py --config configs/example_config.yaml
```
This will output an NHF GPKG to the data folder.

The build process will depend on your CPU and memory availability. The network process can take 20+ minutes. There are over 60 divide attributes which can take 5+ hours - by far the longest step in the process. OCONUS domains are much shorter than CONUS. For debugging, you can turn on and off build steps and build off a pre-existing NHF network. Turning off divide attribute calculation will expediate things significantly.

For more details on running, explore the development documentation. For more details on the process and data, explore the builds documentation.
