from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from hydrofabric_builds.reservoirs.data_prep.rfc_da import build_rfc_da_hydraulics
from hydrofabric_builds.schemas.hydrofabric import BuildHydrofabricConfig, WaterbodiesConfig

logger = logging.getLogger(__name__)


def rfc_da_pipeline(cfg: WaterbodiesConfig, cfg_ref: BuildHydrofabricConfig) -> None:
    """The main file entering reservoir attributes calculation"""
    hydr = build_rfc_da_hydraulics(
        dem_path=cfg.dem.path,
        ref_reservoirs_path=cfg.refres.path,
        ref_wb_path=cfg.refwb.path,
        osm_ref_wb_path=cfg.osm.path,
        nid_clean_path=cfg.nid.path,  # or .parquet
        hf_lakes_path=cfg.nwm_lakes.output_path,
        ref_fp_path=cfg_ref.reference_flowpaths_path,
        max_waterbody_nearest_dist_m=cfg.rules.max_waterbody_nearest_dist_m,
        min_area_sqkm=cfg.rules.min_area_sqkm,
        out_dir=cfg.output_dir,
        out_rfcda=cfg.rfcda_file,
        work_crs=cfg.work_crs,
        default_crs=cfg.default_src_crs,
        use_hazard=True,
        lakes_keep=cfg.nwm_lakes.lakes_keep,
        res_keep=cfg.refres.ref_res_keep,
    )
    logger.info(f"[OK] attributes have been estimated for {len(hydr)} reservoirs")
    del hydr


def crosswalk_waterbodies(hf_path: Path, rfcda_path: Path) -> gpd.GeoDataFrame:
    """Crosswalks RFC-DA reservoirs with reference flowpaths and NHF flowpaths. Saves waterbodies table.

    Parameters
    ----------
    hf_path : Path
        Path to input hydrofabric gpkg
    rfcda_path : Path
        Path to RFC-DA gpkg

    """
    # read rfcda
    gdf_res = gpd.read_file(rfcda_path)

    # read HF ref ID cross walk table
    hf_ref = gpd.read_file(hf_path, layer="reference_flowpaths")
    hf_fp = gpd.read_file(hf_path, layer="flowpaths")
    hf_vfp = gpd.read_file(hf_path, layer="virtual_flowpaths")

    # Deduplicate ref FPs: keep the most downstream VFP per ref_fp_id
    if "segment_order" in hf_ref.columns:
        best_idx = hf_ref.groupby("ref_fp_id")["segment_order"].idxmax()
        hf_ref = hf_ref.loc[best_idx]

    # join on cross walk table
    logger.info("Crosswalking reference flowpath IDs")
    gdf_res = gdf_res.loc[~gdf_res["ref_fab_fp"].isnull(), :].copy()
    gdf_res["ref_fab_fp"] = pd.to_numeric(gdf_res["ref_fab_fp"]).astype(np.int64)
    gdf_res = gdf_res.merge(hf_ref, left_on="ref_fab_fp", right_on="ref_fp_id", how="left")
    gdf_res = gdf_res.merge(hf_fp[["fp_id", "dn_nex_id"]], on="fp_id", how="left")
    gdf_res = gdf_res.merge(hf_vfp[["virtual_fp_id", "dn_virtual_nex_id"]], on="virtual_fp_id", how="left")
    gdf_res = gdf_res.loc[~gdf_res["fp_id"].isnull() | ~gdf_res["virtual_fp_id"].isnull(), :].copy()
    gdf_res["wb_id"] = range(1, gdf_res.shape[0] + 1)

    # select final attribute list
    gdf_res = gdf_res[
        [
            "wb_id",
            "ref_fp_id",
            "fp_id",
            "virtual_fp_id",
            "dn_nex_id",
            "dn_virtual_nex_id",
            "div_id",
            "dam_id",
            "dam_name",
            "dam_type",
            "LkArea",
            "LkMxE",
            "WeirC",
            "WeirL",
            "WeirE",
            "OrficeC",
            "OrficeA",
            "OrficeE",
            "Dam_Length",
            "ifd",
            "geometry",
        ]
    ]

    return gdf_res
