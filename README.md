# hydrofabric-builds
Building Hydrofabric &amp; Processing Ancillary Data

<img style="display: block; margin-left: auto; margin-right: auto;" src="docs/img/hydrofabric.png" alt="hydrofabric" width="40%" height="40%"/>

# About the Data
## Schema

The following schema is the proposed data model for NGWPC hydrofabric datasets produced by this repo.

TODO: Update

<img style="display: block; margin-left: auto; margin-right: auto;" src="docs/img/nhf_v1.1.2_schema.png" alt="nhf_v1.1.2_schema.png" width="100%" height="100%"/>

## Flowpaths FACT Table

The central table (or FACT Table) is `Flowpaths`. Each `flowpath` has a downstream, and upstream `nexus` point, allowing for traversal of a river network through a single table. Additionally, there is a 1:1 relationship between `flowpath` and `divide`.

## NGEN Tables

The tables highlighted in green are the infomation needed for lumped modeling to take place. Lumped models require attributes, the shape of the `divide` that is being modeled, and a `nexus` point for flow to be aggregated to.

## Routing Tables

The tables highlighted in blue contain the information needed for routing at a high resolution. T-Route is expected to run at a fine-scale (~300m segments) with many `virtual_flowpaths`. Each virtual flowpath is delineated based on the reference fabric, and there should be a many -> one relationship between `virtual_flowpaths` and `flowpaths`, with some `virtual flowpaths` not being represented in the `flowpaths` table. These non-represented `flowpaths` have the parameter of `routing_segment` set to False, and will have flow estimated through flow-scaling.

The `reservoir_da` table encodes crosswalks between lakes and gages with an assigned data assimilation code. The `lakes_polygons` layer mirrors the traditional `lakes` point layer, but includes the polygon representation. This polygon representation is used to derive the flowpaths associated with lakes for routing. The `lake_vfp_crosswalk` table contains the intersection of lake polygons and virtual flowpaths so that T-route treats all lake flowpaths as lakes rather than channels.

## Reference Crosswalks

The NGWPC Hydrofabric is built using many reference materials:
- Reference Flowpaths
- Reference Reservoirs
- Reference Waterbodies
- NWM v3 Lakes
- National Inventory of Dams
- USGS/ENVCA/CADWR/TXDOT/RFC/USBR/USACE Streamflow Gages
- NHD+

To ensure `flowpaths` can be mapped to back to the materials that created them, each of the reference materials is mapped to `flowpaths`, `hydrolocations`, and `virtual flowpaths`. The following IDs pairings are used:

- Reference Flowpaths -> `ref_fp_id`
- Reference Reservoirs -> `dam_id`
- Reference Reservoirs -> `ref_fab_wb` is `lake_id` / NHD `COMID`
- Streamflow Gages -> `site_no`
- NHD+ -> `nhd_feature_id`

## Validation
The `validate_hf` task in the pipeline produces a JSON report called `nhf_{version}_validation.json`. This report details various metrics from the built product, such as: number of null divide attributes, number of attributes out of defined minimum and maxium range, and assertions that necessary lakes and gages are present and assigned to flowpaths.


## Visual Diagram
<img style="display: block; margin-left: auto; margin-right: auto;" src="docs/img/nhf_diagram.png" alt="NHF Diagram" width="100%" height="100%"/>


# Development Commands

Run these commands from the repository root.

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

## Documentation
This repository has documentation that can be served via [mkdocs](https://www.mkdocs.org/).
Ensure that dependencies are installed:

```bash
uv sync --extra docs
```
To serve docs locally, run:

```bash
mkdocs serve -a localhost:8080
```
Navigate to `localhost:8080/` in your browser.


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
