"""Contains all code for building reservoir DA table in task"""

import logging
from typing import Any, cast

import geopandas as gpd

from hydrofabric_builds.config import HFConfig
from hydrofabric_builds.hydrofabric.reservoir_da import res_da_pipeline

logger = logging.getLogger(__name__)


def build_reservoir_da(**context: dict[str, Any]) -> dict[str, Any]:
    """Builds reservoir DA layer

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

    df = res_da_pipeline(cfg)
    gpd.GeoDataFrame(df).to_file(cfg.output_file_path, layer="reservoir_da")

    return {"reservoir_da": "done"}
