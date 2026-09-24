"""Contains all code for building active NWM lakes in task"""

import logging
from typing import Any, cast

import geopandas as gpd

from hydrofabric_builds.config import HFConfig
from hydrofabric_builds.hydrofabric.lakes import lakes_pipeline
from hydrofabric_builds.lakes.lakes import crosswalk_vfp_lk

logger = logging.getLogger(__name__)


def build_lakes(**context: dict[str, Any]) -> dict[str, Any]:
    """Builds lakes layer from all NWM lake sources

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

    lakes_pipeline(cfg)

    if cfg.lakes.vfp_lk_crosswalk:
        lakes = gpd.read_file(cfg.output_file_path, layer="lakes")
        vfp = gpd.read_file(cfg.output_file_path, layer="virtual_flowpaths")
        logger.info("Crosswalking lakes and flowpaths")
        gdf_vfp_lk_crosswalk = crosswalk_vfp_lk(cfg, gdf_lakes=lakes, gdf_vfp=vfp)
        gdf_vfp_lk_crosswalk.to_file(
            cfg.output_file_path, layer="lake_vfp_crosswalk", driver="GPKG", overwrite=True
        )

    return {"lakes": "done"}
