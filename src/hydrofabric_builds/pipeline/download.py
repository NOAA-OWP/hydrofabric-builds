"""Contains all code for downloading hydrofabric data"""

import logging
from typing import Any, cast

import geopandas as gpd
import polars as pl

from hydrofabric_builds.config import HFConfig
from hydrofabric_builds.hydrofabric.graph import _validate_and_fix_geometries

logger = logging.getLogger(__name__)


def download_reference_data(**context: dict[str, Any]) -> dict[str, pl.DataFrame]:
    """Opens local / downloads reference materials for the hydrofabric build process

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

    Returns
    -------
    dict[str, gpd.GeoDataFrame]
        The reference flowpath and divides references in memory
    """
    cfg = cast(HFConfig, context["config"])

    _reference_divides = gpd.read_parquet(cfg.build.reference_divides_path)
    _reference_divides["divide_id"] = _reference_divides["divide_id"].astype("int").astype("str")
    _reference_divides = _validate_and_fix_geometries(_reference_divides, geom_type="divides")
    reference_divides = pl.from_pandas(_reference_divides.to_wkb())
    logger.info(f"download Task: Ingested Reference Divides from: {cfg.build.reference_divides_path}")

    _reference_flowpaths = gpd.read_parquet(cfg.build.reference_flowpaths_path)
    _reference_flowpaths["flowpath_id"] = _reference_flowpaths["flowpath_id"].astype("int").astype("str")
    _reference_flowpaths = _validate_and_fix_geometries(_reference_flowpaths, geom_type="flowpaths")
    reference_flowpaths = pl.from_pandas(_reference_flowpaths.to_wkb())
    logger.info(f"download Task: Ingested Reference Flowpaths from: {cfg.build.reference_flowpaths_path}")

    return {"reference_flowpaths": reference_flowpaths, "reference_divides": reference_divides}
