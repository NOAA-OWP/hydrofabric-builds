"""Geometry aggregation module for hydrofabric builds."""

import logging
from itertools import chain
from typing import Any

import rustworkx as rx
from shapely.geometry.base import BaseGeometry
from shapely.ops import linemerge, unary_union

from hydrofabric_builds.schemas.hydrofabric import Aggregations, Classifications

logger = logging.getLogger(__name__)


def _merge_tuples_with_common_values(tuples_list: list[tuple[str, ...]]) -> list[list[str]]:
    """Merge tuples that share any common values using a Union-Find algorithm.

    Parameters
    ----------
    tuples_list : list[tuple[str, ...]]
        List of tuples, each containing values

    Returns
    -------
    list[list[str]]
        Each list contains all connected values
    """
    if not tuples_list:
        return []

    parent: dict[str, str] = {}

    def find(x: str) -> str:
        if x not in parent:
            parent[x] = x
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(x: str, y: str) -> None:
        root_x = find(x)
        root_y = find(y)
        if root_x != root_y:
            parent[root_x] = root_y

    for tup in tuples_list:
        if len(tup) > 0:
            first = tup[0]
            for value in tup[1:]:
                union(first, value)

    groups: dict[str, set[str]] = {}
    for tup in tuples_list:
        for value in tup:
            root = find(value)
            if root not in groups:
                groups[root] = set()
            groups[root].add(value)

    return [list(group) for group in groups.values()]


def _process_aggregation_pairs(
    classifications: Classifications,
    fp_lookup: dict[str, dict[str, Any]],
    div_lookup: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Process aggregation pairs using dictionary lookups.

    Parameters
    ----------
    classifications : Classifications
        Classification results
    fp_lookup : dict[str, dict[str, Any]]
        Flowpath lookup dict with attributes and shapely_geometry
    div_lookup : dict[str, dict[str, Any]]
        Divide lookup dict with shapely_geometry

    Returns
    -------
    list[dict[str, Any]]
        Processed aggregation data with percentage_area_contribution for each ref_id
    """
    groups = _merge_tuples_with_common_values(classifications.aggregation_pairs)

    results: list[dict[str, Any]] = []
    non_nextgen_flowpaths_set: set[str] = set(classifications.non_nextgen_flowpaths)

    for group_ids in groups:
        # Filter out virtual flowpaths
        fp_ids = [fp_id for fp_id in group_ids if fp_id not in non_nextgen_flowpaths_set]

        if not fp_ids:
            logger.debug(f"Skipping group {group_ids} - all flowpaths are virtual")
            continue

        try:
            # Get flowpath data from lookup dict
            fp_data = [fp_lookup[fp_id] for fp_id in fp_ids if fp_id in fp_lookup]

            if not fp_data:
                logger.debug(f"Cannot find flowpaths for {group_ids}")
                continue

            # Sort by hydroseq
            sorted_fps_asc = sorted(fp_data, key=lambda x: x["hydroseq"])
            sorted_fps_desc = sorted(fp_data, key=lambda x: x["hydroseq"], reverse=True)

            # Extract IDs in sorted order
            sorted_ids_asc = [str(fp["flowpath_id"]) for fp in sorted_fps_asc]
            fp_geometry_ids = [str(fp["flowpath_id"]) for fp in sorted_fps_desc]

            # Compute aggregates
            length_km = sum(float(fp["shapely_geometry"].length / 1e3) for fp in fp_data)  # m to km
            hydroseq = max(int(fp["hydroseq"]) for fp in fp_data)
            vpu_id = fp_data[0]["VPUID"]

            # Calculate percentage area contribution for each ref_id
            # This is the area of each flowpath divided by the total divide area
            ref_data = [fp_lookup[_fp_id] for _fp_id in group_ids]
            div_area_sum = 0
            for div_id in group_ids:
                if div_id in div_lookup:
                    div_area_sum += div_lookup[div_id]["shapely_geometry"].area / 1e6  # m2 to km2
            ref_id_to_percentage: dict[str, float] = {}
            if div_area_sum > 0:
                for fp in ref_data:
                    fp_id = str(fp["flowpath_id"])
                    fp_area = float(fp["areasqkm"])
                    # NOTE: Investigate flowpaths with no divide area
                    ref_id_to_percentage[fp_id] = fp_area / div_area_sum

                # Normalize to ensure they sum to exactly 1.0
                total_percentage = sum(ref_id_to_percentage.values())
                if total_percentage > 0:
                    for fp_id in ref_id_to_percentage:
                        ref_id_to_percentage[fp_id] /= total_percentage

            # Get geometries from lookup dicts
            line_geoms: list[BaseGeometry] = [
                fp_lookup[fp_id]["shapely_geometry"] for fp_id in fp_geometry_ids if fp_id in fp_lookup
            ]
            polygon_geoms: list[BaseGeometry] = [
                div_lookup[fp_id]["shapely_geometry"] for fp_id in group_ids if fp_id in div_lookup
            ]

            results.append(
                {
                    "ref_ids": fp_ids,
                    "dn_id": sorted_ids_asc[0],
                    "up_id": sorted_ids_asc[-1],
                    "vpu_id": vpu_id,
                    "hydroseq": hydroseq,
                    "length_km": length_km,
                    "area_sqkm": div_area_sum,
                    "ref_id_to_percentage": ref_id_to_percentage,
                    "line_geometry": linemerge(list(chain.from_iterable(geom.geoms for geom in line_geoms))),
                    "polygon_geometry": unary_union(polygon_geoms) if polygon_geoms else None,
                }
            )

        except KeyError:
            logger.debug(f"Missing flowpath / divide path data for fp_ids {fp_ids}")
            continue
    return results


def _process_independent_flowpaths(
    classifications: Classifications,
    fp_lookup: dict[str, dict[str, Any]],
    div_lookup: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Process independent flowpaths using dictionary lookups.

    Parameters
    ----------
    classifications : Classifications
        Classification results
    fp_lookup : dict[str, dict[str, Any]]
        Flowpath lookup dict with shapely_geometry
    div_lookup : dict[str, dict[str, Any]]
        Divide lookup dict with shapely_geometry

    Returns
    -------
    list[dict[str, Any]]
        Independent flowpath data with percentage_area_contribution
    """
    results: list[dict[str, Any]] = []
    for fp in classifications.independent_flowpaths:
        try:
            fp_data = fp_lookup[fp]
            div_data = div_lookup[fp]

            length_km = float(fp_data["shapely_geometry"].length / 1e3)  # m to km
            hydroseq = int(fp_data["hydroseq"])
            div_area_sqkm = float(div_data["shapely_geometry"].area / 1e6)  # m2 to km2
            vpu_id = fp_data["VPUID"]

            # For independents, the single flowpath represents 100% of the divide
            ref_id_to_percentage = {fp: 1.0}

            results.append(
                {
                    "ref_ids": fp,
                    "vpu_id": vpu_id,
                    "hydroseq": hydroseq,
                    "length_km": length_km,
                    "area_sqkm": div_area_sqkm,
                    "ref_id_to_percentage": ref_id_to_percentage,
                    "line_geometry": fp_lookup[fp]["shapely_geometry"],
                    "polygon_geometry": div_lookup[fp]["shapely_geometry"] if fp in div_lookup else None,
                }
            )
        except KeyError:
            logger.debug(f"Missing flowpath / divide path data for fp_id {fp}")
            continue

    return results


def _process_connectors(
    classifications: Classifications,
    fp_lookup: dict[str, dict[str, Any]],
    div_lookup: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Process connectors using dictionary lookups.

    Parameters
    ----------
    classifications : Classifications
        Classification results
    fp_lookup : dict[str, dict[str, Any]]
        Flowpath lookup dict with shapely_geometry
    div_lookup : dict[str, dict[str, Any]]
        Divide lookup dict with shapely_geometry

    Returns
    -------
    list[dict[str, Any]]
        Connector data with percentage_area_contribution
    """
    results: list[dict[str, Any]] = []
    for fp in classifications.connector_segments:
        try:
            fp_data = fp_lookup[fp]
            div_data = div_lookup[fp]

            length_km = float(fp_data["shapely_geometry"].length / 1e3)  # m to km
            hydroseq = int(fp_data["hydroseq"])
            div_area_sqkm = float(div_data["shapely_geometry"].area / 1e6)  # m2 to km2
            vpu_id = fp_data["VPUID"]

            # For connectors, the single flowpath represents 100% of the divide
            ref_id_to_percentage = {fp: 1.0}

            results.append(
                {
                    "ref_ids": fp,
                    "vpu_id": vpu_id,
                    "hydroseq": hydroseq,
                    "length_km": length_km,
                    "area_sqkm": div_area_sqkm,
                    "ref_id_to_percentage": ref_id_to_percentage,
                    "line_geometry": fp_lookup[fp]["shapely_geometry"],
                    "polygon_geometry": div_lookup[fp]["shapely_geometry"] if fp in div_lookup else None,
                }
            )
        except KeyError:
            logger.debug(f"Missing flowpath / divide path data for fp_id {fp}")
            continue
    return results


def _process_non_nextgen_flowpaths(
    classifications: Classifications,
    fp_lookup: dict[str, dict[str, Any]],
    div_lookup: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Process virtual flowpaths using dictionary lookups.

    Parameters
    ----------
    classifications : Classifications
        Classification results
    fp_lookup : dict[str, dict[str, Any]]
        Flowpath lookup dict with shapely_geometry
    div_lookup : dict[str, dict[str, Any]]
        Divide lookup dict with shapely_geometry

    Returns
    -------
    list[dict[str, Any]]
        virtual flowpath data
    """
    results: list[dict[str, Any]] = []
    for fp in classifications.non_nextgen_flowpaths:
        if fp in fp_lookup:
            results.append(
                {
                    "ref_ids": fp,
                    "line_geometry": fp_lookup[fp]["shapely_geometry"],
                    "polygon_geometry": div_lookup[fp]["shapely_geometry"] if fp in div_lookup else None,
                }
            )

    return results


def _process_virtual_flowpaths(
    classifications: Classifications,
    fp_lookup: dict[str, dict[str, Any]],
    div_lookup: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Process virtual flowpaths - each tuple pair becomes one virtual flowpath.

    Each (flowpath_id, downstream_target) tuple represents a single virtual flowpath
    that should NOT be merged with others, even if they share the same downstream.

    Parameters
    ----------
    classifications : Classifications
        Classification results
    fp_lookup : dict[str, dict[str, Any]]
        Flowpath lookup dict with shapely_geometry
    div_lookup : dict[str, dict[str, Any]]
        Divide lookup dict with shapely_geometry

    Returns
    -------
    list[dict[str, Any]]
        Virtual flowpath data - one entry per tuple pair
    """
    results: list[dict[str, Any]] = []

    # Each tuple (fp_id, downstream_target) is a separate virtual flowpath
    for fp_id, downstream_target in classifications.virtual_flowpath_pairs:
        if fp_id not in fp_lookup:
            logger.debug(f"Flowpath {fp_id} not found in lookup")
            continue

        fp_data = fp_lookup[fp_id]

        if fp_id in div_lookup:
            area_sqkm = float(div_lookup[fp_id]["shapely_geometry"].area / 1e6)
        else:
            area_sqkm = 0.0
        # Single flowpath attributes
        length_km = float(fp_data["shapely_geometry"].length / 1e3)  # m to km
        hydroseq = int(fp_data["hydroseq"])
        vpu_id = fp_data["VPUID"]

        results.append(
            {
                "ref_ids": [fp_id],  # Single flowpath in a list
                "dn_id": downstream_target,  # Where this virtual flowpath connects
                "up_id": fp_id,  # Same as the only flowpath
                "vpu_id": vpu_id,
                "hydroseq": hydroseq,
                "length_km": length_km,
                "area_sqkm": area_sqkm,
                "line_geometry": fp_lookup[fp_id]["shapely_geometry"],
            }
        )

    return results


def _process_non_nextgen_virtual_flowpaths(
    classifications: Classifications,
    fp_lookup: dict[str, dict[str, Any]],
    div_lookup: dict[str, dict[str, Any]],
    graph: rx.PyDiGraph,
    node_indices: dict[str, Any],
) -> list[dict[str, Any]]:
    """Process non-NextGen virtual flowpaths - merge connected chains.

    Groups non-NextGen virtual flowpath tuples that share reference IDs into
    connected chains, then aggregates each chain into a single virtual flowpath.

    Parameters
    ----------
    classifications : Classifications
        Classification results
    fp_lookup : dict[str, dict[str, Any]]
        Flowpath lookup dict with shapely_geometry
    graph: rx.PyDiGraph
        The graph object for this outlet
    node_indices: dict[str, Any]
        The node indices for this graph

    Returns
    -------
    list[dict[str, Any]]
        Non-NextGen virtual flowpath data - one entry per connected chain
    """
    if not classifications.non_nextgen_virtual_flowpath_pairs:
        return []

    # Get all non-NextGen flowpath IDs
    non_nextgen_fp_ids = set()
    for fp_id, _ in classifications.non_nextgen_virtual_flowpath_pairs:
        non_nextgen_fp_ids.add(fp_id)

    # Create subgraph with only non-NextGen flowpaths
    non_nextgen_node_indices = []
    for fp_id in non_nextgen_fp_ids:
        if fp_id in node_indices:
            non_nextgen_node_indices.append(node_indices[fp_id])

    if not non_nextgen_node_indices:
        return []

    # Create subgraph containing only non-NextGen nodes
    non_nextgen_subgraph = graph.subgraph(non_nextgen_node_indices)

    # Find connected components (each component is a separate chain)
    connected_components = rx.weakly_connected_components(non_nextgen_subgraph)

    results: list[dict[str, Any]] = []

    # Process each connected component as one virtual flowpath
    for component in connected_components:
        # Get flowpath IDs from component node indices
        component_fp_ids = [non_nextgen_subgraph[node_idx] for node_idx in component]

        # Get flowpath data for all ref_ids in this component
        fp_data = [fp_lookup[fp_id] for fp_id in component_fp_ids if fp_id in fp_lookup]
        if not fp_data:
            continue

        # Sort by hydroseq
        sorted_fps_asc = sorted(fp_data, key=lambda x: x["hydroseq"])
        sorted_fps_desc = sorted(fp_data, key=lambda x: x["hydroseq"], reverse=True)

        # Extract IDs
        sorted_ids_asc = [str(fp["flowpath_id"]) for fp in sorted_fps_asc]
        fp_geometry_ids = [str(fp["flowpath_id"]) for fp in sorted_fps_desc]

        # Compute aggregates
        length_km = sum(float(fp["shapely_geometry"].length / 1e3) for fp in fp_data)
        hydroseq = max(int(fp["hydroseq"]) for fp in fp_data)
        vpu_id = fp_data[0]["VPUID"]

        area_sqkm = 0
        for div_id in component_fp_ids:
            if div_id in div_lookup:
                area_sqkm += div_lookup[div_id]["shapely_geometry"].area / 1e6

        # Aggregate geometry
        line_geoms = [fp_lookup[fp_id]["shapely_geometry"] for fp_id in fp_geometry_ids if fp_id in fp_lookup]

        results.append(
            {
                "ref_ids": component_fp_ids,  # All connected ref_ids in this component
                "dn_id": sorted_ids_asc[0],  # Most downstream
                "up_id": sorted_ids_asc[-1],  # Most upstream
                "vpu_id": vpu_id,
                "hydroseq": hydroseq,
                "length_km": length_km,
                "area_sqkm": area_sqkm,
                "line_geometry": linemerge(list(chain.from_iterable(geom.geoms for geom in line_geoms))),
            }
        )

    return results


def _aggregate_geometries(
    classifications: Classifications,
    partition_data: dict[str, Any],
) -> Aggregations:
    """Aggregate geometries using dictionary lookups.

    Parameters
    ----------
    classifications : Classifications
        Classification results
    partition_data : dict[str, Any]
        Contains fp_lookup and div_lookup with shapely geometries

    Returns
    -------
    Aggregations
        Aggregated geometry data
    """
    fp_lookup: dict[str, dict[str, Any]] = partition_data["fp_lookup"]
    div_lookup: dict[str, dict[str, Any]] = partition_data["div_lookup"]
    subgraph = partition_data["subgraph"]
    node_indices = partition_data["node_indices"]

    aggregates = _process_aggregation_pairs(classifications, fp_lookup, div_lookup)

    independents = _process_independent_flowpaths(classifications, fp_lookup, div_lookup)

    non_nextgen_flowpaths = _process_non_nextgen_flowpaths(classifications, fp_lookup, div_lookup)

    connectors = _process_connectors(classifications, fp_lookup, div_lookup)

    virtual_flowpaths = _process_virtual_flowpaths(classifications, fp_lookup, div_lookup)

    non_nextgen_virtual_flowpaths = _process_non_nextgen_virtual_flowpaths(
        classifications, fp_lookup, div_lookup, subgraph, node_indices
    )

    return Aggregations(
        aggregates=aggregates,
        independents=independents,
        non_nextgen_flowpaths=non_nextgen_flowpaths,
        connectors=connectors,
        virtual_flowpaths=virtual_flowpaths,
        non_nextgen_virtual_flowpaths=non_nextgen_virtual_flowpaths,
    )
