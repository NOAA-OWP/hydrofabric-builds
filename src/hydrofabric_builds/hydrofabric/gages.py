from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd
from pyprojroot import here

from hydrofabric_builds import HFConfig
from hydrofabric_builds.streamflow_gauges.assign_fp_to_gage import _crosswalk_fp_id, run_assignment
from hydrofabric_builds.streamflow_gauges.CIROH_UA_gages_upstream_area import fill_usgs_basin_from_csv
from hydrofabric_builds.streamflow_gauges.NLDI_upstream_area_builder import (
    attach_nldi_cache,
    build_nldi_cache,
)
from hydrofabric_builds.streamflow_gauges.TXDOT_gages_builder import txdot_read_file
from hydrofabric_builds.streamflow_gauges.usgs_gages_builder import (
    add_missing_usgs_sites,
    build_usgs_gages_from_kmz,
    merge_gage_xy_into_gages,
    merge_minimal_gages,
    merge_usgs_shapefile_into_gages,
)

logger = logging.getLogger(__name__)


def gage_pipeline(cfg: HFConfig) -> gpd.GeoDataFrame:
    """
    Build the unified `gages` GeoDataFrame from local sources.

    Parameters
    ----------
    config file : HFConfig
        gages config file.

    Returns
    -------
    GeoDataFrame
        The merged `gages` GeoDataFrame that was written to disk.
    """
    # ---------------------------------------------------------------------
    # 1) USGS discontinued (KMZ)
    # ---------------------------------------------------------------------
    """
    State gage file with kmz format can be downloaded from the following USGS link:
    https://waterwatch.usgs.gov/index.php?id=stategage

    choose "past flow/runoff"
    choose option "streamgage locations in KML"
    Then all files for all 50 states plus AK and PR can be downloaded. save them in a directory and point the
    following variable to that directory
    """
    gage_cfg = cfg.gages

    update_existing = gage_cfg.gages.target.update_existing
    exclude_ids = gage_cfg.gages.target.exclude_ids
    local_root = here() / "data" / "gages"
    usgs_discontinued_dir = local_root / gage_cfg.gages.inputs.usgs_discontinued.dir
    crs_usgs_discontinued = gage_cfg.gages.inputs.usgs_discontinued.gage_source_crs
    gages = build_usgs_gages_from_kmz(
        usgs_discontinued_dir, src_crs=crs_usgs_discontinued
    )  # scans all streamgages_*.kmz

    # ---------------------------------------------------------------------
    # 2) USGS live (SHP) — merge a set of known shapefiles
    # ---------------------------------------------------------------------
    usgs_active_main_dir = local_root / gage_cfg.gages.inputs.usgs_active.dir
    shp_file_paths = [
        usgs_active_main_dir / "mv01dstx_shp" / "mv01dstx.shp",
        usgs_active_main_dir / "pa01dstx_shp" / "pa01dstx.shp",
        usgs_active_main_dir / "pa07dstx_shp" / "pa07dstx.shp",
        usgs_active_main_dir / "pa14dstx_shp" / "pa14dstx.shp",
        usgs_active_main_dir / "realstx_shp" / "realstx.shp",
    ]
    for shp_path in shp_file_paths:
        # Skip quietly if a listed file isn't present
        if not shp_path.exists():
            logger.warning(f"[warn] USGS active shapefile not found, skipping: {shp_path}")
            continue
        gages = merge_usgs_shapefile_into_gages(
            gages=gages,
            shp_path=shp_path,
            update_existing=update_existing,
        )

    # ---------------------------------------------------------------------
    # 3) TXDOT (RDB/TXT) — append/update minimal mapped fields
    # ---------------------------------------------------------------------
    """
    TXDOT_sites = ["08030530","08031005",
    "08031020","08041788","08041790","08041940","08041945","08041970","08042455","08042468","08042470","08042515",
    "08042539","08064990","08065080","08065310","08065340","08065420","08065700","08065820","08065925","08066087",
    "08066138","08066380","08067280","08067505","08067520","08067653","08068020","08068025","08070220","08070550",
    "08070900","08076990","08077110","08077640","08077670","08077888","08078400","08078890","08078910","08078935",
    "08097000","08098295","08100950","08102730","08108705","08108710","08109310","08110520","08111006","08111051",
    "08111056","08111070","08111080","08111085","08111090",'08111110',"08117375","08117403","08117857","08117858",
    "08162580","08163720","08163880","08163900","08164150","08164200","08164410","08167000","08169778","08173210",
    "08174545","08180990","08189298","08189320","08189520","08189585","08189590","08189718"]

    reading TXDOT sites from a .txt file downloaded from the following address.
    As of Oct 2025, it is not publicly available
    https://waterservices.usgs.gov/nwis/site/?format=rdb&siteStatus=all&sites=08030530,08031005,08031020,08041788,08041790,08041940,08041945,08041970,08042455,08042468,08042470,08042515,08042539,08064990,08065080,08065310,08065340,08065420,08065700,08065820,08065925,08066087,08066138,08066380,08067280,08067505,08067520,08067653,08068020,08068025,08070220,08070550,08070900,08076990,08077110,08077640,08077670,08077888,08078400,08078890,08078910,08078935,08097000,08098295,08100950,08102730,08108705,08108710,08109310,08110520,08111006,08111051,08111056,08111070,08111080,08111085,08111090,08111110,08117375,08117403,08117857,08117858,08162580,08163720,08163880,08163900,08164150,08164200,08164410,08167000,08169778,08173210,08174545,08180990,08189298,08189320,08189520,08189585,08189590,08189718
    """
    txdot_path = local_root / gage_cfg.gages.inputs.txdot_gages.path
    src_crs_txdot = gage_cfg.gages.inputs.txdot_gages.gage_source_crs
    if txdot_path.exists():
        gdf_TXDOT_gages = txdot_read_file(path=txdot_path, src_crs=src_crs_txdot)
        gages = merge_minimal_gages(
            gages=gages,
            source=gdf_TXDOT_gages,
            update_existing=update_existing,
        )
    else:
        logger.warning(f"TXDOT file not found, skipping: {txdot_path}")

    # ---------------------------------------------------------------------
    # 4) CADWR/ENVCA/AK/HI/PR & misc. XY CSVs
    # ---------------------------------------------------------------------
    gages_xy_path = local_root / gage_cfg.gages.inputs.CADWR_ENVCA.path
    src_crs = gage_cfg.gages.inputs.CADWR_ENVCA.gage_source_crs
    if gages_xy_path.exists():
        gages = merge_gage_xy_into_gages(
            gages=gages,
            gage_xy_csv=gages_xy_path,
            src_crs=src_crs,
            update_existing=update_existing,
            exclude_ids=exclude_ids,
            fill_value="-",
        )
    else:
        logger.warning(f"gages: CADWR_ENVCA file list not found, skipping: {gages_xy_path}")

    # ---------------------------------------------------------------------
    # 5) NWM calibration gages — ensure presence; fill missing via NWIS Site Service
    # ---------------------------------------------------------------------
    usgs_cal_gages_path = local_root / gage_cfg.gages.inputs.nwm_calib_gages.path
    if usgs_cal_gages_path.exists():
        usgs_cal_gages = pd.read_csv(usgs_cal_gages_path, header=0, dtype=str)  # sep="\t",
        keep_cols = ["Gage ID", "Agency"]
        usgs_cal_gages = usgs_cal_gages[keep_cols]
        usgs_cal_gages.columns = ["site_no", "Agency"]
        missed_gages = usgs_cal_gages.loc[
            ~usgs_cal_gages["site_no"].isin(gages["site_no"].astype(str).unique()), "site_no"
        ].tolist()
        if missed_gages:
            logger.info(f"gages: ({len(missed_gages)}) Calibration gages missing; attempting NWIS fetch...")
            gages_updated, usgs_ids, non_usgs, fetched_df = add_missing_usgs_sites(gages, missed_gages)
            logger.info(f"gages: USGS-style IDs fetched: {len(usgs_ids)}; non-USGS IDs: {len(non_usgs)}")
            if non_usgs:
                logger.info(f"gages: Non-USGS examples (not fetched): {non_usgs[:10]}")
            logger.info(f"gages: Added rows (gages): {len(gages_updated) - len(gages)}")
            logger.info(f"gages: total number of gages collected: {len(gages_updated)}")
            gages = gages_updated
    else:
        logger.warning(f"gages: NWM calibration list not found, skipping: {usgs_cal_gages_path}")

    # ---------------------------------------------------------------------
    # 6) Finding upstream area for USGS gages using API
    # ---------------------------------------------------------------------
    run_NLDI_upstream_basins = gage_cfg.NLDI_upstream_basins.run_NLDI_upstream_basins
    nldi_file_path = local_root / gage_cfg.NLDI_upstream_basins.path
    layer_polys = gage_cfg.NLDI_upstream_basins.layer_polys
    layer_points = gage_cfg.NLDI_upstream_basins.layer_points
    if run_NLDI_upstream_basins:
        build_nldi_cache(
            gages=gages,  # GeoDataFrame in EPSG:4326
            out_gpkg=nldi_file_path.as_posix(),
            layer_polys=layer_polys,
            layer_points=layer_points,
            keep_status=("USGS-active", "USGS-discontinued", "TXDOT"),
            work_crs="EPSG:5070",
            usgs_crs="EPSG:4326",
            use_threads=False,
            max_workers=32,
        )

    # ---------------------------------------------------------------------
    # 7) Assign NLDI basins column to gages
    # ---------------------------------------------------------------------
    if nldi_file_path.exists():
        gages = attach_nldi_cache(gages, nldi_file_path, layer_polys=layer_polys)
    else:
        gages["basin_area_km2"] = "none"

    # ---------------------------------------------------------------------
    # 8) Add upstream basin area from CIROH-UA csv file to gages
    # ---------------------------------------------------------------------
    cfg_CIROH_UA = gage_cfg.gages.inputs.CIROH_UA
    gages = fill_usgs_basin_from_csv(
        gages,
        csv_path=local_root / cfg_CIROH_UA.path,
        gage_col_csv=cfg_CIROH_UA.id_col_name,
        area_col_csv=cfg_CIROH_UA.area_col_name,
    )
    # ---------------------------------------------------------------------
    # 9) Assign flowpath to gages
    # ---------------------------------------------------------------------
    buffer_gage = gage_cfg.assign_fp_to_gages.buffer_m
    parallel = gage_cfg.assign_fp_to_gages.parallel
    max_workers = gage_cfg.assign_fp_to_gages.max_workers
    gages = run_assignment(
        gages=gages,
        flowpaths_path=Path(cfg.build.reference_flowpaths_path),
        flowpaths_layer="reference_flowpaths",
        flow_id_col="flowpath_id",
        buffer_m=buffer_gage,
        work_crs=gage_cfg.assign_fp_to_gages.work_crs,
        parallel=parallel,  ### serial or parallel
        max_workers=max_workers,  ### None: if serial
        tol=gage_cfg.assign_fp_to_gages.rel_err,
    )
    # ---------------------------------------------------------------------
    # 10) drop the columns we don't need
    # ---------------------------------------------------------------------
    keep_cols = ["site_no", "geometry", "status", "USGS_basin_km2", "ref_fp_id", "method_fp_to_gage"]
    gages = gages[keep_cols]
    # removing the gages that don't have flowpaths
    gages = gages.loc[gages["ref_fp_id"] == gages["ref_fp_id"]].reset_index(drop=True)
    gages["ref_fp_id"] = pd.to_numeric(gages["ref_fp_id"])

    # ---------------------------------------------------------------------
    # 11) Crosswalk ref_fp_id to fp_id
    # ---------------------------------------------------------------------
    gages = _crosswalk_fp_id(gages, cfg.output_file_path)

    # ---------------------------------------------------------------------
    # 12) Write final output and return
    # ---------------------------------------------------------------------
    output = cfg.output_dir / gage_cfg.gages.target.out_gpkg
    gpkg_layer_name = gage_cfg.gages.target.gpkg_layer_name
    gages = gages.to_crs(gage_cfg.gages.target.crs)
    gages.to_file(output, layer=gpkg_layer_name, driver="GPKG", overwrite=True)
    logger.info(f"Saved gages layer to {output}")
    return gages
