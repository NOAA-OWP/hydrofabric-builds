import logging
from typing import Any, cast

import geopandas as gpd

from hydrofabric_builds.config import HFConfig
from hydrofabric_builds.hydrofabric.gages import gage_pipeline

logger = logging.getLogger(__name__)


def build_gages(**context: dict[str, Any]) -> dict[str, Any]:
    """Builds gages table. Builds gages dataset if not found in output location in gage configs

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

    gage_file = cfg.output_dir / cfg.gages.gages.target.out_gpkg

    gage_pipeline(cfg)

    gdf = gpd.read_file(gage_file)
    gdf.to_file(cfg.output_file_path, layer="gages", driver="GPKG", overwrite=True)
    logger.info("Saved gages layer.")
    return {"gages": "done"}
