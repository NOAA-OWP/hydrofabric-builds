"""Contains all code for building hydrolocation in task"""

from typing import Any, cast

from hydrofabric_builds.config import HFConfig
from hydrofabric_builds.hydrofabric.hydrolocations import hydrolocations_pipeline


def build_hydrolocations(**context: dict[str, Any]) -> dict[str, Any]:
    """
    Builds hydrolocations table and updates waterbodies and gauges table with IDs

    Parameters
    ----------
    **context : dict
        Airflow-compatible context containing:
        - ti : TaskInstance for XCom operations
        - config : HFConfig with pipeline configuration
        - task_id : str identifier for this task
        - run_id : str identifier for this pipeline run
        - ds : str execution date
        - execution_date : datetime object

    """
    cfg = cast(HFConfig, context["config"])

    hydrolocations_pipeline(cfg.output_file_path)

    return {"hydrolocations": "done"}
