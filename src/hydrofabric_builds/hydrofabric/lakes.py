import logging

import geopandas as gpd

from hydrofabric_builds.config import HFConfig
from hydrofabric_builds.helpers.flowpath_association import make_vfp_graph
from hydrofabric_builds.hydrofabric.utils import (
    _crosswalk_fp_nexus,
    _crosswalk_reference_to_vfp,
)
from hydrofabric_builds.lakes.hydraulics import _populate_hydraulics
from hydrofabric_builds.lakes.lakes import (
    _add_nrcs_to_source,
    _aggregate_lake_polygons,
    _assert_nwm_lakes,
    _associate_lake_flowpaths,
    _calculate_elevation__lhd,
    _calculate_elevation__nwm,
    _calculate_elevation__refres,
    _calculate_elevation__refwb,
    _calculate_elevation__ror,
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
    _set_run_of_river_flag,
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

    5. Run of River Dams: Manually delineated polygons for large RFC run-of-river dams

    6. Low-head dams: Crosswalked where possible. Small buffered points for low-head dams to be modeled as channels.

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
    elif (
        cfg.lakes.nwm.run
        or cfg.lakes.adhoc.run
        or cfg.lakes.ref_wb.run
        or cfg.lakes.ref_res.run
        or cfg.lakes.run_of_river.run
        or cfg.lakes.low_head_dams.run
    ) is False:
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
        # run NWM lakes:
        # - associate lake with flowpaths
        # - match NWM lakes to reference reservoirs to avoid including them multiple times
        # - calculate polygon and point elevation
        # - add source `NWM`
        # - append to working GDF list to be concatenated
        # - copy polygons if they exist
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
            # match NWM lakes to reference reservoirs to avoid including them multiple times
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
            gdf_nwm_lakes["source"] = "NWM"
            # add to gdf list that will be concatenated at end
            gdf_list.append(gdf_nwm_lakes)

            if cfg.lakes.nwm.flowpath_association_method == "polygon_outlet":
                lake_polys["nwm_lakes"] = inputs["nwm_lakes"].copy()
        else:
            # an empty dataframe including dam_id column is needed to filter the reference reservoirs
            gdf_nwm_lakes = gpd.GeoDataFrame(columns=["dam_id"])

        # ------------------------------------------------------
        # Reference Waterbodies
        # Any lakes needed from the reference waterbodies dataset as defined by other tables
        # ------------------------------------------------------
        # run reference waterbodies:
        # reference_waterbodies is a polygon dataset crosswalked to COMID/lake_id and used
        # to add more polygons to dataset
        # - prep ref WB by selecting what waterbodies are needed from each dataset that links to them
        # - associate polygons with flowpaths
        # - calculate polygon and point elevation
        # - add source `ref_wb`
        # - append to working GDF list to be concatenated
        # - copy polygons
        if cfg.lakes.ref_wb.run:
            logger.info("Running adhoc lakes found only in reference waterbodies")
            gdf_ref_wb = _prep_ref_wb(
                cfg,
                gdf_adhoc=inputs["adhoc"].copy(),
                gdf_usbr=inputs["usbr"].copy(),
                gdf_lhdi=inputs["low_head_dams_point"].copy(),
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
        # Low Head Dams
        # ------------------------------------------------------
        # run low-head dams (polygon dataset crosswalked to COMID/lake_id where possible else has a generated lake_id)
        # - select lowhead dam polygons that have NID in lake_id - these are not matched in other datasets
        # - associate polygons with flowpaths
        # - calculate polygon and point elevation
        # - add source `low_head_dam`
        # - append to working GDF list to be concatenated
        # - copy polygons
        if cfg.lakes.low_head_dams.run:
            logger.info("Running low head dams found only in reference waterbodies")
            # include only polygons with nid in lake_id
            gdf_lhdi_poly = inputs["low_head_dams_polygon"]
            gdf_lhdi_poly = gdf_lhdi_poly[gdf_lhdi_poly["lake_id"].str.contains("nid")].copy()
            # input the polygon for polygon association - it will return centroid points
            gdf_lhdi_pts = _associate_lake_flowpaths(
                cfg,
                "low_head_dams",
                gdf=gdf_lhdi_poly,
                graph=vfp_graph,
                graph_id_to_idx=vfp_graph_id_to_idx,
                gdf_vfp=inputs["virtual_flowpaths"],
            )
            gdf_lhdi_pts = _calculate_elevation__lhd(
                cfg,
                gdf_lhdi_pts=gdf_lhdi_pts,
                gdf_lhdi_orig=gdf_lhdi_poly,
            )
            gdf_lhdi_pts["source"] = "low_head_dam"
            gdf_list.append(gdf_lhdi_pts)
            lake_polys["low_head_dams"] = gdf_lhdi_poly.copy()
        else:
            gdf_lhdi_poly = gpd.GeoDataFrame(columns=["dam_id"])

        # ------------------------------------------------------
        # Run of River Dams
        # ------------------------------------------------------
        #  large RFC run of river dams (polygon dataset crosswalked to COMID/lake_id where possible
        #   else has a generated lake_id)
        #  Crosswalked to NWPS / RFC location ID for use in reservoir DA
        # - select run of river polygons that have ror in lake_id - these are not matched in other datasets
        # - associate polygons with flowpaths
        # - calculate polygon and point elevation
        # - add source `run_of_river`
        # - append to working GDF list to be concatenated
        # - copy polygons
        if cfg.lakes.run_of_river.run:
            logger.info("Running run of river dams")
            gdf_ror_poly = inputs["run_of_river"]
            gdf_ror_poly = gdf_ror_poly[gdf_ror_poly["lake_id"].str.contains("ror")].copy()
            # input the polygon for polygon association - it will return centroid points
            gdf_ror_pts = _associate_lake_flowpaths(
                cfg,
                "run_of_river",
                gdf=gdf_ror_poly,
                graph=vfp_graph,
                graph_id_to_idx=vfp_graph_id_to_idx,
                gdf_vfp=inputs["virtual_flowpaths"].copy(),
            )

            gdf_ror_pts = _calculate_elevation__ror(
                cfg,
                gdf_ror_pts=gdf_ror_pts,
                gdf_ror_orig=gdf_ror_poly,
                surface_area_field=cfg.lakes.run_of_river.nid_surface_area_field,
            )
            gdf_ror_pts["source"] = "run_of_river"
            gdf_list.append(gdf_ror_pts)
            lake_polys["run_of_river"] = gdf_ror_poly.copy()
        else:
            gdf_ror_poly = gpd.GeoDataFrame(columns=["dam_id"])
        # ------------------------------------------------------
        # Reference Reservoirs
        # Filter and exclude reservoirs already used by nwm lakes and waterbodies
        # ------------------------------------------------------
        # reference reservoirs: point dataset crosswalked to lake_id/COMID
        # - filter reference reservoir to meeting configurable criteria
        # - calculate polygon (from reference waterbodies) and point elevation
        # - set source to `ref_res`
        # - append to working GDF list to be concatenated
        # - copy polygons
        if cfg.lakes.ref_res.run:
            logger.info("Running reference reservoirs")
            gdf_ref_res = _filter_ref_res(
                cfg,
                gdf_ref_res=inputs["ref_res"].copy(),
                gdf_nwm=gdf_nwm_lakes,
                gdf_ref_wb=gdf_ref_wb,
            )
            gdf_ref_res = _calculate_elevation__refres(
                cfg, gdf_ref_res=gdf_ref_res, gdf_wb_poly=inputs["ref_wb"].copy()
            )
            gdf_ref_res = _crosswalk_reference_to_vfp(hf_path=cfg.output_file_path, gdf=gdf_ref_res)
            gdf_ref_res["source"] = "ref_res"
            gdf_list.append(gdf_ref_res)
            lake_polys["ref_wb"] = inputs["ref_wb"].copy()

        # ------------------------------------------------------
        # Concat all lakes
        # ------------------------------------------------------
        # concatenate all lakes from processed gdf list
        logger.info("All lakes source files run. Concatenating lakes.")
        gdf_all_lks = _concat_lakes(cfg, gdf_list)

        # ------------------------------------------------------
        # Deduplicate by lake_id (COMID) across sources
        # ------------------------------------------------------
        # deduplicate lake_id based on hydrosequence priority for nwm lakes
        # and source priority for all other lakes
        logger.info("Deduplicating lake_id (COMID) across sources")
        gdf_all_lks = _dedup_lake_id(cfg, gdf_all_lks)

        # ------------------------------------------------------
        # Join National Inventory of Dams (NID) Attributes
        # ------------------------------------------------------
        # Join NID attributes to lakes that are non-NWM
        # NWM lakes do not have NID attributes retained
        # see function for more details on de-duplication in joining
        logger.info("Joining lakes to NID")
        gdf_all_lks = _join_nid(cfg, gdf_all_lks, inputs["nid"].copy())

        # ------------------------------------------------------
        # Add NRCS flag to source column
        # ------------------------------------------------------
        # if NID data is available, add NRCS flag if owner is NRCS
        nid_df = inputs["nid"].copy()
        if not nid_df.empty:
            gdf_all_lks = _add_nrcs_to_source(nid_df, gdf_all_lks)
        # ------------------------------------------------------
        # Add run of river flag
        # ------------------------------------------------------
        gdf_all_lks = _set_run_of_river_flag(gdf_all_lks)

        # ------------------------------------------------------
        # Hydraulics
        # ------------------------------------------------------
        # populate hydraulic parameters. Retain pre-set attributes
        # Calculates new attributes with NID data if present
        logger.info("Calculating hydraulic parameters")
        gdf_all_lks = _populate_hydraulics(gdf_all_lks)

        # ------------------------------------------------------
        # Finalize Table
        # ------------------------------------------------------
        logger.info("Finalizing lakes layer")
        # crosswalk flowpaths and nexuses using the reference lookup
        gdf_all_lks = _crosswalk_fp_nexus(
            gdf=gdf_all_lks,
            hf_ref=inputs["hf_ref"].copy(),
            vfp=inputs["virtual_flowpaths"].copy(),
            fp=inputs["flowpaths"].copy(),
        )
        # Override Great Lakes fp_id and virtual_fp_id with hardcoded values
        if cfg.res_da.great_lakes:
            gdf_all_lks = _override_great_lakes(gdf=gdf_all_lks, mapping=cfg.lakes.great_lakes)

        # create nhf_lake_id: these are spatially based from points
        gdf_all_lks = _create_ids(gdf=gdf_all_lks)
        # filter columns to final requested fields (change in cfg.lakes.fields if you need more)
        gdf_all_lks = _filter_columns(gdf=gdf_all_lks, fields=cfg.lakes.fields)

        # cache lakes file
        gdf_all_lks.to_file(cfg.lakes.lakes_path, layer="lakes", driver="GPKG", overwrite=True)

        # assert all NWM lakes included if run
        if cfg.lakes.nwm.run:
            _assert_nwm_lakes(cfg, gdf_all_lks)

        # create NWM lakes polygons layer from polygon list
        if lake_polys:
            gdf_polygons = _aggregate_lake_polygons(cfg, lake_polys=lake_polys, lake_points=gdf_all_lks)
            gdf_polygons.to_file(
                cfg.output_file_path,
                layer="lakes_polygons",
                driver="GPKG",
                overwrite=True,
            )

        # save lakes to NHF
        gdf_all_lks.to_file(cfg.output_file_path, layer="lakes", driver="GPKG", overwrite=True)
