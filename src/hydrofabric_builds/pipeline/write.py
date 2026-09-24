"""Contains all code for writing hydrofabric data"""

import logging
import sqlite3
from typing import Any, cast

import pandas as pd

from hydrofabric_builds.config import HFConfig
from hydrofabric_builds.task_instance import TaskInstance

logger = logging.getLogger(__name__)


def _normalize_id_types(df: pd.DataFrame) -> pd.DataFrame:
    """Convert integer ID columns to float64 for consistent GPKG output.

    GeoPackage (SQLite) has no native nullable integer type. When pandas writes
    Int64 (nullable) columns to GPKG and they contain
    nulls, reading them back yields float64. This function normalizes numeric
    ID columns to float64 so that the GPKG has uniform
    numeric types regardless of null presence.

    Parameters
    ----------
    df : pd.DataFrame
        Input DataFrame with potentially mixed integer dtypes.

    Returns
    -------
    pd.DataFrame
        Copy with integer ID columns cast to float64.
    """
    df = df.copy()
    for col in df.columns:
        if "id" not in col.lower():
            continue
        dtype = df[col].dtype
        if pd.api.types.is_integer_dtype(dtype):
            df[col] = df[col].astype("float64")
    return df


def write_base_hydrofabric(**context: dict[str, Any]) -> dict:
    """Writes the base hydrofabric layers to disk

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
    dict
        The reference flowpath and divides references in memory
    """
    cfg = cast(HFConfig, context["config"])
    ti = cast(TaskInstance, context["ti"])
    file_name = cfg.output_file_path
    file_name.unlink(missing_ok=True)  # deletes files that exist with the same name

    final_flowpaths = _normalize_id_types(
        ti.xcom_pull(task_id="trace_attributes", key="flowpaths_with_attributes")
    )
    final_divides = _normalize_id_types(ti.xcom_pull(task_id="reduce_base", key="divides"))
    final_nexus = _normalize_id_types(ti.xcom_pull(task_id="reduce_base", key="nexus"))
    final_virtual_flowpaths = _normalize_id_types(
        ti.xcom_pull(task_id="reduce_base", key="virtual_flowpaths")
    )
    final_virtual_nexus = _normalize_id_types(ti.xcom_pull(task_id="reduce_base", key="virtual_nexus"))
    final_reference_flowpaths = _normalize_id_types(
        ti.xcom_pull(task_id="reduce_base", key="reference_flowpaths")
    )

    final_divides.to_file(file_name, layer="divides", driver="GPKG")
    final_flowpaths.to_file(file_name, layer="flowpaths", driver="GPKG")
    final_nexus.to_file(file_name, layer="nexus", driver="GPKG")
    final_virtual_flowpaths.to_file(file_name, layer="virtual_flowpaths", driver="GPKG")
    final_virtual_nexus.to_file(file_name, layer="virtual_nexus", driver="GPKG")

    conn = sqlite3.connect(file_name)
    final_reference_flowpaths.to_sql("reference_flowpaths", conn, index=False)
    conn.close()

    logger.info(f"write_base task: wrote base geopackage layers to {file_name}")
    return {"base_file_path": file_name}
