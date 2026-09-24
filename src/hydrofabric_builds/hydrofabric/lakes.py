import logging

import geopandas as gpd

from hydrofabric_builds.config import HFConfig
from hydrofabric_builds.helpers.flowpath_association import make_vfp_graph
from hydrofabric_builds.hydrofabric.utils import _crosswalk_fp_nexus, _crosswalk_reference_to_vfp
from hydrofabric_builds.lakes.hydraulics import _populate_hydraulics
from hydrofabric_builds.lakes.lakes import (
    _aggregate_lake_polygons,
    _assert_nwm_lakes,
    _associate_lake_flowpaths,
    _calculate_elevation__nwm,
    _calculate_elevation__refres,
    _calculate_elevation__refwb,
    _concat_lakes,
    _create_ids,
    _dedup_lake_id,
    _filter_columns,
    _filter_ref_res,
    _fold_ref_res_to_nwm_lakes,
    _join_nid,
    _override_great_lakes,
    _prep_ref_wb,
    _read_inputs,
)

logger = logging.getLogger(__name__)


def lakes_pipeline(cfg: HFConfig) -> None:
    """Pipeline to run lakes.

    Can use cached lakes or generate from any combination of NWM lakes, adhoc lakes, reference waterbodies, and reference reservoirs
    by selecting "run: True" in config. By default all files are expected to be present.
    If no lake methods chosen, a blank layer will be writte

    1. NWM Lakes: Polygon dataset will be associated with reference flowpaths. Point location will be snapped to nearby
    reference reservoirs if available.

    2. Adhoc Lakes: A GPKG of any additional lakes that must be included. They will be filtered to find any lakes that are only in reference
    waterbodies. If there is no way to associate with a polygon, lakes will be discarded.

    3. Reference Waterbodies: Reference Waterbodies are included if they are requested in Adhoc Lakes.

    4. Reference Reservoirs: Reference reservoirs are filtered based on config logic (minimum area, max distance from flowpath).
    Reference reservoirs with COMID in NWM lakes will be excluded. Reservoirs must be associated with a reference waterbody to be
    included.

    National Inventory of Dams (NID): NID data is joined when available. This includes dam characteristics and improves parameters.

    Elevation: Elevation is calculated separately for each dataset due to differences in point/polygon and filling missing data.

    Hydraulics: Hydraulics are calculated based on the best available data. See hydaulics.py for details on hydraulic calculations.
    If fields are already present, they are retained. Then, based on available NID and elevation, values are derived. If data is not
    available, defaults are used.

    Lakes are subset to the requested fields, crosswalked to nexus and flowpaths, and assigned a global nhf_lake_id.

    Parameters
    ----------
    cfg : HFConfig
        HF Config
    """
    # use cached lakes if requested
    if cfg.lakes.use_cached_lakes:
        logger.info("Using cached lakes file to build lakes layer.")
        gdf = gpd.read_file(cfg.lakes.lakes_path)
        gdf.to_file(cfg.output_file_path, layer="lakes", driver="GPKG", overwrite=True)

    # if no lake types were selected to run, write blank layer
    elif (cfg.lakes.nwm.run or cfg.lakes.adhoc.run or cfg.lakes.ref_wb.run or cfg.lakes.ref_res.run) is False:
        gdf = gpd.GeoDataFrame(columns=cfg.lakes.fields + ["geometry"], crs=cfg.crs)
        gdf.to_file(cfg.output_file_path, layer="lakes", driver="GPKG", overwrite=True)
        logger.info("No lake types were selected to run. Wrote blank layer to lakes.")

    # else run pipeline
    else:
        gdf_list = []
        lake_polys = {}
        inputs = _read_inputs(cfg)
        vfp_graph, vfp_graph_id_to_idx = make_vfp_graph(
            vfp=inputs["virtual_flowpaths"], vn=inputs["virtual_nexus"]
        )

        # ------------------------------------------------------
        # NWM lakes
        # ------------------------------------------------------
        if cfg.lakes.nwm.run:
            logger.info("Running NWM lakes")
            gdf_nwm_lakes = _associate_lake_flowpaths(
                cfg,
                "nwm",
                gdf=inputs["nwm_lakes"].copy(),
                graph=vfp_graph,
                graph_id_to_idx=vfp_graph_id_to_idx,
                gdf_vfp=inputs["virtual_flowpaths"].copy(),
            )
            # improve placement with reference reservoirs
            gdf_nwm_lakes = _fold_ref_res_to_nwm_lakes(
                cfg,
                nwm_lakes_pt=gdf_nwm_lakes,
                ref_res=inputs["ref_res"].copy(),
                hf_ref=inputs["hf_ref"].copy(),
                fp=inputs["flowpaths"].copy(),
            )
            gdf_nwm_lakes = _calculate_elevation__nwm(
                cfg, gdf_nwm_pts=gdf_nwm_lakes, gdf_nwm_orig=inputs["nwm_lakes"].copy()
            )

            gdf_list.append(gdf_nwm_lakes)

            if cfg.lakes.nwm.flowpath_association_method == "polygon_outlet":
                lake_polys["nwm_lakes"] = inputs["nwm_lakes"].copy()
        else:
            # an empty dataframe including dam_id column is needed to filter the reference reservoirs
            gdf_nwm_lakes = gpd.GeoDataFrame(columns=["dam_id"])

        # ------------------------------------------------------
        # Reference Waterbodies
        # Any lakes needed from the reference waterbodies dataset as defined by Adhoc Table
        # ------------------------------------------------------
        if cfg.lakes.ref_wb.run:
            logger.info("Running adhoc lakes found only in reference waterbodies")
            gdf_ref_wb = _prep_ref_wb(
                cfg,
                gdf_adhoc=inputs["adhoc"].copy(),
                gdf_usbr=inputs["usbr"].copy(),
                gdf_ref_res=inputs["ref_res"].copy(),
                gdf_wb_polys=inputs["ref_wb"].copy(),
            )
            gdf_ref_wb = _associate_lake_flowpaths(
                cfg,
                "ref_wb",
                gdf=gdf_ref_wb,
                graph=vfp_graph,
                graph_id_to_idx=vfp_graph_id_to_idx,
                gdf_vfp=inputs["virtual_flowpaths"],
            )
            gdf_ref_wb = _calculate_elevation__refwb(
                cfg, gdf_refwb_pts=gdf_ref_wb, gdf_refwb_poly=inputs["ref_wb"].copy()
            )
            gdf_list.append(gdf_ref_wb)
            lake_polys["ref_wb"] = inputs["ref_wb"].copy()
        else:
            # an empty dataframe including dam_id column is needed to filter the reference reservoirs
            gdf_ref_wb = gpd.GeoDataFrame(columns=["dam_id"])

        # ------------------------------------------------------
        # Reference Reservoirs
        # Filter and exclude reservoirs already used by nwm lakes and waterbodies
        # ------------------------------------------------------
        if cfg.lakes.ref_res.run:
            logger.info("Running reference reservoirs")
            gdf_ref_res = _filter_ref_res(
                cfg, gdf_ref_res=inputs["ref_res"].copy(), gdf_nwm=gdf_nwm_lakes, gdf_ref_wb=gdf_ref_wb
            )
            gdf_ref_res = _calculate_elevation__refres(
                cfg, gdf_ref_res=gdf_ref_res, gdf_wb_poly=inputs["ref_wb"].copy()
            )
            gdf_ref_res = _crosswalk_reference_to_vfp(hf_path=cfg.output_file_path, gdf=gdf_ref_res)
            gdf_list.append(gdf_ref_res)
            lake_polys["ref_wb"] = inputs["ref_wb"].copy()

        # ------------------------------------------------------
        # Concat all lakes
        # ------------------------------------------------------
        logger.info("All lakes source files run. Concatenating lakes.")
        gdf_all_lks = _concat_lakes(cfg, gdf_list)

        # ------------------------------------------------------
        # Deduplicate by lake_id (COMID) across sources
        # ------------------------------------------------------
        logger.info("Deduplicating lake_id (COMID) across sources")
        gdf_all_lks = _dedup_lake_id(cfg, gdf_all_lks)

        # ------------------------------------------------------
        # Join National Inventory of Dams (NID) Attributes
        # ------------------------------------------------------
        logger.info("Joining lakes to NID")
        gdf_all_lks = _join_nid(cfg, gdf_all_lks, inputs["nid"].copy())
        # ------------------------------------------------------
        # Hydraulics
        # ------------------------------------------------------
        logger.info("Calculating hydraulic parameters")
        gdf_all_lks = _populate_hydraulics(gdf_all_lks)

        # ------------------------------------------------------
        # Finalize Table
        # ------------------------------------------------------
        logger.info("Finalizing lakes layer")
        gdf_all_lks = _crosswalk_fp_nexus(
            gdf=gdf_all_lks,
            hf_ref=inputs["hf_ref"].copy(),
            vfp=inputs["virtual_flowpaths"].copy(),
            fp=inputs["flowpaths"].copy(),
        )
        # Override Great Lakes fp_id and virtual_fp_id with hardcoded values
        if cfg.res_da.great_lakes:
            gdf_all_lks = _override_great_lakes(gdf=gdf_all_lks, mapping=cfg.lakes.great_lakes)
        gdf_all_lks = _create_ids(gdf=gdf_all_lks)
        gdf_all_lks = _filter_columns(gdf=gdf_all_lks, fields=cfg.lakes.fields)

        # cache lakes file and save to NHF
        gdf_all_lks.to_file(cfg.lakes.lakes_path, layer="lakes", driver="GPKG", overwrite=True)

        # assert all NWM lakes included if run
        if cfg.lakes.nwm.run:
            _assert_nwm_lakes(cfg, gdf_all_lks)

        # create NWM lakes polygons layer
        if lake_polys:
            gdf_polygons = _aggregate_lake_polygons(cfg, lake_polys=lake_polys, lake_points=gdf_all_lks)
            gdf_polygons.to_file(cfg.output_file_path, layer="lakes_polygons", driver="GPKG", overwrite=True)

        gdf_all_lks.to_file(cfg.output_file_path, layer="lakes", driver="GPKG", overwrite=True)
