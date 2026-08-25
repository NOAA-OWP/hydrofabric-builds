"""Contains all code for building divide attributes in task"""

import logging
from typing import Any, cast

from hydrofabric_builds.config import HFConfig
from hydrofabric_builds.helpers.spatial import domain_mask
from hydrofabric_builds.hydrofabric.divide_attributes import (
    divide_attributes_pipeline_parallel,
    divide_attributes_pipeline_single,
)

logger = logging.getLogger(__name__)


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
    div_cfg = cfg.divide_attributes

    if cfg.divide_attributes.processes > 1:
        divide_attributes_pipeline_parallel(div_cfg, processes=cfg.divide_attributes.processes)
    else:
        # Create divides mask if requested [note: should be re-created due to divides numbering changing on rebuild]
        if div_cfg.domain_mask:
            if div_cfg.divides_masked:
                logger.info("Creating domain mask for divides")
                domain_mask(
                    mask_path=div_cfg.domain_mask,
                    hf_path=div_cfg.hf_path,
                    output_path=div_cfg.divides_masked,
                    hf_layer="divides",
                    mask_layer="divides",
                )
                logger.info(f"Created domain mask: {div_cfg.divides_masked}")
            divide_attributes_pipeline_single(div_cfg)
        else:
            divide_attributes_pipeline_single(div_cfg)

    return {"divide_attributes": "done"}
