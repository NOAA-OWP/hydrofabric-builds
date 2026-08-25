"""Contains all code for building divide attributes in task"""

from typing import Any, cast

from hydrofabric_builds.config import HFConfig
from hydrofabric_builds.hydrofabric.divide_attributes import (
    divide_attributes_pipeline_parallel,
    divide_attributes_pipeline_single,
)


def build_divide_attributes(**context: dict[str, Any]) -> dict[str, Any]:
    """
    Builds divide attributes in parallel

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

    if cfg.divide_attributes.processes > 1:
        divide_attributes_pipeline_parallel(cfg.divide_attributes, processes=cfg.divide_attributes.processes)
    else:
        divide_attributes_pipeline_single(cfg.divide_attributes)

    return {"divide_attributes": "done"}
