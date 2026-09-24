"""Helpers for associating geometries with reference flowpaths"""

import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd
import rustworkx as rx

logger = logging.getLogger(__name__)


def associate_flowpaths_nearest_point(
    gdf_flowpaths: gpd.GeoDataFrame,
    gdf_points: gpd.GeoDataFrame,
    search_radius_m: int | float,
    point_id: str,
    flowpath_id: str,
    flowpath_id_out_field: str,
) -> gpd.GeoDataFrame:
    """Associate point geometries with flowpath lines by buffering by a search radius and selecting mimnium distance

    Adapted from gage nearest fp code. Use a large buffer to ensure matches.

    Parameters
    ----------
    points_path : Path
        Points to associate with flowpath - can be virtual, reference, or flowpath
    flowpaths_path : Path
        Flowpath linestrings
    search_radius_m : int | float
        Buffer radius for matching flowpaths
    point_id : str
        Column name for ID in points gdf
    flowpath_id : str
        Column name for ID in flowpath gdf
    flowpath_id_out_field: str
        Column name for the flowpath ID in output file

    Returns
    -------
    gpd.GeoDataFrame
        Original point gdf including associated flowpath
    """
    # coerce geometry to 2D linestings
    gdf_flowpaths["geometry"] = gdf_flowpaths["geometry"].line_merge()
    gdf_flowpaths["geometry"] = gdf_flowpaths["geometry"].force_2d()

    # change to reference flowpath CRS if not matching
    if gdf_points.crs != gdf_flowpaths.crs:
        gdf_points = gdf_points.to_crs(gdf_flowpaths.crs)
        assert gdf_points.crs == gdf_flowpaths.crs, "CRS does not match for flowpaths and points"

    # buffer points with search radius
    gdf_points_buffer = gdf_points.copy()
    gdf_points_buffer["geometry"] = gdf_points["geometry"].buffer(float(search_radius_m))

    # intersect points buffer with flowpaths
    joined = gpd.sjoin(gdf_points_buffer, gdf_flowpaths, predicate="intersects", how="left")

    # prepare matches - based on gages nearest fp
    out = {}
    # cycle through each point ID and its subset
    for pt_row, sub in joined.groupby(point_id):
        # get point geometry
        pt = gdf_points.loc[gdf_points[point_id] == pt_row, "geometry"].values[0]

        # select flowpath geometry and ID from flowpath table for each candidate list
        candidates = gdf_flowpaths.loc[
            gdf_flowpaths[flowpath_id].isin(sub[flowpath_id].values), [flowpath_id, "geometry"]
        ].copy()

        # if no flowpaths in buffer, skip
        if candidates.shape[0] == 0:
            continue

        # calculate distance from point
        candidates["dist"] = candidates["geometry"].distance(pt)

        # select first minimum distance
        # TODO: take lower hydrosequence if tie?
        best_fp = candidates.loc[candidates["dist"] == min(candidates["dist"]), flowpath_id].values[0]
        out[pt_row] = str(best_fp)

    # assign dict of points and flowpaths to point gdf
    for k, v in out.items():
        gdf_points.loc[gdf_points[point_id] == k, flowpath_id_out_field] = v

    # NOTE: forcing was needed in AK
    if pd.api.types.is_object_dtype(gdf_points[flowpath_id_out_field]):
        gdf_points["virtual_fp_id"] = pd.to_numeric(gdf_points["virtual_fp_id"]).astype(pd.Int64Dtype())

    return gdf_points


def join_attributes(
    gdf: gpd.GeoDataFrame,
    attrib_dst_key: str,
    attrib_src_path: Path,
    attrib_src_layer: str | None = None,
    attrib_src_key: str = "lake_id",
    attrib_src_fields: list[str] | None = None,
    rename: bool = True,
) -> gpd.GeoDataFrame:
    """Join attributes from source file to given dataframe on given key(s)

    Parameters
    ----------
    gdf: GeoDataFrame
        Dataframe to join attributes into
    attrib_src_path: Path,
        Path to attribute source file
    attrib_src_layer: str,
        Name of attribute source layer
    attrib_src_key: str,
        Key to perform join with
    attrib_src_fields: list[str],
        Fields from attribute source to preserve
    rename : bool
        Whether to rename attrib_dst_key to attrib_src_key in returned GDF

    """
    if (
        attrib_src_path and not attrib_src_fields
    ):  # It is illegal behavior to define only one of these fields. We enforce that here
        raise ValueError(
            "flowpath_association: `attrib_src_path` was provided but `attrib_src_fields` was `None`, attribute source fields must be specified in order to merge"
        )
    else:
        attrib_src_fields_valid: list[str] = attrib_src_fields if attrib_src_fields else list[str]()
        gdf_attrib_src = (
            gpd.read_file(attrib_src_path, layer=attrib_src_layer)
            if attrib_src_layer
            else gpd.read_file(attrib_src_path)
        )
        if attrib_src_key != attrib_dst_key:
            gdf_attrib_src.drop("geometry", axis=1, inplace=True)
            if rename:
                gdf = gdf.rename(columns={attrib_dst_key: attrib_src_key})
                attrib_dst_key = attrib_src_key
            if attrib_src_key in attrib_src_fields_valid:
                attrib_src_fields_valid.remove(attrib_src_key)

        if not pd.api.types.is_object_dtype(gdf_attrib_src[attrib_src_key]):
            gdf_attrib_src[attrib_src_key] = (
                gdf_attrib_src[attrib_src_key].astype(pd.Int64Dtype()).astype(str)
            )

        if not pd.api.types.is_object_dtype(gdf[attrib_dst_key]):
            gdf[attrib_dst_key] = gdf[attrib_dst_key].astype(str)

        gdf_merged = gdf.merge(
            gdf_attrib_src[[attrib_src_key] + attrib_src_fields_valid],
            how="left",
            left_on=attrib_dst_key,
            right_on=attrib_src_key,
        )
        gdf_merged["attrib_src"] = attrib_src_path.name

    return gdf_merged


def make_vfp_graph(vfp: gpd.GeoDataFrame, vn: gpd.GeoDataFrame) -> tuple[rx.PyDiGraph, dict[str, int]]:
    """Build graph from virtual flowpaths and virtual nexus

    Parameters
    ----------
    vfp : gpd.GeoDataFrame
        virtual flowpath geodataframe
    vn : gpd.GeoDataFrame
        virtual nexus geodataframe

    Returns
    -------
    tuple[rx.PyDiGraph, dict[str, int]]
        PyDiGraph of virtual network, dictionary of virtual flowpath id : integer index used in graph
    """
    edges = (
        vfp[["virtual_fp_id", "dn_virtual_nex_id"]]
        .merge(
            vn[["virtual_nex_id", "dn_virtual_fp_id"]],
            how="left",
            left_on="dn_virtual_nex_id",
            right_on="virtual_nex_id",
        )
        .rename(columns={"dn_virtual_fp_id": "to_vfp_id"})[["virtual_fp_id", "to_vfp_id"]]
    )

    edges["virtual_fp_id"] = edges["virtual_fp_id"].astype(pd.Int64Dtype()).astype(str)
    edges["to_vfp_id"] = edges["to_vfp_id"].astype(pd.Int64Dtype()).astype(str)
    all_ids = pd.concat([edges["virtual_fp_id"], edges["to_vfp_id"]]).unique()
    id_to_idx = {fp_id: idx for idx, fp_id in enumerate(all_ids)}

    # Build directed graph from edge list
    graph: rx.PyDiGraph = rx.PyDiGraph()
    graph.add_nodes_from(range(len(all_ids)))
    graph.extend_from_edge_list(
        [
            (id_to_idx[src], id_to_idx[dst])
            for src, dst in zip(edges["virtual_fp_id"], edges["to_vfp_id"], strict=True)
        ]
    )

    return graph, id_to_idx


def associate_flowpaths_polygon_graph(
    gdf_poly: gpd.GeoDataFrame,
    gdf_vfp: gpd.GeoDataFrame,
    graph: rx.PyDiGraph,
    id_to_idx: dict[str, int],
    poly_id: str,
    vfp_id: str = "virtual_fp_id",
    intersection_length_min_m: int = 3,
) -> gpd.GeoDataFrame:
    """Associate polygon data with the intersecting flowpath with the largest subgraph (ancestors)

    Parameters
    ----------
    gdf_poly : gpd.GeoDataFrame
        polygon geodataframe (e.g. lakes)
    gdf_vfp : gpd.GeoDataFrame
       virtual flowpaths geodataframe, unmodified
    graph : rx.PyDiGraph
        virtual flowpath graph
    id_to_idx : dict[str, str]
        mapping of virtual flowpath ID to integer index used in graph
    fp_id : str
        field name for flowpath/virtual flowpath
    poly_id : str
        field name for polygon ID
    intersection_length_min_m : int, optional
        If a path intersects the polygon by less than this value, it will be removed. This is to handle
        frequent cases where flowpaths only overlap by <1 meter. If the small intersection is used
        the lake will route water too far downstream, by default 3 meters

    Returns
    -------
    gpd.GeoDataFrame
        _description_
    """
    # Cast all IDs to string
    if pd.api.types.is_numeric_dtype(gdf_poly[poly_id]):
        gdf_poly[poly_id] = gdf_poly[poly_id].astype(pd.Int64Dtype()).astype(str)
    gdf_vfp[vfp_id] = gdf_vfp[vfp_id].astype(pd.Int64Dtype()).astype(str)

    # intersect polygons and linestrings resulting in linestring intersections
    int_vfp = gdf_poly.overlay(gdf_vfp, keep_geom_type=False)

    poly_fp_pairs = {}
    missing_keys = []

    # process each polygon to find flowpath with most ancestors (largest subgraph)
    for poly in int_vfp[poly_id].unique():
        # for each polygon, get all intersecting flowpaths
        # for each intersecting flowpath, check the intersection length
        # if the intersection length > minimum intersection length, keep flowpaths
        candidates = int_vfp.loc[int_vfp[poly_id] == poly, [vfp_id, "geometry"]]
        single_poly = gdf_poly.loc[gdf_poly[poly_id] == poly, [poly_id, "geometry"]]
        int = single_poly.overlay(candidates, how="intersection", keep_geom_type=False)
        int = int.loc[int["geometry"].length > intersection_length_min_m]

        # track which flowpath has most ancestors and the flowpath ID
        # for each candidate, get a list of ancestors
        # save the candidate with largest subgraph / most ancestors
        max_ancestors = 0
        max_fp = None

        for cand in int[vfp_id].values:
            try:
                ind = id_to_idx[cand]
                vals = list(rx.ancestors(graph, ind))

                # set max ancestor count and candidate
                # including >= is to handle 0 if the only cand is 0
                if len(vals) >= max_ancestors:
                    max_ancestors = len(vals)
                    max_fp = cand
            except KeyError:
                missing_keys.append(cand)
                continue

        poly_fp_pairs[poly] = max_fp

    if missing_keys:
        logger.warning(
            f"Missing flowpath IDs detected in flowpath graph. Graph may be misconstructed. Keys: {missing_keys}"
        )

    # join flowpaths back to polygons
    df_pairs = pd.DataFrame(data={poly_id: poly_fp_pairs.keys(), vfp_id: poly_fp_pairs.values()})
    gdf_poly = gdf_poly.merge(df_pairs, on=poly_id, how="left")
    gdf_poly["geometry"] = gdf_poly["geometry"].centroid
    gdf_poly[vfp_id] = pd.to_numeric(gdf_poly[vfp_id]).astype(pd.Int64Dtype())
    return gdf_poly
