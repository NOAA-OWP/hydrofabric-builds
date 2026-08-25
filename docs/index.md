# Building Hydrofabric(s) and processing ancillary data

This repository stores data processing tools for building the Hydrofabric and ancillary data.

### Getting Started
This repo is managed through [UV](https://docs.astral.sh/uv/getting-started/installation/) and can be installed through:
```sh
uv sync
source .venv/bin/activate
```

### Development
To ensure that icefabric follows the specified structure, be sure to install the local dev dependencies and run `pre-commit install`

### Documentation
To build the user guide documentation for Icefabric locally, run the following commands:
```sh
uv sync --extra docs
mkdocs serve -a localhost:8080
```
Docs will be spun up at localhost:8080/
