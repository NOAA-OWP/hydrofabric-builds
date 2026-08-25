import logging
import sqlite3
from pathlib import Path
from typing import Any, cast

import pandas as pd

from hydrofabric_builds.config import HFConfig
from hydrofabric_builds.hydrofabric.fp_crosswalk import fp_crosswalk_pipeline

logger = logging.getLogger(__name__)


def build_fp_crosswalk(**context: dict[str, Any]) -> dict[str, Any]:
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
    output_path = Path(cfg.output_file_path)
    fp_crosswalk_output_path = Path(cfg.fp_crosswalk.outputs.path)

    if not fp_crosswalk_output_path.exists():
        fp_crosswalk_pipeline(cfg)

    df = pd.read_parquet(fp_crosswalk_output_path)
    conn = sqlite3.connect(output_path)
    df.to_sql("nhd", conn, index=False, if_exists="replace")
    conn.close()
    logger.info("Saved flowpath crosswalk table.")
    return {"fp_crosswalk": "done"}
