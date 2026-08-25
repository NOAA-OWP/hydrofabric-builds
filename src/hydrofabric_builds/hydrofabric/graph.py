"""A file for all graph related internal functions"""

from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import polars as pl
import rustworkx as rx
from scipy import sparse


def _validate_and_fix_geometries(gdf: gpd.GeoDataFrame, geom_type: str) -> gpd.GeoDataFrame:
    """Validate and fix invalid geometries in a GeoDataFrame.

    Parameters
    ----------
    gdf : gpd.GeoDataFrame
        GeoDataFrame to validate
    geom_type : str
        Description for logging (e.g., "flowpaths", "divides")

    Returns
    -------
    gpd.GeoDataFrame
        GeoDataFrame with fixed geometries

    Raises
    ------
    ValueError
        If geometries cannot be fixed or invalid geometries remain
    """
    invalid_mask = ~gdf.geometry.is_valid
    invalid_count = invalid_mask.sum()

    if invalid_count == 0:
        return gdf  # No invalid geometries

    geometries = gdf[invalid_mask].geometry
    gdf.loc[invalid_mask, "geometry"] = geometries.make_valid()

    if len(gdf[~gdf.geometry.is_valid]) > 0:
        raise ValueError(f"Could not fix invalid geometries in {geom_type}")

    still_invalid = (~gdf.geometry.is_valid).sum()
    if still_invalid > 0:
        raise ValueError(f"Invalid Geometries remain: {gdf[~gdf.geometry.is_valid]}")

    return gdf


def _build_rustworkx_object(
    upstream_network: dict[str, list[str]] | dict[int, list[int]],
) -> tuple[rx.PyDiGraph, dict[str, int] | dict[int, int]]:
    """Build a RustWorkX directed graph from upstream network dictionary.

    Parameters
    ----------
    upstream_network : dict[str, list[str]] | dict[int, list[int]]
        Dictionary mapping downstream flowpath IDs to lists of upstream flowpath IDs

    Returns
    -------
    tuple[rx.PyDiGraph, dict[str, int] | dict[int, int]]
        The flowpaths object in graph form and node indices for each object in the graph
    """
    graph = rx.PyDiGraph(check_cycle=True)
    node_indices: dict[Any, int] = {}
    for to_edge in sorted(upstream_network.keys()):
        from_edges = upstream_network[to_edge]  # type: ignore
        if to_edge not in node_indices:
            node_indices[to_edge] = graph.add_node(to_edge)
        for from_edge in from_edges:
            if from_edge not in node_indices:
                node_indices[from_edge] = graph.add_node(from_edge)
    for to_edge, from_edges in upstream_network.items():
        for from_edge in from_edges:
            graph.add_edge(node_indices[from_edge], node_indices[to_edge], None)
    return graph, node_indices


def _detect_cycles(graph: rx.PyDiGraph) -> None:
    """Detect any cycles in the rustworkx graph and validate lower triangular structure.

    Parameters
    ----------
    graph : rx.PyDiGraph
        The DiGraph object

    Raises
    ------
    AssertionError
        If the flowpaths are not in a lower triangular ordering or have multiple successors
    """
    ts_order: list[int] = rx.topological_sort(graph)  # Reindex the flowpaths based on the topo order
    id_order = [graph.get_node_data(gidx) for gidx in ts_order]
    idx_map: dict[Any, int] = {id_val: idx for idx, id_val in enumerate(id_order)}

    col: list[int] = []
    row: list[int] = []

    for node in ts_order:
        if graph.out_degree(node) == 0:  # terminal node
            continue
        id_val = graph.get_node_data(node)
        assert len(graph.successors(node)) == 1, f"Node {id_val} has multiple successors, not dendritic"
        id_ds = graph.successors(node)[0]
        col.append(idx_map[id_val])
        row.append(idx_map[id_ds])

    matrix = sparse.coo_matrix(
        (np.ones(len(row), dtype=np.uint8), (row, col)), shape=(len(ts_order), len(ts_order)), dtype=np.uint8
    )

    # Ensure matrix is lower triangular
    assert np.all(matrix.row >= matrix.col), "Matrix is not lower triangular"


def _find_outlets_by_hydroseq(reference_flowpaths: pl.DataFrame) -> list[str]:
    """Find outlets for the river using hydroseq.

    Parameters
    ----------
    reference_flowpaths : pl.DataFrame
        The flowpath reference

    Returns
    -------
    list[str]
        All outlets from the reference
    """
    df_pl = reference_flowpaths.select(pl.col(["flowpath_id", "hydroseq", "dnhydroseq", "totdasqkm"]))

    df_with_str_id = df_pl.with_columns(
        pl.col("flowpath_id").cast(pl.Float64).cast(pl.Int64).cast(pl.Utf8).alias("flowpath_id_str")
    )

    hydroseq_set: set[Any] = set(df_pl["hydroseq"].to_list())

    outlets_df = df_with_str_id.filter(
        (pl.col("dnhydroseq") == 0) | ~pl.col("dnhydroseq").is_in(hydroseq_set)
    ).sort("flowpath_id_str")  # dnhydroseq is 0, or doesn't exist in hydroseq

    # outlets_sorted = outlets_df.sort("totdasqkm", descending=True) # Commenting out until production
    outlets: list[str] = outlets_df["flowpath_id_str"].to_list()

    return outlets


def _build_upstream_dict_from_nexus(
    flowpaths_pl: pl.DataFrame, edge_id: str = "fp_id", node_id: str = "nex_id"
) -> dict[int, list[int]]:
    """Build upstream connectivity dictionary from flowpath nexus connections.

    Uses nexus IDs as the connection points between flowpaths.

    Parameters
    ----------
    flowpaths_pl : pl.DataFrame
        Flowpaths with fp_id, up_nex_id, dn_nex_id columns

    Returns
    -------
    dict[int, list[int]]
        Dictionary mapping downstream fp_id (int) -> list of upstream fp_ids (int)
    """
    fp_pl = flowpaths_pl.with_columns(
        [
            pl.col(edge_id).cast(pl.Int32),
            pl.col(f"up_{node_id}").cast(pl.Int32),
            pl.col(f"dn_{node_id}").cast(pl.Int32),
        ]
    )
    # Create mapping: nex_id -> downstream fp_id (where this nexus is the upstream nexus)
    nexus_to_downstream = fp_pl.select(
        [
            pl.col(f"up_{node_id}").alias(node_id),
            pl.col(edge_id).alias(f"dn_{edge_id}"),
        ]
    ).filter(pl.col(node_id).is_not_null())

    # Create mapping: nex_id -> upstream fp_id (where this nexus is the downstream nexus)
    nexus_to_upstream = fp_pl.select(
        [
            pl.col(f"dn_{node_id}").alias(node_id),
            pl.col(edge_id).alias(f"up_{edge_id}"),
        ]
    ).filter(pl.col(node_id).is_not_null())

    # Join to find connections: upstream fp -> nexus -> downstream fp
    connections = nexus_to_upstream.join(nexus_to_downstream, on=node_id, how="inner").select(
        [
            pl.col(f"dn_{edge_id}"),
            pl.col(f"up_{edge_id}"),
        ]
    )

    # Group by downstream to get list of upstreams
    upstream_dict_df = connections.group_by(f"dn_{edge_id}").agg(
        pl.col(f"up_{edge_id}").sort().alias("upstream_list")
    )

    # Convert to dictionary
    upstream_dict: dict[int, list[int]] = dict(
        zip(
            upstream_dict_df[f"dn_{edge_id}"].to_list(),
            upstream_dict_df["upstream_list"].to_list(),
            strict=False,
        )
    )

    return upstream_dict


def _build_graph(reference_flowpaths: pl.DataFrame) -> dict[str, list[str]]:
    """Build a graph of upstream flowpath connections.

    Parameters
    ----------
    reference_flowpaths : pl.DataFrame
        The reference flowpaths

    Returns
    -------
    dict[str, list[str]]
        The upstream dictionary containing upstream and downstream connections
        Key is the downstream flowpath ID, values are the upstream flowpath IDs
    """
    pl_reference_flowpaths = reference_flowpaths.select(
        pl.col(["flowpath_id", "hydroseq", "dnhydroseq", "totdasqkm"])
    )

    df = (
        pl_reference_flowpaths.select(
            [
                pl.col("flowpath_id").cast(pl.Float64).cast(pl.Int64).cast(pl.Utf8).alias("flowpath_id_str"),
                pl.col("hydroseq").cast(pl.Utf8).alias("hydroseq_str"),
                pl.col("dnhydroseq"),
            ]
        )
        .filter(pl.col("dnhydroseq").is_not_null() & (pl.col("dnhydroseq") != 0))
        .with_columns(pl.col("dnhydroseq").cast(pl.Utf8).alias("dnhydroseq_str"))
    )

    hydroseq_lookup = pl_reference_flowpaths.select(
        [
            pl.col("hydroseq").cast(pl.Utf8).alias("hydroseq_str"),
            pl.col("flowpath_id").cast(pl.Float64).cast(pl.Int64).cast(pl.Utf8).alias("flowpath_id_str"),
        ]
    )

    merged = df.join(hydroseq_lookup, left_on="dnhydroseq_str", right_on="hydroseq_str", how="inner").select(
        [
            pl.col("flowpath_id_str").alias("upstream_fp"),
            pl.col("flowpath_id_str_right").alias("downstream_fp"),
        ]
    )

    upstream_network_df = (
        merged.group_by("downstream_fp")
        .agg(pl.col("upstream_fp").alias("upstream_list"))
        .select([pl.col("downstream_fp"), pl.col("upstream_list")])
    )

    upstream_dict: dict[str, list[str]] = dict(
        zip(
            upstream_network_df["downstream_fp"].to_list(),
            upstream_network_df["upstream_list"].to_list(),
            strict=False,
        )
    )

    return upstream_dict


def _extract_outlet_subgraph(
    outlet: str | int,
    graph: rx.PyDiGraph,
    node_indices: dict[str, int] | dict[int, int],
) -> tuple[rx.PyDiGraph, dict[str | int, int], set[int]]:
    """Extract just the subgraph for this outlet.

    Parameters
    ----------
    outlet : str | int
        Outlet flowpath ID (string or integer depending on graph type)
    graph : rx.PyDiGraph
        Full network graph
    node_indices : dict[str, int] | dict[int, int]
        Mapping of flowpath IDs to node indices in full graph

    Returns
    -------
    tuple[rx.PyDiGraph, dict[str | int, int], set[int]]
        - subgraph: rx.PyDiGraph containing only this outlet's tree
        - subgraph_node_indices: dict mapping flowpath_id -> node index in subgraph
        - upstream_node_set: set of node indices from original graph
    """
    sub_indices: dict[Any, int] = {}
    if outlet not in node_indices:
        subgraph = rx.PyDiGraph()
        sub_indices = {outlet: subgraph.add_node(outlet)}
        return subgraph, sub_indices, {0}

    start_node = node_indices[outlet]  # type: ignore
    upstream_nodes: set[int] = rx.ancestors(graph, start_node)
    upstream_nodes.add(start_node)

    subgraph = graph.subgraph(list(upstream_nodes))

    # Create new node_indices mapping for subgraph
    # Map flowpath_id -> new subgraph node index
    for node_idx in range(len(subgraph)):
        flowpath_id = subgraph.get_node_data(node_idx)
        sub_indices[flowpath_id] = node_idx

    return subgraph, sub_indices, upstream_nodes


def _create_dictionary_lookup(df: pl.DataFrame, _id: str) -> dict[str, dict[str, Any]]:
    """Create dictionary lookups for flowpath and divide information.

    Parameters
    ----------
    df : pl.DataFrame
        The dataset to make into a lookup
    _id : str
        The column name to use as the dictionary key

    Returns
    -------
    dict[str, dict[str, Any]]
        The dictionary lookups for the dataframe passed, keyed by string representation of _id
    """
    shapes = gpd.GeoSeries.from_wkb(df["geometry"])
    _lookup = df.to_dicts()
    lookup: dict[str, dict[str, Any]] = {str(row[_id]): row for row in _lookup}
    for _id_, geom in zip(lookup.keys(), shapes, strict=True):
        lookup[_id_]["shapely_geometry"] = geom

    return lookup


def _partition_all_outlet_subgraphs(
    outlets: list[str] | list[int],
    graph: rx.PyDiGraph,
    node_indices: dict[str, int] | dict[int, int],
    reference_flowpaths: pl.DataFrame,
    reference_divides: pl.DataFrame | None = None,
    _id: str = "flowpath_id",
) -> dict[str, dict[str, Any]] | dict[int, dict[str, Any]]:
    """Pre-partition subgraphs and filter data for all outlets.

    Extracts independent subgraphs for each outlet and filters the reference data
    to only include flowpaths and divides within each outlet's drainage basin.

    Parameters
    ----------
    outlets : list[str] | list[int]
        List of outlet flowpath IDs (strings for old system, integers for new system)
    graph : rx.PyDiGraph
        Full network graph containing all flowpaths
    node_indices : dict[str, int] | dict[int, int]
        Mapping of flowpath IDs to node indices in the full graph
    reference_flowpaths : pl.DataFrame
        Full reference flowpaths Polars DataFrame
    reference_divides : pl.DataFrame, optional
        Full reference divides Polars DataFrame, by default None
    _id : str, optional
        Column name to use as the ID field for filtering, by default "flowpath_id"

    Returns
    -------
    dict[str, dict[str, Any]] | dict[int, dict[str, Any]]
        Dictionary mapping outlet ID to partition data containing:
        - "subgraph": rx.PyDiGraph - Subgraph containing only this outlet's drainage basin
        - "node_indices": dict - Node indices mapping for the subgraph
        - "flowpaths": pl.DataFrame - Flowpaths filtered to this outlet's basin
        - "divides": pl.DataFrame | None - Divides filtered to this outlet's basin (or None)
        - "fp_lookup": dict[str, dict[str, Any]] - Flowpath lookup dictionary
        - "div_lookup": dict[str, dict[str, Any]] | None - Divide lookup dictionary (or None)

    Notes
    -----
    - The graph nodes store flowpath IDs (either strings or integers depending on construction)
    - Each outlet's subgraph is independent and contains only upstream flowpaths
    - The fp_lookup and div_lookup dictionaries always use string keys regardless of input type
    """
    partitions: dict[Any, dict[str, Any]] = {}

    for outlet in outlets:
        subgraph, sub_indices, _ = _extract_outlet_subgraph(outlet, graph, node_indices)

        relevant_ids: set[Any] = {subgraph.get_node_data(i) for i in range(len(subgraph))}
        filtered_flowpaths = reference_flowpaths.filter(pl.col(_id).is_in(list(relevant_ids)))
        fp_lookup = _create_dictionary_lookup(filtered_flowpaths, _id)

        if reference_divides is not None:
            filtered_divides = reference_divides.filter(pl.col("divide_id").is_in(list(relevant_ids)))
            div_lookup = _create_dictionary_lookup(filtered_divides, "divide_id")
        else:
            filtered_divides = None
            div_lookup = None

        partitions[outlet] = {
            "subgraph": subgraph,
            "node_indices": sub_indices,
            "flowpaths": filtered_flowpaths,
            "divides": filtered_divides,
            "fp_lookup": fp_lookup,
            "div_lookup": div_lookup,
        }

    return partitions


def build_nhf_graph(flowpaths_gdf: pd.DataFrame) -> rx.PyDiGraph:
    """Builds a rustworkx graph object for the NHF product

    Parameters
    ----------
    flowpaths_gdf : pd.DataFrame
        the flowpaths geodataframe
    """
    graph = rx.PyDiGraph(check_cycle=True)
    fp_id_to_node = {}

    # Add all flowpaths as nodes
    for fp_id in flowpaths_gdf["fp_id"]:
        fp_id_to_node[fp_id] = graph.add_node(fp_id)

    # Verify all flowpaths are represented as nodes
    assert len(graph) == len(flowpaths_gdf), (
        f"Graph has {len(graph)} nodes but there are {len(flowpaths_gdf)} flowpaths"
    )

    # Build edges based on shared nexus points
    # If flowpath A's dn_nex_id == flowpath B's up_nex_id, then A flows into B
    edge_count = 0
    for _, row in flowpaths_gdf.iterrows():
        fp_id = row["fp_id"]
        dn_nex_id = row["dn_nex_id"]

        if pd.isna(dn_nex_id):
            continue

        # Find downstream flowpath(s) that have this nexus as their up_nex_id
        downstream_fps = flowpaths_gdf[flowpaths_gdf["up_nex_id"] == dn_nex_id]

        for _, dn_row in downstream_fps.iterrows():
            dn_fp_id = dn_row["fp_id"]
            graph.add_edge(fp_id_to_node[fp_id], fp_id_to_node[dn_fp_id], None)
            edge_count += 1

    all_nexus_ids = set()
    for val in flowpaths_gdf["dn_nex_id"]:
        if not pd.isna(val):
            all_nexus_ids.add(val)
    for val in flowpaths_gdf["up_nex_id"]:
        if not pd.isna(val):
            all_nexus_ids.add(val)
    return graph
