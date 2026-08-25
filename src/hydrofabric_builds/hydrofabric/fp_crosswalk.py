from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from hydrofabric_builds import HFConfig
from hydrofabric_builds.crosswalk.fp.fp_crosswalk import build_crosswalk_from_files

logger = logging.getLogger(__name__)


def fp_crosswalk_pipeline(cfg: HFConfig) -> pd.DataFrame:
    """Build crosswalk table mapping NWM flow IDs to reference flowpath IDs."""
    fp_cfg = cfg.fp_crosswalk

    crosswalk = build_crosswalk_from_files(
        reference_path=Path(cfg.build.reference_flowpaths_path),
        nwm_path=fp_cfg.target.path,
        ref_id_col=fp_cfg.reference.id_col,
        nwm_id_col=fp_cfg.target.id_col,
        reference_layer=None,
        nwm_layer=fp_cfg.target.layer,
        work_crs=cfg.crs,
        search_radius_m=fp_cfg.search_radius_m,
        percent_inside_min=fp_cfg.percent_inside_min,
    )

    out_path = Path(fp_cfg.outputs.path)
    crosswalk.to_parquet(out_path, index=False)
    logger.info(f"Flowpath crosswalk written to {out_path}")

    return crosswalk
