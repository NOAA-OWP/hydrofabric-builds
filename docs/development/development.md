# Development Commands

Run these commands from the repository root.

## Install all dependencies

This repo is managed through [UV](https://docs.astral.sh/uv/getting-started/installation/)
The following command installs the project's base dependencies, the `docs` optional extra, and all dependency groups (`dev`, `examples`, and `tests`):

```bash
uv sync --all-extras --all-groups
```

Python 3.12 or newer is required.

## Unpack Data
Extract the `hydrofabric_builds_data.tar` archive to the `data` folder. This archive includes all data for running the canonical NHF for all domains.

## Run the hydrofabric build

Run the main build script directly with the CONUS example configuration:

```bash
uv run python scripts/hf_runner.py --config configs/example_config.yaml
```

Alternatively, use a domain-specific `just` recipe.

`just` calls series of commands called "recipes" similar to a `make` file. Install on linux with `apt get just` or follow linked readme for other platforms. After installing `just`, you can use the following commands to build the hydrofabric.

```bash
just build-conus
just build-ak
just build-hi
just build-prvi
```

Run the build with a custom configuration:

```bash
just build "configs/my_custom_config.yaml"
```

## Run tests

### Run the complete test suite

```bash
uv run pytest tests
```

### Run all tests in one module

```bash
uv run pytest tests/test_config.py
```

### Run one test in a module

```bash
uv run pytest tests/test_config.py::test_from_yaml_1
```

For a test method defined inside a class, include the class name in the pytest node ID. For example:

```bash
uv run pytest tests/test_graph.py::TestBuildGraphUnit::test_simple_linear_network
```

### Development
To ensure that hydrofabric-builds follows the specified structure, be sure to install the local dev dependencies and run `uv run pre-commit install`
