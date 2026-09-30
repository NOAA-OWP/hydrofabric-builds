"""Contains all functions for building lakes"""

import logging

import geopandas as gpd
import numpy as np
import pandas as pd
import rustworkx as rx

from hydrofabric_builds.config import HFConfig
from hydrofabric_builds.helpers.flowpath_association import (
    associate_flowpaths_nearest_point,
    associate_flowpaths_polygon_graph,
    join_attributes,
)
from hydrofabric_builds.lakes.helpers import point_elevation, polygon_elevation
from hydrofabric_builds.pipeline.processing import _encode_unique
from hydrofabric_builds.schemas.hydrofabric import (
    GreatLakesMapping,
)

logger = logging.getLogger(__name__)

# NID Parameters to be used in _join_nid
# NID reports every length in feet; the rest of the pipeline works in meters.
FT_TO_M = 0.3048
NID_LENGTH_FIELDS_FT = (
    "structural_height",
    "dam_height",
    "hydraulic_height",
    "nid_height",
    "dam_length",
    "spillway_width",
)

NID_TO_NHF = {
    "dam_length": "dam_crest_length_m",  # length along the top of the dam, spillway included
    "spillway_width": "spillway_width_m",  # width at max design pool; pipe diameter for pipe spillways
}


def _override_great_lakes(
    gdf: gpd.GeoDataFrame,
    mapping: GreatLakesMapping,
    lake_id_field: str = "lake_id",
) -> gpd.GeoDataFrame:
    """Override fp_id and virtual_fp_id for Great Lakes with hardcoded values.

    Parameters
    ----------
    gdf : gpd.GeoDataFrame
        Lakes dataframe
    mapping : GreatLakesMapping
        Great Lakes mapping with hardcoded fp_id and virtual_fp_id
    lake_id_field : str
        Field name for lake ID, by default "lake_id"

    Returns
    -------
    gpd.GeoDataFrame
        Updated lakes dataframe
    """
    great_lakes = mapping.model_dump()
    for _k, v in great_lakes.items():
        lake_id = v[lake_id_field]
        mask = gdf[lake_id_field].astype(str) == str(lake_id)
        if mask.any():
            gdf.loc[mask, "fp_id"] = v["fp_id"]
            gdf.loc[mask, "virtual_fp_id"] = v["virtual_fp_id"]
            logger.info(f"Overriding fp_id and virtual_fp_id for Great Lake {lake_id}")
    return gdf


def _read_inputs(cfg: HFConfig) -> dict[str, gpd.GeoDataFrame]:
    """Read commonly used inputs. Return an empty geodataframe if no file to support type checking throughout.

    Functions will test if gdf is empty.
    """
    inputs = {}

    inputs["nwm_lakes"] = (
        gpd.read_file(cfg.lakes.nwm.path, layer=cfg.lakes.nwm.layer).to_crs(cfg.crs)
        if cfg.lakes.nwm.path.exists()
        else gpd.GeoDataFrame(geometry=[], crs=cfg.crs)
    )
    inputs["nid"] = pd.read_csv(cfg.lakes.nid.path) if cfg.lakes.nid.path.exists() else pd.DataFrame()
    inputs["adhoc"] = (
        gpd.read_file(cfg.lakes.adhoc.path).to_crs(cfg.crs)
        if cfg.lakes.adhoc.path.exists()
        else gpd.GeoDataFrame(geometry=[], crs=cfg.crs)
    )
    inputs["usbr"] = (
        gpd.read_file(cfg.lakes.usbr.path).to_crs(cfg.crs)
        if cfg.lakes.usbr.path.exists()
        else gpd.GeoDataFrame(geometry=[], crs=cfg.crs)
    )
    inputs["low_head_dams_polygon"] = (
        gpd.read_file(cfg.lakes.low_head_dams.path, layer=cfg.lakes.low_head_dams.layer_poly).to_crs(cfg.crs)
        if cfg.lakes.low_head_dams.path.exists()
        else gpd.GeoDataFrame(geometry=[], crs=cfg.crs)
    )
    inputs["low_head_dams_point"] = (
        gpd.read_file(cfg.lakes.low_head_dams.path, layer=cfg.lakes.low_head_dams.layer_point).to_crs(cfg.crs)
        if cfg.lakes.low_head_dams.path.exists()
        else gpd.GeoDataFrame(geometry=[], crs=cfg.crs)
    )
    inputs["run_of_river"] = (
        gpd.read_file(cfg.lakes.run_of_river.path, layer=cfg.lakes.run_of_river.layer_polygon).to_crs(cfg.crs)
        if cfg.lakes.run_of_river.path.exists()
        else gpd.GeoDataFrame(geometry=[], crs=cfg.crs)
    )
    inputs["run_of_river_points"] = (
        gpd.read_file(cfg.lakes.run_of_river.path, layer=cfg.lakes.run_of_river.layer_points).to_crs(cfg.crs)
        if cfg.lakes.run_of_river.path.exists()
        else gpd.GeoDataFrame(geometry=[], crs=cfg.crs)
    )
    inputs["ref_res"] = (
        gpd.read_file(cfg.lakes.ref_res.path).to_crs(cfg.crs)
        if cfg.lakes.ref_res.path.exists()
        else gpd.GeoDataFrame(geometry=[], crs=cfg.crs)
    )
    inputs["ref_wb"] = (
        gpd.read_file(cfg.lakes.ref_wb.path).to_crs(cfg.crs)
        if cfg.lakes.ref_wb.path.exists()
        else gpd.GeoDataFrame(geometry=[], crs=cfg.crs)
    )
    inputs["hf_ref"] = gpd.read_file(cfg.output_file_path, layer="reference_flowpaths")
    inputs["virtual_flowpaths"] = gpd.read_file(cfg.output_file_path, layer="virtual_flowpaths")
    inputs["flowpaths"] = gpd.read_file(cfg.output_file_path, layer="flowpaths")
    inputs["virtual_nexus"] = gpd.read_file(cfg.output_file_path, layer="virtual_nexus")

    return inputs


def _associate_lake_flowpaths(
    main_cfg: HFConfig,
    lake_type: str,
    graph: rx.PyDiGraph,
    graph_id_to_idx: dict[str, int],
    gdf_vfp: gpd.GeoDataFrame,
    gdf: gpd.GeoDataFrame | None = None,
) -> gpd.GeoDataFrame:
    """Associate flowpaths and join attributes from source file if requested.

    An attribute file can be supplied to join pre-computed attributes to lakes.
    The associated file will be saved so that it can be picked up in pipeline runs.

    Parameters
    ----------
    main_cfg : HFConfig
        Main HF Config
    lake_type : str
        The string representing the name of the lake type in the LakesConfig (e.g. 'nwm', 'ref_wb')
    gdf : gpd.GeoDataFrame | None, optional
        An in memory GFF. If None, will read from the lake type config, by default None

    Returns
    -------
    gpd.GeoDataFrame
        FP-associated geodataframe
    """
    # Get the cfg for the requested lake type
    cfg = getattr(main_cfg.lakes, lake_type)
    # if a gdf is passed in, use it, if not read from config
    if gdf is None:
        try:
            gdf = gpd.read_file(cfg.path, layer=cfg.layer)
        except Exception as e:  # noqa: BLE001
            logger.info(
                f"Input file for lake type {lake_type} could not be read. Skipping flowpath association for {lake_type}. Exception: {e}"
            )
            # Return empty dataframe to cause rest of pipeline to work
            return gpd.GeoDataFrame(columns=["geometry", "lake_id"])

    # Preprocess lakes by associating with flowpaths if requested or if processed path does not exist
    if cfg.associate_flowpaths or not cfg.fp_associated_path.exists():
        # Use nearest point association method
        if cfg.flowpath_association_method == "nearest_point":
            logger.info(f"Associating {lake_type} flowpath with points")
            gdf = associate_flowpaths_nearest_point(
                gdf_points=gdf,
                gdf_flowpaths=gdf_vfp,
                search_radius_m=cfg.search_radius_m,
                point_id=cfg.id_field,
                flowpath_id="virtual_fp_id",
                flowpath_id_out_field="virtual_fp_id",
            )
        # use polygon flowpath outlet method
        elif cfg.flowpath_association_method == "polygon_outlet":
            logger.info(f"Associating {lake_type} flowpaths with polygons using graph method")
            logger.info("Building VFP graph")
            poly_id = cfg.id_field if cfg.id_field in gdf.columns else main_cfg.lakes.output_comid_field
            logger.info("Associating flowpaths")
            gdf = associate_flowpaths_polygon_graph(
                gdf_poly=gdf,
                graph=graph,
                gdf_vfp=gdf_vfp,
                id_to_idx=graph_id_to_idx,
                vfp_id="virtual_fp_id",
                poly_id=poly_id,
                intersection_length_min_m=cfg.intersection_length_min_m,
                buffer_size_m=main_cfg.lakes.flowpath_association_buffer_m,
            )

        # invalid method
        else:
            raise ValueError(f"Config contained invalid flowpath association method for {lake_type}")

        if cfg.attrib_src_path:
            gdf = join_attributes(
                gdf,
                attrib_dst_key=cfg.id_field,
                attrib_src_path=cfg.attrib_src_path,
                attrib_src_layer=cfg.attrib_src_layer,
                attrib_src_key=cfg.attrib_src_key,
                attrib_src_fields=cfg.fields.copy(),
                rename=True,
            )
        if main_cfg.lakes.output_comid_field not in gdf.columns:
            gdf = gdf.rename(columns={cfg.id_field: main_cfg.lakes.output_comid_field})

        if pd.api.types.is_numeric_dtype(gdf[main_cfg.lakes.output_comid_field]):
            gdf[main_cfg.lakes.output_comid_field] = (
                gdf[main_cfg.lakes.output_comid_field].astype(pd.Int64Dtype()).astype(pd.StringDtype())
            )

        # Save nwm_lakes layer to NHF
        if cfg.fp_associated_path.exists():
            gdf.to_file(cfg.fp_associated_path, layer="lakes", driver="GPKG", overwrite=True)
        else:
            gdf.to_file(cfg.fp_associated_path, layer="lakes", driver="GPKG")

    else:
        # read the pre-processed file to return
        gdf = gpd.read_file(cfg.fp_associated_path)

    return gdf


def _fold_ref_res_to_nwm_lakes(
    cfg: HFConfig,
    nwm_lakes_pt: gpd.GeoDataFrame,
    ref_res: gpd.GeoDataFrame,
    hf_ref: gpd.GeoDataFrame,
    fp: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Find NWM lakes that have a reference reservoir to exclude these reference reservoirs from selection

    Match reference reservoirs to NWM lakes based on buffering the outlet flowpath of lake polygon
    If reference reservoir is within max search distance (set in cfg.lakes.nwm.max_refres_search_distance_m),
    the NWM lake receives the dam_id and nid_id
    Point geometry is always retained due to problems with duplicate reference reservoirs found

    Algorithm:
    1. The flowpath geometry is spatially joined (nearest) to reference reservoirs with
    max search distance (meters)
    2. The closest (minimum distance) reference reservoir in the spatial join is selected.
    3. The NID and dam_id attributes for the NWM lake are replaced with the reference reservoir
    4. fp_id is used to join `hydrosequence` which is used in downstream code

    NOTE:
    An improved algorithm would use the VFP graph to ensure reference reservoirs are near outlets
    and avoid duplication of lakes sharing a reference reservoir
    This function used to change the placement of NWM lakes to the reference reservoir, but these
    duplicates caused problems creating the nhf_lake_id which uses point geometry to create ID
    and should be unique
    Hydrosequence is added here to support downstream work for de-duplication of NWM lakes

    Parameters
    ----------
    cfg : HFConfig
        Main HFConfig
    nwm_lakes_pt : gpd.GeoDataFrame
        NWM Lakes points with associated outlet flowpaths
    ref_res : gpd.GeoDataFrame
        Reference Reservoirs point dataset with dam attributes
    hf_ref : gpd.GeoDataFrame
        Table `reference_flowpaths` in NHF. Includes crosswalk between fp_id and virtual_fp_id
    fp : gpd.GeoDataFrame
        NHF flowpaths layer

    Returns
    -------
    gpd.GeoDataFrame
        Updated NWM lakes points
    """
    # only run if reference reservoirs are present and requested
    if cfg.lakes.nwm.improve_placement_path.exists() and cfg.lakes.nwm.use_cached_improve_placement:
        nwm_lakes_pt = gpd.read_file(cfg.lakes.nwm.improve_placement_path)
        return nwm_lakes_pt

    elif cfg.lakes.nwm.improve_placement_ref_res and not ref_res.empty:
        logger.info("Matching NWM lakes with reference reservoirs to remove them if duplicated.")
        ref_res = ref_res.to_crs(cfg.crs)
        max_distance = cfg.lakes.nwm.max_refres_search_distance_m
        nwm_lakes_pt[["dam_name", "dam_id", "nid"]] = [None, None, None]

        # merge ref_fp_id in
        nwm_lakes_pt = nwm_lakes_pt.merge(hf_ref[["fp_id", "virtual_fp_id"]], how="left", on="virtual_fp_id")

        # for each lake, match the flowpath ID geometry to the nearest reference reservoir in buffer distance
        # update the geometry and attributes of lakes with reference reservoir info
        for idx, row in nwm_lakes_pt.iterrows():
            # extract matching ref FP geometry for spatial index
            fps = fp.loc[(fp["fp_id"] == row["fp_id"]), "geometry"]
            candidates = ref_res.sindex.nearest(fps, max_distance=max_distance)
            # If we found a candidate, copy over all of (dam_name, nid, dam_id) - leave geometry as centroid
            if candidates.shape[1] != 0:
                nwm_lakes_pt.loc[idx, ["dam_name", "nid", "dam_id"]] = ref_res.loc[
                    candidates[1, 0], ["dam_name", "nid", "dam_id"]
                ]

        # get hydroseq and rename to what downstream code expects
        nwm_lakes_pt = nwm_lakes_pt.merge(fp[["fp_id", "hydroseq"]], on="fp_id", how="left")
        nwm_lakes_pt.rename(columns={"hydroseq": "_hydroseq"}, inplace=True)
        nwm_lakes_pt.drop(columns=["fp_id"], inplace=True)

        gdf = gpd.GeoDataFrame(nwm_lakes_pt, crs=cfg.crs)
        return gdf

    else:
        nwm_lakes_pt[["nid", "dam_id"]] = None
        return gpd.GeoDataFrame(nwm_lakes_pt, crs=cfg.crs)


def _calculate_elevation__nwm(
    cfg: HFConfig, gdf_nwm_pts: gpd.GeoDataFrame, gdf_nwm_orig: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    """Calculate elevation for NWM Lakes.

    Polygon elevation is used for `ref_elev`. Point elevation is used for `dam_elev`
    """
    if cfg.lakes.calculate_elevation:
        logger.info("Calculating NWM elevations")

        # if original nwm lakes is polygons - join nwm lake polygons for polygon elevation (ref_elev)
        if gdf_nwm_orig.geometry.iloc[0].geom_type in ["Polygon", "MultiPolygon"]:
            gdf_nwm_poly = polygon_elevation(cfg.lakes.dem.path, gdf_nwm_orig, "ref_elev")
            gdf_nwm_poly.rename(
                columns={cfg.lakes.nwm.id_field: cfg.lakes.output_comid_field},
                inplace=True,
            )
            gdf_nwm_poly[cfg.lakes.output_comid_field] = (
                gdf_nwm_poly[cfg.lakes.output_comid_field].astype(pd.Int64Dtype()).astype(str)
            )
            gdf_nwm_poly = gdf_nwm_poly.to_crs(cfg.crs)
            gdf_nwm_poly["nwm_lakes_area"] = gdf_nwm_poly.area
            gdf_nwm_pts = gdf_nwm_pts.merge(
                gdf_nwm_poly[[cfg.lakes.output_comid_field, "ref_elev", "nwm_lakes_area"]].copy(),
                on=cfg.lakes.output_comid_field,
                how="left",
            )
            gdf_nwm_pts["dam_elev"] = point_elevation(cfg.lakes.dem.path, gdf_nwm_pts)
            gdf_nwm_pts["LkArea"] = np.where(
                gdf_nwm_pts["LkArea"].isnull(),
                gdf_nwm_pts["nwm_lakes_area"] / 1_000_000.0,
                gdf_nwm_pts["LkArea"],
            )

            # Populate empty ref_elev elevations with dam_elev if missing
            gdf_nwm_pts["ref_elev"] = gdf_nwm_pts["ref_elev"].fillna(gdf_nwm_pts["dam_elev"])

        # if all points
        else:
            gdf_nwm_pts["dam_elev"] = point_elevation(cfg.lakes.dem.path, gdf_nwm_pts)
            gdf_nwm_pts["ref_elev"] = gdf_nwm_pts["dam_elev"].copy()

    else:
        gdf_nwm_pts["dam_elev"] = np.nan
        gdf_nwm_pts["ref_elev"] = np.nan

    return gdf_nwm_pts


def _calculate_elevation__refwb(
    cfg: HFConfig, gdf_refwb_pts: gpd.GeoDataFrame, gdf_refwb_poly: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    """Calculate elevation for Reference Waterbodies

    Polygon elevation is used for `ref_elev`.
    """
    if cfg.lakes.calculate_elevation:
        gdf_refwb_poly = gdf_refwb_poly.loc[
            gdf_refwb_poly[cfg.lakes.ref_wb.id_field].isin(gdf_refwb_pts[cfg.lakes.output_comid_field])
        ].copy()
        gdf_refwb_poly = polygon_elevation(cfg.lakes.dem.path, gdf_refwb_poly, "ref_elev")
        gdf_refwb_poly["dam_elev"] = gdf_refwb_poly["ref_elev"].copy()
        gdf_refwb_pts = gdf_refwb_pts.merge(
            gdf_refwb_poly[[cfg.lakes.ref_wb.id_field, "ref_elev", "dam_elev"]],
            left_on=cfg.lakes.output_comid_field,
            right_on=cfg.lakes.ref_wb.id_field,
            how="left",
        )
    else:
        gdf_refwb_pts["dam_elev"] = np.nan
        gdf_refwb_pts["ref_elev"] = np.nan

    return gdf_refwb_pts


def _calculate_elevation__lhd(
    cfg: HFConfig, gdf_lhdi_pts: gpd.GeoDataFrame, gdf_lhdi_orig: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    """Calculate elevation for low head dams.

    Low head dams are joined to reference waterbodies (polygon) to get polygon elevation ('ref_elev').
    Point elevation is used for 'dam_elev.'
    """
    if cfg.lakes.calculate_elevation:
        # polygons - join low head dam polygons for polygon elevation (ref_elev)
        # ref wb ID was changed to final output ID in filter step
        logger.info("Calculating low head dams elevations")
        if gdf_lhdi_orig.geometry.iloc[0].geom_type in ["Polygon", "MultiPolygon"]:
            gdf_lhdi_poly = polygon_elevation(cfg.lakes.dem.path, gdf_lhdi_orig, "ref_elev")
            gdf_lhdi_poly.rename(
                columns={cfg.lakes.low_head_dams.id_field: cfg.lakes.output_comid_field},
                inplace=True,
            )
            gdf_lhdi_poly[cfg.lakes.output_comid_field].astype(str)
            gdf_lhdi_poly = gdf_lhdi_poly.to_crs(cfg.crs)
            gdf_lhdi_poly["LkArea"] = gdf_lhdi_poly.area / 1_000_000.0
            gdf_lhdi_pts = gdf_lhdi_pts.merge(
                gdf_lhdi_poly[[cfg.lakes.output_comid_field, "ref_elev", "LkArea"]].copy(),
                on=cfg.lakes.output_comid_field,
                how="left",
            )
            gdf_lhdi_pts["dam_elev"] = point_elevation(cfg.lakes.dem.path, gdf_lhdi_pts)
            # Populate empty polygon elevations with point elevations if missing
            gdf_lhdi_pts["ref_elev"] = gdf_lhdi_pts["ref_elev"].fillna(gdf_lhdi_pts["dam_elev"])
        # Only Points
        else:
            gdf_lhdi_pts["dam_elev"] = point_elevation(cfg.lakes.dem.path, gdf_lhdi_pts)
            gdf_lhdi_pts["ref_elev"] = gdf_lhdi_pts["dam_elev"].copy()

    else:
        gdf_lhdi_pts["dam_elev"] = np.nan
        gdf_lhdi_pts["ref_elev"] = np.nan

    return gdf_lhdi_pts


def _calculate_elevation__ror(
    cfg: HFConfig,
    gdf_ror_pts: gpd.GeoDataFrame,
    gdf_ror_orig: gpd.GeoDataFrame,
    surface_area_field: str = "surface_area",
) -> gpd.GeoDataFrame:
    """Calculate elevations for run of river dams.

    Run of river dams are joined to reference waterbodies (polygon) to get polygon elevation ("ref_elev").
    Point elevation is used for "dam_elev"
    """
    if cfg.lakes.calculate_elevation:
        if gdf_ror_orig.geometry.iloc[0].geom_type in ["Polygon", "MultiPolygon"]:
            gdf_ror_orig = polygon_elevation(cfg.lakes.dem.path, gdf_ror_orig, "ref_elev")
            gdf_ror_orig.rename(
                columns={cfg.lakes.run_of_river.id_field: cfg.lakes.output_comid_field},
                inplace=True,
            )
            gdf_ror_orig[cfg.lakes.output_comid_field].astype(str)
            gdf_ror_orig = gdf_ror_orig.to_crs(cfg.crs)
            gdf_ror_orig["LkArea"] = gdf_ror_orig[surface_area_field] * 0.00404686
            gdf_ror_pts = gdf_ror_pts.merge(
                gdf_ror_orig[[cfg.lakes.output_comid_field, "ref_elev", "LkArea"]].copy(),
                on=cfg.lakes.output_comid_field,
                how="left",
            )
            gdf_ror_pts["dam_elev"] = point_elevation(cfg.lakes.dem.path, gdf_ror_pts)
            # Populate empty ref_elev elevations with dam_elev if missing
            gdf_ror_pts["ref_elev"] = gdf_ror_pts["ref_elev"].fillna(gdf_ror_pts["dam_elev"])
        # If only passed points
        else:
            gdf_ror_pts["dam_elev"] = point_elevation(cfg.lakes.dem.path, gdf_ror_pts)
            gdf_ror_pts["ref_elev"] = gdf_ror_pts["dam_elev"].copy()
    else:
        gdf_ror_pts["dam_elev"] = np.nan
        gdf_ror_pts["ref_elev"] = np.nan
    return gdf_ror_pts


def _calculate_elevation__refres(
    cfg: HFConfig, gdf_ref_res: gpd.GeoDataFrame, gdf_wb_poly: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    """Calculate elevation for Reference Reservoirs

    Reference reservoirs are joined to reference waterbodies (polygon) to get polygon elevation (`ref_elev`)
    Point elevation is used for `dam_elev`
    """
    if cfg.lakes.calculate_elevation:
        # polygons - join ref wb polygons for polygon elevation (ref_elev)
        # ref wb ID was changed to final output ID in filter step
        gdf_wb_poly = gdf_wb_poly.rename(columns={cfg.lakes.ref_wb.id_field: cfg.lakes.output_comid_field})
        gdf_wb_poly = gdf_wb_poly.loc[
            gdf_wb_poly[cfg.lakes.output_comid_field].isin(gdf_ref_res[cfg.lakes.output_comid_field])
        ].copy()
        gdf_wb_poly = polygon_elevation(cfg.lakes.dem.path, gdf_wb_poly, "ref_elev")
        gdf_ref_res = gdf_ref_res.merge(
            gdf_wb_poly[[cfg.lakes.output_comid_field, "ref_elev"]].copy(),
            on=cfg.lakes.output_comid_field,
            how="left",
        )
        # point
        gdf_ref_res["dam_elev"] = point_elevation(cfg.lakes.dem.path, gdf_ref_res)
        # Populate empty ref_elev elevations with dam_elev if missing
        gdf_ref_res["ref_elev"] = gdf_ref_res["ref_elev"].fillna(gdf_ref_res["dam_elev"])

    else:
        gdf_ref_res["dam_elev"] = np.nan
        gdf_ref_res["ref_elev"] = np.nan

    return gdf_ref_res


def _prep_ref_wb(
    cfg: HFConfig,
    gdf_adhoc: gpd.GeoDataFrame,
    gdf_usbr: gpd.GeoDataFrame,
    gdf_lhdi: gpd.GeoDataFrame,
    gdf_ref_res: gpd.GeoDataFrame,
    gdf_wb_polys: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Filters lakes that may have a reference waterbody lake ID

    Considered lake types: Adhoc RFC, USBR, Low-head Dam Inventory, Reference Reservoirs

    Merge reference reservoirs data into reference waterbodies (e.g. flowpath IDs, NID, area)

    Returns required reference waterbodies with attributes
    """
    # ref_wb_id: the id column for COMID in reference waterbodies
    ref_wb_id = cfg.lakes.ref_res.ref_wb_id_col
    # the name of lake_id in the output
    output_lake_id = cfg.lakes.output_comid_field
    # columns to retain
    keep_columns = [
        output_lake_id,
        "ref_fab_fp",
        "ref_fab_wb",
        "nid",
        "dam_id",
        "LkArea",
        "attrib_src",
        "geometry",
        "source",
    ]
    # set source
    gdf_adhoc["source"] = "adhoc"
    gdf_usbr["source"] = "USBR"
    gdf_lhdi["source"] = "low_head_dam"
    # process each source where keep_field is the field specifying what reservoirs should be kept
    processed_gdf = []
    for (
        gdf,
        keep_field,
    ) in zip(
        [gdf_adhoc, gdf_usbr, gdf_lhdi],
        [
            cfg.lakes.adhoc.ref_wb_field,
            cfg.lakes.usbr.ref_wb_field,
            cfg.lakes.low_head_dams.ref_wb_field,
        ],
        strict=False,
    ):
        # select where reference waterbody is required
        if not gdf.empty:
            gdf = gdf.loc[
                (gdf[keep_field] == True) | (gdf[keep_field] == "1"), :  # noqa: E712
            ].copy()
            # cast ID to string if ref wb to string
            if pd.api.types.is_object_dtype(gdf_ref_res[ref_wb_id]) and not pd.api.types.is_object_dtype(
                gdf[output_lake_id]
            ):
                gdf[output_lake_id] = gdf[output_lake_id].astype(pd.Int64Dtype()).astype(pd.StringDtype())

            # merge the ref res data
            gdf = gdf.merge(
                gdf_ref_res[["ref_fab_fp", "ref_fab_wb", "nid"]],
                left_on=cfg.lakes.ref_wb.output_id_field,
                right_on=cfg.lakes.ref_res.ref_wb_id_col,
                how="left",
            )

            # get geometry from ref wb polygons (area in m², convert to km²)
            gdf_wb_polys["LkArea"] = gdf_wb_polys.geometry.area / 1_000_000.0
            # remove polygon point geometry from original gdf
            gdf.drop(columns=["geometry"], inplace=True)
            # merge original gdf to get wb polygons, wb ID, and area
            # after meging drop the ref wb version of original ID field
            gdf = gdf.merge(
                gdf_wb_polys[[cfg.lakes.ref_wb.id_field, "LkArea", "geometry"]],
                left_on=cfg.lakes.ref_wb.output_id_field,
                right_on=cfg.lakes.ref_wb.id_field,
                how="inner",
            ).drop(columns=[cfg.lakes.ref_wb.id_field])

            # keep only needed columns
            for col in keep_columns:
                if col not in gdf.columns:
                    gdf[col] = None

            gdf = gdf[keep_columns].reset_index(drop=True).copy()
            # append to procssed gdf list
            processed_gdf.append(gdf)

    # concat dfs and drop duplicates of lake_id
    output = pd.concat(processed_gdf).reset_index(drop=True)
    output = output.drop_duplicates(subset=[output_lake_id])
    output = gpd.GeoDataFrame(output, crs=cfg.crs)
    return output


def _filter_ref_res(
    cfg: HFConfig,
    gdf_ref_res: gpd.GeoDataFrame,
    gdf_nwm: gpd.GeoDataFrame,
    gdf_ref_wb: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Filter reference reservoirs based on criteria

    Criteria for filtering:
    - dam_id is not in NWM lakes (reference reservoirs joined to NWM lakes in previous step)
    - dam_id is not in selected reference waterbodies
    - reference waterbody is present (`ref_fab_wb` is not null)
    - distance to flowpath is less than configured `ref_res_max_distance_m`
    - area is less than configured `min_wb_area_sqkm`

    Fields are renamed (ref_fab_fp : ref_fp_id) and (ref_wb_id : configured lake ID value from nwm.output_id_field)
    """
    # filter to exclude dam_id that have already been joined to nwm lakes or included wb
    gdf_ref_res = gdf_ref_res.loc[
        ~gdf_ref_res["dam_id"].isin(gdf_nwm["dam_id"])
        & ~gdf_ref_res["dam_id"].isin(gdf_ref_wb["dam_id"])
        & (gdf_ref_res["ref_fab_wb"].isnull() == False),  # noqa: E712
        [
            "geometry",
            "dam_id",
            "ref_fab_fp",
            "ref_fab_wb",
            "nid",
            "wb_areasqkm",
            "distance_to_fp_m",
        ],
    ].copy()

    # filter to criteria
    gdf_ref_res = gdf_ref_res[
        (
            (gdf_ref_res["distance_to_fp_m"] < cfg.lakes.ref_res.max_distance_m)
            & (gdf_ref_res["wb_areasqkm"] >= cfg.lakes.ref_res.min_wb_area_sqkm)
        )
        | (gdf_ref_res["dam_id"].isin(cfg.lakes.ref_res.ref_res_keep))
    ].copy()

    # Deduplicate on ref_fab_wb (waterbody COMID), keeping the row with the most NID data
    gdf_ref_res["_nid_priority"] = (
        gdf_ref_res["nid"].notna().astype(int) + gdf_ref_res["dam_id"].notna().astype(int) * 2
    )
    gdf_ref_res = gdf_ref_res.sort_values("_nid_priority", ascending=False).drop_duplicates(
        subset="ref_fab_wb", keep="first"
    )
    gdf_ref_res = gdf_ref_res.drop(columns=["_nid_priority"])

    gdf_ref_res = gdf_ref_res.rename(
        columns={"ref_fab_fp": "ref_fp_id", "ref_fab_wb": cfg.lakes.output_comid_field}
    )

    # Add hydroseq from reference flowpaths for COMID dedup tiebreaking
    gdf_ref_fp = gpd.read_parquet(cfg.build.reference_flowpaths_path)
    hydro_lookup = (
        gdf_ref_fp[["flowpath_id", "hydroseq"]]
        .drop_duplicates(subset="flowpath_id")
        .rename(columns={"flowpath_id": "ref_fp_id", "hydroseq": "_hydroseq"})
    )
    hydro_lookup["ref_fp_id"] = pd.to_numeric(hydro_lookup["ref_fp_id"], errors="coerce")
    gdf_ref_res["ref_fp_id"] = pd.to_numeric(gdf_ref_res["ref_fp_id"], errors="coerce")
    gdf_ref_res = gdf_ref_res.merge(hydro_lookup, on="ref_fp_id", how="left")

    return gdf_ref_res


def _concat_lakes(cfg: HFConfig, gdfs: list[gpd.GeoDataFrame]) -> gpd.GeoDataFrame:
    """Concat all available lakes and force final CRS."""
    for i, gdf in enumerate(gdfs):
        gdfs[i] = gdf.to_crs(cfg.crs)
    return pd.concat(gdfs, ignore_index=True)


def _dedup_lake_id(
    cfg: HFConfig,
    gdf: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Deduplicate by lake_id (COMID) after concatenation, before NID join.

    Uses priority based on source column.  Priority as follows:
    1. Run of River RFC
    2. Adhoc RFC
    3. Lowhead dams
    4. USBR
    5. NWM lakes
    6. Reference reservoirs
    """
    # create mask of all duplicated lake_id, return if empty
    dupe_mask = gdf[cfg.lakes.output_comid_field].duplicated(keep=False)
    if not dupe_mask.any():
        return gdf

    # find number of duplicate groups
    n_dupe_groups = gdf.loc[dupe_mask, cfg.lakes.output_comid_field].nunique()
    logger.info(
        f"Deduplicating {cfg.lakes.output_comid_field}: "
        f"{dupe_mask.sum()} rows across {n_dupe_groups} duplicate groups"
    )
    # Detach geometry to avoid geopandas sort_values/drop_duplicates bugs
    # that set geometry values to None in certain versions.
    geom = gdf.geometry
    df = pd.DataFrame(gdf.drop(columns=["geometry"]))
    df["orig_index"] = df.index
    # Dedupe NWM lakes first.  For duplicate lake_id, will take the lowest _hydroseq val
    # _hydroseq is available only if _fold_ref_res_to_nwm_lakes is called
    if "_hydroseq" in df.columns:
        df_nwm = df[df["source"] == "NWM"]
        df_nwm = df_nwm.sort_values(by="_hydroseq")
        df_nwm = df_nwm.drop_duplicates(subset=[cfg.lakes.output_comid_field], keep="first")
        df = pd.concat([df[df["source"] != "NWM"], df_nwm], ignore_index=False)

    # Set lake priorty, sort dataframe by priority, and drop duplicates using first found value
    lake_priority = ["run_of_river", "adhoc", "low_head_dam", "USBR", "NWM", "ref_res"]
    order_lakes = {val: i for i, val in enumerate(lake_priority)}
    df = df.sort_values(by="source", key=lambda x: x.map(order_lakes))
    df = df.drop_duplicates(subset=[cfg.lakes.output_comid_field], keep="first")

    # Re-attach geometry by aligning on original index labels
    # (df.index retains original labels after drop_duplicates)
    result = gpd.GeoDataFrame(df, geometry=geom[df["orig_index"]], crs=cfg.crs)
    result.reset_index(drop=True, inplace=True)
    result.drop(columns=["orig_index"], inplace=True)
    logger.info(f"Removed {len(gdf) - len(result)} duplicated lakes")
    return result


def _join_nid(cfg: HFConfig, res_df: gpd.GeoDataFrame, nid_df: pd.DataFrame) -> gpd.GeoDataFrame:
    """Join National Inventory Dams (NID) data to lakes.

    NID is joined by attribute (nidid) first. When multiple NID coordinate
    records exist for the same lake_id/nid, the spatially closest one to the
    lake point is kept.

    When different lake_ids share the same dam_id and nid (i.e. multiple
    reservoir records referencing the same dam), only the row whose lake
    geometry is closest to the NID point is retained.

    NWM lakes (attrib_src is set) are excluded from the NID merge logic,
    then stitched back.
    """
    # Columns to retain from NID for hydraulics computation
    keep_cols = [
        "nidid",
        "dam_name",
        "dam_type",
        "spillway_type",
        "spillway_width",
        "dam_length",
        "dam_height",
        "structural_height",
        "hydraulic_height",
        "nid_height",
        "surface_area",
        "wb_areasqkm",
        "nid_storage",
        "normal_storage",
        "max_storage",
        "hazard",
        "purposes",
    ]

    # Early return if NID is not available
    if not cfg.lakes.ref_res.run:
        logger.info("Reference reservoirs not run. Skipping NID join.")
        res_df[keep_cols] = None
        return res_df

    if nid_df.empty:
        logger.info("NID table not available. Skipping NID join.")
        res_df[keep_cols] = None
        return res_df

    if "nid" not in res_df.columns:
        logger.info(
            "'nid' column not found in lakes during NID join. "
            "NID data will not be used in hydraulics calculation."
        )
        res_df[keep_cols] = None
        return res_df

    # Preprocess NID table
    nid_df.columns = [col.lower() for col in nid_df.columns]

    # rename columns and coerce to numeric
    for col in ("spillway_type", "dam_type"):
        if col in nid_df.columns:
            nid_df[col] = nid_df[col].astype("string")
    for col in (
        "structural_height",
        "dam_height",
        "nid_height",
        "surface_area",
        "hydraulic_height",
        "dam_length",
        "spillway_width",
    ):
        if col in nid_df.columns:
            nid_df[col] = pd.to_numeric(nid_df[col], errors="coerce")

    # Convert before the merge. The coalesce below prefers a row's own value, and the
    # run-of-river source already supplies meters, so a later conversion would leave one
    # column holding two units.
    for col in NID_LENGTH_FIELDS_FT:
        if col in nid_df.columns:
            nid_df[col] = nid_df[col] * FT_TO_M

    if "surface_area" not in nid_df.columns:
        nid_df["surface_area"] = np.nan

    # Make NID spatial
    nid_df = nid_df.loc[~nid_df["latitude"].isnull() & ~nid_df["longitude"].isnull()].copy()
    nid_gdf = gpd.GeoDataFrame(
        nid_df,
        geometry=gpd.points_from_xy(nid_df["longitude"], nid_df["latitude"]),
        crs=4326,
    )
    nid_gdf = nid_gdf.to_crs(res_df.crs)
    nid_gdf = nid_gdf.replace(["<NA>", "None", "nan"], None)

    # Rename for merge
    nid_gdf = nid_gdf.rename(columns={"nidid": "nid", "dam_name": "dam_name_nid"})

    # Filter NID to only the nid values present in our lakes
    nid_vals = res_df["nid"].dropna().unique()
    nid_gdf = nid_gdf[nid_gdf["nid"].isin(nid_vals)].copy()

    # Clean string nulls
    res_df = res_df.replace(["<NA>", "None", "nan"], None)
    res_df = res_df.drop_duplicates(keep="first")

    # Separate NWM lakes (already have NID data from placement improvement)
    nwm_df = res_df.loc[res_df["attrib_src"].notna()].copy()
    res_df = res_df.loc[res_df["attrib_src"].isna()].copy()

    nulls = {"None", "nan", "<NA>"}

    # Exclude non-NWM rows whose dam_id, nid, or lake_id reference a dam/feature already
    # represented by an NWM lake (same dam, different lake_id; or same lake_id, different dam).
    # The NWM lake's attributes from placement improvement take priority.
    if not nwm_df.empty:
        lake_id_col = cfg.lakes.output_comid_field
        res_df[lake_id_col] = res_df[lake_id_col].astype(str)
        nwm_df[lake_id_col] = nwm_df[lake_id_col].astype(str)
        res_df["nid"] = res_df["nid"].astype(str)
        nwm_df["nid"] = nwm_df["nid"].astype(str)
        res_df["dam_id"] = res_df["dam_id"].astype(str)
        nwm_df["dam_id"] = nwm_df["dam_id"].astype(str)

        # by casting as string, we have to remove nullvalues
        # NOTE: this could be improved by re-working datatypes to not cast as string
        res_df = res_df.loc[
            ~(
                res_df["nid"].isin(set(nwm_df["nid"]) - nulls)
                | res_df["dam_id"].isin(set(nwm_df["dam_id"]) - nulls)
                | res_df[lake_id_col].isin(set(nwm_df[lake_id_col]) - nulls)
            )
        ].copy()

        # null NWM nid going forward to avoid duplication problems
        # NOTE: Future work could fix duplication issuesand retain NID
        # duplication bug was discovered late in development without time to fix
        # since NWM retains NWM v3 attributes in hydraulics.py, it does not reduce data
        nwm_df["nid"] = None

    # Attribute-merge NID onto non-NWM lakes
    res_df = res_df.merge(nid_gdf, on="nid", how="left")
    # NID attributes are present in both tables
    # coaelsce the new column with original name
    for col in keep_cols:
        if (f"{col}_x" in res_df.columns) and (f"{col}_y" in res_df.columns):
            res_df[col] = res_df[[f"{col}_x", f"{col}_y"]].bfill(axis=1).iloc[:, 0]

    # Continue to handle nulls as they keep popping back in due to string casting
    res_df = res_df.replace(["<NA>", "None", "nan"], None)

    # Dedup: same (dam_id, nid) across different lake_ids -> keep closest to NID point
    dam_dupe_cols = ["dam_id", "nid"]
    dam_dupe_mask = (
        res_df.duplicated(subset=dam_dupe_cols, keep=False) & res_df["dam_id"].notna() & res_df["nid"].notna()
    )
    # if any dupe, separate dupes from non-dupes to process
    if dam_dupe_mask.any():
        logger.info(
            f"Resolving {dam_dupe_mask.sum()} duplicate dam_id/nid rows "
            f"for {res_df.loc[dam_dupe_mask, 'dam_id'].nunique()} dam(s)"
        )
        dupes = res_df[dam_dupe_mask].copy()
        non_dupes = res_df[~dam_dupe_mask].copy()

        # Compute distance between lake (geometry_x) and NID point (geometry_y)
        dupes = dupes.loc[dupes["dam_id"].notna()]
        dupes["_dam_nid_dist"] = dupes["geometry_x"].distance(dupes["geometry_y"])
        keep_idx = dupes.groupby(dam_dupe_cols)["_dam_nid_dist"].idxmin()
        deduped = dupes.loc[keep_idx].drop(columns=["_dam_nid_dist"])

        # Restore active geometry from geometry_x
        deduped = deduped.rename(columns={"geometry_x": "geometry"}).drop(
            columns=["geometry_y"], errors="ignore"
        )
        non_dupes = non_dupes.rename(columns={"geometry_x": "geometry"}).drop(
            columns=["geometry_y"], errors="ignore"
        )
        res_df = gpd.GeoDataFrame(pd.concat([non_dupes, deduped], ignore_index=True), crs=cfg.crs)

    # Dedup: one (lake_id, nid) -> potentially many NID coordinate records
    dupe_cols = [cfg.lakes.output_comid_field, "nid"]
    dupe_mask = res_df.duplicated(subset=dupe_cols, keep=False)
    if dupe_mask.any():
        logger.info(
            f"Resolving {dupe_mask.sum()} duplicate NID coordinate rows "
            f"for {res_df[dupe_mask][dupe_cols[0]].nunique()} lake(s)"
        )
        dupes = res_df[dupe_mask].copy()
        non_dupes = res_df[~dupe_mask].copy()

        # Use available geometry columns (geometry_x from merge, or geometry if already restored)
        geom_col = "geometry_x" if "geometry_x" in dupes.columns else "geometry"
        nid_geom_col = "geometry_y" if "geometry_y" in dupes.columns else geom_col
        dupes["_nid_dist"] = dupes[geom_col].distance(dupes[nid_geom_col])
        keep_idx = dupes.groupby(dupe_cols)["_nid_dist"].idxmin()
        deduped = dupes.loc[keep_idx].drop(columns=["_nid_dist"])

        # Restore active geometry from geometry_x
        if "geometry_x" in deduped.columns:
            deduped = deduped.rename(columns={"geometry_x": "geometry"}).drop(
                columns=["geometry_y"], errors="ignore"
            )
            non_dupes = non_dupes.rename(columns={"geometry_x": "geometry"}).drop(
                columns=["geometry_y"], errors="ignore"
            )
        res_df = gpd.GeoDataFrame(pd.concat([non_dupes, deduped], ignore_index=True), crs=cfg.crs)

    # Restore active geometry if still needed (neither dedup ran, or only dam_id/nid dedup didn't run)
    if "geometry_x" in res_df.columns:
        res_df = res_df.rename(columns={"geometry_x": "geometry"}).drop(
            columns=["geometry_y"], errors="ignore"
        )
        res_df = gpd.GeoDataFrame(res_df, crs=cfg.crs)

    # Stitch NWM lakes back in
    output = pd.concat([res_df, nwm_df], ignore_index=True)

    # retain new columns whether they were pre-existing or in nid df
    # dam_length: "dam_crest_length_m",  # length along the top of the dam, spillway included
    # spillway_width: "spillway_width_m",  # width at max design pool; pipe diameter for pipe spillways
    for src, dst in NID_TO_NHF.items():
        if src in output.columns:
            output[src] = pd.to_numeric(output[src], errors="coerce")
            output[dst] = output[src].copy()
            output[dst] = np.where(output[dst] > 0, output[dst], np.nan)

    # Rename nid -> nidid for output schema consistency
    output = output.rename(columns={"nid": "nidid"})

    output[cfg.lakes.output_comid_field] = output[cfg.lakes.output_comid_field].astype(str).copy()

    return gpd.GeoDataFrame(output, crs=cfg.crs)


def _add_nrcs_to_source(nid_df: pd.DataFrame, gdf_all_lks: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Use the DAM_DESIGNER column in NID df to append NRCS to source column"""
    nrcs_df = nid_df[nid_df["DAM_DESIGNER"].str.contains("NRCS", case=False, na=False)]
    if "nidid" in gdf_all_lks.columns:
        gdf_all_lks.loc[gdf_all_lks["nidid"].isin(nrcs_df["NIDID"]), "source"] += "_NRCS"
    return gdf_all_lks


def _set_run_of_river_flag(gdf_all_lks: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Set run_of_river flag to True if source is run_of_river or low_head_dam"""
    gdf_all_lks["run_of_river"] = np.where(
        (gdf_all_lks["source"] == "run_of_river") | (gdf_all_lks["source"] == "low_head_dam"),
        True,
        False,
    )
    return gdf_all_lks


def _filter_columns(gdf: gpd.GeoDataFrame, fields: list[str]) -> gpd.GeoDataFrame:
    """Final filter for columns.

    Include all IDs and requested columns. Fill with nulls if column not present.
    """
    # add nulls for any missing columns requested
    for f in fields:
        if f not in gdf.columns:
            gdf[f] = None

    out_columns = (
        [
            "nhf_lake_id",
            "ref_fp_id",
            "fp_id",
            "virtual_fp_id",
            "dn_nex_id",
            "dn_virtual_nex_id",
            "div_id",
        ]
        + fields
        + ["geometry"]
    )

    gdf.replace(pd.NA, None, inplace=True)

    # select final attribute list
    return gpd.GeoDataFrame(gdf[out_columns])


def _create_ids(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Create location-based lake IDs using Open Location Code (Plus Codes).

    Converts the centroid of each lake geometry to a deterministic base-20 integer ID similar to the process for generating the location based fp_id's in ./src/hydrofabric_builds/lakes/lakes.py
    """
    gdf_wgs84 = gdf.to_crs("EPSG:4326")
    used_ints: set[int] = set()
    ids: list[int] = []

    for geom in gdf_wgs84.geometry:
        pt = geom.centroid
        _, olc_int = _encode_unique(pt.y, pt.x, code_length=12, used_ints=used_ints)
        used_ints.add(olc_int)
        ids.append(olc_int)

    gdf["nhf_lake_id"] = ids
    gdf["nhf_lake_id"] = gdf["nhf_lake_id"].astype("Int64")
    return gdf


def _assert_nwm_lakes(cfg: HFConfig, gdf_all_lks: gpd.GeoDataFrame) -> None:
    """Assert all NWM lakes are present in the final output. Write missing to output."""
    gdf_nwm_lakes = gpd.read_file(cfg.lakes.nwm.path, layer=cfg.lakes.nwm.layer)

    # check that the fields are the same name in NWM as output and cast if needed
    if gdf_nwm_lakes[cfg.lakes.nwm.id_field].dtype != gdf_all_lks[cfg.lakes.output_comid_field].dtype:
        gdf_nwm_lakes[cfg.lakes.nwm.id_field] = gdf_nwm_lakes[cfg.lakes.nwm.id_field].astype(pd.StringDtype())
        gdf_all_lks[cfg.lakes.output_comid_field] = gdf_all_lks[cfg.lakes.output_comid_field].astype(
            pd.StringDtype()
        )
    # check NWM IDs are in All lakes
    notin = gdf_nwm_lakes.loc[
        ~gdf_nwm_lakes[cfg.lakes.nwm.id_field].isin(gdf_all_lks[cfg.lakes.output_comid_field])
    ]

    if len(notin) == 0:
        logger.info(f"All {len(gdf_nwm_lakes[cfg.lakes.nwm.id_field])} NWM lakes included")
    else:
        notin.to_file(cfg.lakes.lakes_path.parent / "missing_lakes.gpkg")
        raise ValueError(
            f"{len(notin)} missing NWM lakes found. Wrote missing lakes to {(cfg.lakes.lakes_path.parent / 'missing_lakes.gpkg')}"
        )

    return


def _get_lake_geom(cfg: HFConfig, gdf_lakes: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Read in original lake polygons or points that match gdf lakes for virtual flowpath-lake crosswalk.

    Note: If NWM lakes is a point file (Alaska), the process functions.
    In `crosswalk_fp_lk` any geometry with no intersection will be assigned the flowpath that
    was already associated with it.
    """
    lake_id_field = cfg.lakes.output_comid_field
    lake_polys = []

    # read NWM lakes
    if cfg.lakes.nwm.path.exists():
        gdf_nwm = gpd.read_file(cfg.lakes.nwm.path, layer=cfg.lakes.nwm.layer)
        gdf_nwm.rename(columns={cfg.lakes.nwm.id_field: lake_id_field}, inplace=True)
        gdf_nwm = gdf_nwm[["geometry", lake_id_field]]
        lake_polys.append(gdf_nwm)

    # read reference waterbodies
    if cfg.lakes.ref_wb.path.exists():
        gdf_wb = gpd.read_file(cfg.lakes.ref_wb.path)
        gdf_wb.rename(columns={cfg.lakes.ref_wb.id_field: lake_id_field}, inplace=True)
        gdf_wb = gdf_wb[["geometry", lake_id_field]]
        lake_polys.append(gdf_wb)

    # read run of river dams
    if cfg.lakes.run_of_river.path.exists():
        gdf_ror = gpd.read_file(cfg.lakes.run_of_river.path, layer=cfg.lakes.run_of_river.layer_polygon)
        gdf_ror = gdf_ror[["geometry", lake_id_field]]
        lake_polys.append(gdf_ror)

    if not lake_polys:
        logger.info("No lake polygons available, could not run lakes-flowpaths crosswalk.")
        return gpd.GeoDataFrame(columns=["nhf_lake_id", lake_id_field, "virtual_fp_id"])

    gdf_lake_polys = pd.concat(lake_polys)

    if pd.api.types.is_numeric_dtype(gdf_lake_polys[lake_id_field]):
        gdf_lake_polys[lake_id_field] = (
            gdf_lake_polys[lake_id_field].astype(pd.Int64Dtype()).astype(pd.StringDtype())
        )

    # filter to lakes that are present in lakes file
    gdf_lake_polys = gdf_lake_polys.loc[gdf_lake_polys[lake_id_field].isin(gdf_lakes[lake_id_field])].copy()

    return gdf_lake_polys


def crosswalk_vfp_lk(
    cfg: HFConfig, gdf_lakes: gpd.GeoDataFrame, gdf_vfp: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    """Crosswalks virtual flowpaths and lakes.

    Uses lake data source for geometry. This is generally polygons but will work with points (Alaska).
    Points are unlikely to get intersection matches and will use the fallback to taking the virtual flowpath
    that was associated with the lakes in lakes pipeline.

    Any unmatched lakes using pure intersect will retrieve virtual flowpath that was associated prior.
    """
    lake_id_field = cfg.lakes.output_comid_field
    gdf_geom = _get_lake_geom(cfg, gdf_lakes)
    gdf_geom = gdf_geom.to_crs(cfg.crs)

    # spatial join polys and vfps
    gdf_join = gdf_geom.sjoin(gdf_vfp[["geometry", "virtual_fp_id"]], how="left", predicate="intersects")
    gdf_join = gdf_lakes[["nhf_lake_id", lake_id_field]].merge(gdf_join, on=lake_id_field)
    df_join = gdf_join[["nhf_lake_id", lake_id_field, "virtual_fp_id"]].copy().reset_index(drop=True)
    del gdf_join

    # join lakes without vfp intersection to lakes table and take pre-associated vfp
    df_unmatched = df_join.loc[df_join["virtual_fp_id"].isnull(), ["nhf_lake_id", lake_id_field]].copy()
    if not df_unmatched.empty:
        logger.info(
            f"All lakes did not intersect virtual flowpaths. Copying associated flowpaths for {len(df_unmatched)} lakes."
        )
        df_unmatched = df_unmatched.merge(
            gdf_lakes[[lake_id_field, "virtual_fp_id"]], on=lake_id_field, how="left"
        )
        df_join = df_join.loc[~df_join["virtual_fp_id"].isnull()].copy()

        df_join = pd.concat([df_join, df_unmatched], ignore_index=True).reset_index(drop=True)

    return gpd.GeoDataFrame(df_join)


def _aggregate_lake_polygons(
    cfg: HFConfig,
    lake_polys: dict[str, gpd.GeoDataFrame],
    lake_points: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Aggregate lake polygons from the final lake points to save as layer

    lake_id are de-duped between nwm_lakes and ref_wb. nwm_lakes is preferred.

    Parameters
    ----------
    cfg : HFConfig
        Main Config
    lake_polys : dict[str, gpd.GeoDataFrame]
        Lookup of polygon data source : geodataframe for `nwm_lakes` and `ref_wb`
    lake_points : gpd.GeoDataFrame
        Final points dataframe

    Returns
    -------
    gpd.GeoDataFrame
        Polygon dataframe with 1:1 relationship to lake COMID
    """
    lake_id_field = cfg.lakes.output_comid_field

    # cast all to string and rename inputs
    if pd.api.types.is_numeric_dtype(lake_points[lake_id_field]):
        lake_points[lake_id_field] = (
            lake_points[lake_id_field].astype(pd.Int64Dtype()).astype(pd.StringDtype())
        )
    if "nwm_lakes" in lake_polys.keys():
        if lake_id_field not in lake_polys["nwm_lakes"]:
            lake_polys["nwm_lakes"] = lake_polys["nwm_lakes"].rename(
                columns={cfg.lakes.nwm.id_field: cfg.lakes.output_comid_field}
            )
    if "ref_wb" in lake_polys.keys():
        if lake_id_field not in lake_polys["ref_wb"]:
            lake_polys["ref_wb"] = lake_polys["ref_wb"].rename(
                columns={cfg.lakes.ref_wb.id_field: cfg.lakes.output_comid_field}
            )

    # iterate through geodataframes and select lake_id present in points gdf
    extracted_poly_list = []
    for k, gdf_polys in lake_polys.items():
        if not pd.api.types.is_object_dtype(gdf_polys[lake_id_field].dtype):
            gdf_polys[lake_id_field] = (
                gdf_polys[lake_id_field].astype(pd.Int64Dtype()).astype(pd.StringDtype())
            )
        gdf_polys = gdf_polys.to_crs(cfg.crs)
        gdf_polys = (
            gdf_polys.loc[
                gdf_polys[lake_id_field].isin(lake_points[lake_id_field]),
                [lake_id_field, "geometry"],
            ]
            .copy()
            .reset_index(drop=True)
        )
        gdf_polys = gdf_polys.merge(
            lake_points[[lake_id_field, "virtual_fp_id", "nhf_lake_id"]],
            on=lake_id_field,
        )
        gdf_polys["source"] = k
        extracted_poly_list.append(gdf_polys)

    # concat dataframes and set priority to `nwm_lakes`
    all_polys = pd.concat(extracted_poly_list, ignore_index=True).reset_index(drop=True)
    all_polys["priority"] = 0
    all_polys.loc[all_polys["source"] == "nwm_lakes", "priority"] = 1
    priority_idx = all_polys.groupby(lake_id_field)["priority"].idxmax()
    all_polys = all_polys.loc[priority_idx].copy().reset_index(drop=True).drop(columns=["priority"])

    if len(all_polys) != len(lake_points):
        logger.warning("Lake polygon length does not match lake point length")
    if len(all_polys[lake_id_field].unique()) != len(all_polys):
        logger.warning("Lake polygons are not unique")
    return all_polys
