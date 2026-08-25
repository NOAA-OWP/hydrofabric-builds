"""Contains all code for building waterbodies in task"""

import logging
from typing import Any, cast

from hydrofabric_builds.config import HFConfig
from hydrofabric_builds.hydrofabric.waterbodies import crosswalk_waterbodies, rfc_da_pipeline
from hydrofabric_builds.reservoirs.data_prep.nwm_lakes import prep_nwm_lakes

logger = logging.getLogger(__name__)


def build_waterbodies(**context: dict[str, Any]) -> dict[str, Any]:
    """Builds waterbodies table

    Builds RFC-DA dataset if not found in output location in reservoir configs

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

    rfcda_file = cfg.waterbodies.rfcda_file

    if not rfcda_file.exists():
        logger.info(f"RFC-DA file not found at {rfcda_file}. Running RFC-DA pipeline.")
        if cfg.waterbodies.nwm_lakes.prep_nwm_lakes or not cfg.waterbodies.nwm_lakes.output_path.exists():
            logger.info("Prepping HF 2.2/NWM lakes for inclusion in RFC-DA")
            prep_nwm_lakes(
                lakes_path=cfg.waterbodies.nwm_lakes.input_path,
                lakes_layer=cfg.waterbodies.nwm_lakes.layer,
                ref_wb_path=cfg.waterbodies.refwb.path,
                ref_fp_path=cfg.build.reference_flowpaths_path,
                buffer_size_m=cfg.waterbodies.nwm_lakes.buffer_size_m,
                output=cfg.waterbodies.nwm_lakes.output_path,
            )
            logger.info("NWM lakes prepared")

        rfc_da_pipeline(cfg.waterbodies, cfg.build)

        assert rfcda_file.exists(), "RFC-DA pipeline was run, but output file not found"

        logger.info(f"RFC-DA file created: {rfcda_file}.")

    logger.info(f"Using RFC-DA file found at {rfcda_file}.")
    gdf = crosswalk_waterbodies(cfg.output_file_path, rfcda_file)

    gdf.to_file(cfg.output_file_path, layer="waterbodies", driver="GPKG", overwrite=True)
    logger.info("Saved waterbodies layer")

    return {"waterodies": "done"}
