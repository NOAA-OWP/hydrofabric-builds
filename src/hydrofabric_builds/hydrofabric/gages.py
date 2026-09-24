from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd
from pyprojroot import here

from hydrofabric_builds import HFConfig
from hydrofabric_builds.hydrofabric.utils import _crosswalk_nexus, _crosswalk_reference
from hydrofabric_builds.streamflow_gauges.append_from_routelink import (
    append_from_routelink,
)
from hydrofabric_builds.streamflow_gauges.assign_fp_to_gage import override_flowpath_id, run_assignment
from hydrofabric_builds.streamflow_gauges.CIROH_UA_gages_upstream_area import fill_usgs_basin_from_csv
from hydrofabric_builds.streamflow_gauges.NLDI_upstream_area_builder import (
    attach_nldi_cache,
    build_nldi_cache,
)
from hydrofabric_builds.streamflow_gauges.TXDOT_gages_builder import txdot_read_file
from hydrofabric_builds.streamflow_gauges.usgs_gages_builder import (
    add_missing_usgs_sites,
    build_usgs_gages_from_kmz,
    merge_adhoc_lakes_gages,
    merge_canadian_great_lakes,
    merge_gage_xy_into_gages,
    merge_minimal_gages,
    merge_nid_gages,
    merge_rfc_gages,
    merge_usace,
    merge_usbr,
    merge_usgs_shapefile_into_gages,
)

logger = logging.getLogger(__name__)


def _required_local_path(local_root: Path, path: Path | None, input_name: str) -> Path:
    """Resolve a required gage input path beneath the local gage data root."""
    if path is None:
        raise ValueError(f"Gage input '{input_name}' requires a path")
    return local_root / path


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
    gage_cfg = cfg.gages
    local_root = here() / "data" / "gages"

    if gage_cfg.gages.prebuilt_gages:
        # -----------------------------------------------------------------
        # Read pre-built gages, skip collection + API steps (1-8)
        # -----------------------------------------------------------------
        gages = gpd.read_file(
            gage_cfg.gages.prebuilt_gages,
            layer=gage_cfg.gages.prebuilt_gages_layer,
        )
        required = ["site_no", "geometry", "USGS_basin_km2"]
        missing = [c for c in required if c not in gages.columns]
        if missing:
            raise ValueError(f"Pre-built gages missing required columns: {missing}")
        # Drop columns that steps 9-11 will regenerate to avoid duplicates
        stale = [
            c
            for c in ("ref_fp_id", "method_fp_to_gage", "fp_id", "virtual_fp_id", "hy_id")
            if c in gages.columns
        ]
        if stale:
            gages = gages.drop(columns=stale)
        if gages.crs and gages.crs.to_epsg() != 4326:
            gages = gages.to_crs("EPSG:4326")
        logger.info(f"Loaded {len(gages)} pre-built gages from {gage_cfg.gages.prebuilt_gages}")
    else:
        # -----------------------------------------------------------------
        # Steps 1-8: collect gages from local sources + APIs
        # -----------------------------------------------------------------

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

        update_existing = gage_cfg.gages.target.update_existing
        exclude_ids = gage_cfg.gages.target.exclude_ids
        usgs_discontinued_dir = _required_local_path(
            local_root, gage_cfg.gages.inputs.usgs_discontinued.dir, "usgs_discontinued.dir"
        )
        crs_usgs_discontinued = gage_cfg.gages.inputs.usgs_discontinued.gage_source_crs
        gages = build_usgs_gages_from_kmz(
            usgs_discontinued_dir, src_crs=crs_usgs_discontinued
        )  # scans all streamgages_*.kmz

        # ---------------------------------------------------------------------
        # 2) USGS live (SHP) — merge a set of known shapefiles
        # ---------------------------------------------------------------------
        usgs_active_main_dir = _required_local_path(
            local_root, gage_cfg.gages.inputs.usgs_active.dir, "usgs_active.dir"
        )
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
        txdot_path = _required_local_path(
            local_root, gage_cfg.gages.inputs.txdot_gages.path, "txdot_gages.path"
        )
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
        gages_xy_path = _required_local_path(local_root, gage_cfg.gages.inputs.other.path, "other.path")
        src_crs = gage_cfg.gages.inputs.other.gage_source_crs
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
            logger.warning(f"gages: 'other' file list not found, skipping: {gages_xy_path}")

        # ---------------------------------------------------------------------
        # 5) NWM calibration gages — ensure presence; fill missing via NWIS Site Service
        # ---------------------------------------------------------------------
        usgs_cal_gages_path = _required_local_path(
            local_root, gage_cfg.gages.inputs.nwm_calib_gages.path, "nwm_calib_gages.path"
        )
        if usgs_cal_gages_path.exists():
            usgs_cal_gages = pd.read_csv(usgs_cal_gages_path, header=0, dtype=str)  # sep="\t",
            keep_cols = ["Gage ID", "Agency"]
            usgs_cal_gages = usgs_cal_gages[keep_cols]
            usgs_cal_gages.columns = pd.Index(["site_no", "Agency"])
            missed_gages = usgs_cal_gages.loc[
                ~usgs_cal_gages["site_no"].isin(gages["site_no"].astype(str).unique()), "site_no"
            ].tolist()
            if missed_gages:
                logger.info(
                    f"gages: ({len(missed_gages)}) Calibration gages missing; attempting NWIS fetch..."
                )
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
        # 6) Add RFC gages from RFC, USACE, Adhoc, Canadian Great Lakes, USBR
        # ---------------------------------------------------------------------
        rfc_gages_path = _required_local_path(local_root, gage_cfg.gages.inputs.rfc.path, "rfc.path")
        nwm_rfc_path = local_root / gage_cfg.gages.inputs.nwm_rfc.path
        adhoc_path = _required_local_path(
            local_root, gage_cfg.gages.inputs.adhoc_lakes.path, "adhoc_lakes.path"
        )
        nid_path = _required_local_path(local_root, gage_cfg.gages.inputs.nid.path, "nid.path")
        usbr_path = _required_local_path(local_root, gage_cfg.gages.inputs.usbr.path, "usbr.path")
        usace_path = _required_local_path(local_root, gage_cfg.gages.inputs.usace.path, "usace.path")

        if rfc_gages_path.exists():
            gages = merge_rfc_gages(
                gages,
                rfc_path=rfc_gages_path,
                nwm_rfc_path=nwm_rfc_path,
                rfc_id_col=gage_cfg.gages.inputs.rfc.id_col_name,
                status_col=gage_cfg.gages.inputs.rfc.status_col_name,
                nwm_rfc_id=gage_cfg.gages.inputs.nwm_rfc.rfc_id_col,
                x_col=gage_cfg.gages.inputs.rfc.x_col_name,
                y_col=gage_cfg.gages.inputs.rfc.y_col_name,
                rfc_crs=gage_cfg.gages.inputs.rfc.gage_source_crs,
            )
        else:
            logger.info(f"gages: 'rfc' file not found, skipping: {rfc_gages_path}")

        if nid_path.exists():
            gages = merge_nid_gages(
                gages,
                nid_path=nid_path,
                nwm_rfc_path=nwm_rfc_path,
                nid_id_col=gage_cfg.gages.inputs.nid.id_col_name,
                nwm_usace_id=gage_cfg.gages.inputs.nwm_rfc.usace_id_col,
                x_col=gage_cfg.gages.inputs.nid.x_col_name,
                y_col=gage_cfg.gages.inputs.nid.y_col_name,
                nid_crs=gage_cfg.gages.inputs.nid.gage_source_crs,
            )
        else:
            logger.info(f"gages: 'nid' file not found, skipping: {nid_path}")

        if adhoc_path.exists():
            gages = merge_adhoc_lakes_gages(
                gages,
                adhoc_path=adhoc_path,
                adhoc_gage_id=gage_cfg.gages.inputs.adhoc_lakes.id_col_name,
                x_col=gage_cfg.gages.inputs.adhoc_lakes.x_col_name,
                y_col=gage_cfg.gages.inputs.adhoc_lakes.y_col_name,
                adhoc_crs=gage_cfg.gages.inputs.adhoc_lakes.gage_source_crs,
            )
        else:
            logger.info(f"gages: 'adhoc lakes' file list not found, skipping: {adhoc_path}")

        if gage_cfg.gages.inputs.canada_great_lakes:
            gages = merge_canadian_great_lakes(gages, great_lakes_cfg=cfg.lakes.great_lakes)

        if usbr_path.exists():
            gages = merge_usbr(
                gages, usbr_path=usbr_path, usbr_gage_id=gage_cfg.gages.inputs.usbr.id_col_name
            )
        else:
            logger.info(f"gages: 'usbr' file list not found, skipping: {usbr_path}")

        if usace_path.exists():
            gages = merge_usace(
                gages, usace_path=usace_path, usace_gage_id=gage_cfg.gages.inputs.usace.id_col_name
            )
        else:
            logger.info(f"gages: 'usace' file list not found, skipping: {usace_path}")

        # ---------------------------------------------------------------------
        # 6) Append RouteLink gages not already in set
        # ---------------------------------------------------------------------
        gages = append_from_routelink(
            gdf=gages,
            routelink=_required_local_path(
                local_root, gage_cfg.gages.inputs.routelink.path, "routelink.path"
            ),
            id_col_name=gage_cfg.gages.inputs.routelink.id_col_name,
            shape=None,
        )

        # ---------------------------------------------------------------------
        # 7) Finding upstream area for USGS gages using API
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
        # 8) Assign NLDI basins column to gages
        # ---------------------------------------------------------------------
        if nldi_file_path.exists():
            gages = attach_nldi_cache(gages, nldi_file_path, layer_polys=layer_polys)
        else:
            gages["basin_area_km2"] = "none"

        # ---------------------------------------------------------------------
        # 9) Add upstream basin area from CIROH-UA csv file to gages
        # ---------------------------------------------------------------------
        cfg_CIROH_UA = gage_cfg.gages.inputs.CIROH_UA
        gages = fill_usgs_basin_from_csv(
            gages,
            csv_path=_required_local_path(local_root, cfg_CIROH_UA.path, "CIROH_UA.path"),
            gage_col_csv=cfg_CIROH_UA.id_col_name,
            area_col_csv=cfg_CIROH_UA.area_col_name,
        )

    # ---------------------------------------------------------------------
    # 10) Assign flowpath to gages
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
    # 11) drop the columns we don't need
    # ---------------------------------------------------------------------
    keep_cols = ["site_no", "geometry", "status", "USGS_basin_km2", "ref_fp_id", "method_fp_to_gage"]
    gages = gages[keep_cols]
    # removing the gages that don't have flowpaths
    gages = gages.loc[gages["ref_fp_id"] == gages["ref_fp_id"]].reset_index(drop=True)
    gages["ref_fp_id"] = pd.to_numeric(gages["ref_fp_id"])

    # ---------------------------------------------------------------------
    # 12) Crosswalk ref_fp_id to fp_id
    # ---------------------------------------------------------------------
    gages = _crosswalk_reference(cfg.output_file_path, gages)

    # Update fp_id and virtual_fp_id before crosswalking their downstream nexuses.
    if gage_cfg.assign_fp_to_gages.override_fp_path:
        override_fp_path = _required_local_path(
            local_root, gage_cfg.assign_fp_to_gages.override_fp_path, "override_fp_path"
        )
        if override_fp_path.exists():
            override_fp = pd.read_csv(
                override_fp_path,
                dtype={"site_no": str, "fp_id": pd.Int64Dtype(), "virtual_fp_id": pd.Int64Dtype()},
            )
            gages = override_flowpath_id(gages, override_fp)
        else:
            logger.warning(f"Gage flowpath override CSV not found; skipping overrides: {override_fp_path}")

    gages = _crosswalk_nexus(cfg.output_file_path, gages)

    # ---------------------------------------------------------------------
    # 13) Write final output and return
    # ---------------------------------------------------------------------
    logger.info(f"total gages: {len(gages)}")
    output = cfg.output_dir / gage_cfg.gages.target.out_gpkg
    gpkg_layer_name = gage_cfg.gages.target.gpkg_layer_name
    gages = gages.to_crs(gage_cfg.gages.target.crs)
    gages.to_file(output, layer=gpkg_layer_name, driver="GPKG", overwrite=True)
    logger.info(f"Saved gages layer to {output}")
    return gages
