# Architecture - Finding your Way Around

hydrofabric-builds is structured to run as an Apache airflow-style program with a runner script (`scripts/hf_runner.py`) and a list of tasks (`src/hydrofabric_builds/pipeline`). The pipeline folder calls a single or small number of functions to complete the pipeline step.

Pipeline steps refer to more detailed functions in `src/hydrofabric_builds/hydrofabric` and pipeline steps with significant data processing have additional folders (`src/hydrofabric_builds/lakes`, `streamflow_gauges`).

Other `util` style functions are found in `crosswalk` and `helpers`.

The `schemas` folder contains the important `hydrofabric.py` which includes all Pydantic data models and other constant enums for building hydrofabric pieces. All pipeline steps refer to various Pydantic models that store and validate input data.

`src/hydrofabric_builds/config.py` contains the higher level Pydantic model for the overarching hydrofabric config file and the task selection list. Any new pipeline step should be added here.

It is recommended to move all data needed to run the NHF to the `data` folder or use a symlink to do so. The config _should_ work with other root directories, but may require this set throughout different pipeline steps due to divering development over a short timeframe. Keeping the data all in `hydrofabric-builds/data` avoids this issue.

Note that this project was ended a year early: the authors would have liked things in cleaner, finalized states but time did not allow.
