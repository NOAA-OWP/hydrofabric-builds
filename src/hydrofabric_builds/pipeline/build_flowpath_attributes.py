"""Contains all code for building flowpath attributes in task"""

from typing import Any, cast

from hydrofabric_builds.config import HFConfig
from hydrofabric_builds.hydrofabric.flowpath_attributes import flowpath_attributes_pipeline


def build_flowpath_attributes(**context: dict[str, Any]) -> dict[str, Any]:
    """
    Builds flowpath attributes

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

    flowpath_attributes_pipeline(cfg.flowpath_attributes)

    return {"flowpath_attributes": "done"}
