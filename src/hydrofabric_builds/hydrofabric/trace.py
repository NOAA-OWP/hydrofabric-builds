"""Tracing and classification module for hydrofabric builds."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, NamedTuple

import polars as pl
import rustworkx as rx

from hydrofabric_builds.config import HFConfig
from hydrofabric_builds.schemas.hydrofabric import Classifications

logger = logging.getLogger(__name__)


class FPInfo(NamedTuple):
    """Flowpath info extracted from lookup."""

    id: str
    order: int
    area: float
    to_id: str


@dataclass
class Context:
    """Immutable trace context shared across processing."""

    graph: rx.PyDiGraph
    node_indices: dict[str, int]
    fp_lookup: dict[str, dict[str, Any]]
    div_ids: set[str]
    threshold: float
    headwater_virtual_length_threshold: float


@dataclass
class State:
    """Mutable trace state accumulated during processing."""

    queue: list[str] = field(default_factory=list)
    processed: set[str] = field(default_factory=set)
    cumulative_areas: dict[str, float] = field(default_factory=dict)
    independent: set[str] = field(default_factory=set)
    connectors: set[str] = field(default_factory=set)
    non_nextgen: set[str] = field(default_factory=set)
    aggregations: set[tuple[str, str]] = field(default_factory=set)
    aggregation_set: set[str] = field(default_factory=set)
    non_nextgen_virtual_pairs: list[tuple[str, ...]] = field(default_factory=list)
    non_nextgen_virtual_sources: set[str] = field(default_factory=set)
    force_queue: set[str] = field(default_factory=set)


def get_info(ctx: Context, fid: str) -> FPInfo:
    """Get flowpath info from lookup."""
    row = ctx.fp_lookup[fid]
    return FPInfo(
        id=fid,
        order=int(row["streamorder"]),
        area=float(row["areasqkm"]),
        to_id=str(int(row["flowpath_toid"])),
    )


def get_upstreams(ctx: Context, fid: str) -> list[str]:
    """Get immediate upstream flowpath IDs."""
    if fid not in ctx.node_indices:
        return []
    idx = ctx.node_indices[fid]
    return [str(ctx.graph[i]) for i in ctx.graph.predecessor_indices(idx)]


def get_ancestors(ctx: Context, fid: str) -> set[str]:
    """Get all ancestor flowpath IDs."""
    if fid not in ctx.node_indices:
        return set()
    idx = ctx.node_indices[fid]
    return {ctx.graph[i] for i in rx.ancestors(ctx.graph, idx)}


def lacks_deep_divides(ctx: Context, upstreams: list[str]) -> bool:
    """Check if Layer 1 and Layer 2 upstreams lack divides."""
    if any(u in ctx.div_ids for u in upstreams):
        return False
    return not any(u2 in ctx.div_ids for u in upstreams for u2 in get_upstreams(ctx, u))


def find_longest_path_ids(ctx: Context, start_id: str) -> set[str]:
    """Find IDs on the longest path from ancestors to start_id."""
    if start_id not in ctx.node_indices:
        return set()

    idx = ctx.node_indices[start_id]
    anc_indices = rx.ancestors(ctx.graph, idx)
    subgraph = ctx.graph.subgraph(list(anc_indices) + [idx])
    simple_paths = rx.all_pairs_all_simple_paths(subgraph)
    longest = max(
        (p for y in simple_paths.values() for z in y.values() for p in z),
        key=len,
    )
    return {subgraph[i] for i in longest}


def enqueue(st: State, ids: list[str]) -> None:
    """Append unprocessed IDs to the queue."""
    for fid in ids:
        if fid not in st.processed:
            st.queue.append(fid)


def aggregate(st: State, src: str, tgt: str) -> None:
    """Record an aggregation pair."""
    st.aggregations.add((src, tgt))
    st.aggregation_set.update([src, tgt])


def mark_virtual_tree(ctx: Context, st: State, start_id: str, tgt_id: str) -> None:
    """Iteratively mark tree as virtual/aggregated."""
    stack = [start_id]
    while stack:
        curr = stack.pop()
        st.non_nextgen.add(curr)
        aggregate(st, curr, tgt_id)
        st.processed.add(curr)
        if curr not in st.non_nextgen_virtual_sources:
            st.non_nextgen_virtual_sources.add(curr)
            st.non_nextgen_virtual_pairs.append((curr, tgt_id))
        stack.extend(get_upstreams(ctx, curr))


def traverse_and_aggregate(ctx: Context, st: State, start_id: str) -> None:
    """Find longest path; mark others virtual."""
    if start_id not in ctx.node_indices:
        return

    idx = ctx.node_indices[start_id]
    anc_indices = rx.ancestors(ctx.graph, idx)
    anc_ids = [ctx.graph[i] for i in anc_indices] + [start_id]
    longest_ids = find_longest_path_ids(ctx, start_id)
    all_non_nextgen = set(anc_ids).isdisjoint(ctx.div_ids)

    if all_non_nextgen:
        st.non_nextgen.add(start_id)

    queue = [start_id]
    while queue:
        curr = queue.pop(0)
        fp = get_info(ctx, curr)
        st.processed.add(curr)

        if curr not in longest_ids or all_non_nextgen:
            st.non_nextgen.add(curr)
            if curr not in st.non_nextgen_virtual_sources:
                st.non_nextgen_virtual_sources.add(curr)
                st.non_nextgen_virtual_pairs.append((curr, fp.to_id))

        aggregate(st, curr, start_id)
        queue.extend(get_upstreams(ctx, curr))


def handle_headwater(ctx: Context, st: State, curr_id: str, fp: FPInfo) -> None:
    """Handle a headwater flowpath with no upstreams."""
    if curr_id in ctx.div_ids:
        length_km = ctx.fp_lookup[curr_id].get("lengthkm", float("inf"))
        if fp.order == 1 and fp.to_id != "0" and length_km < ctx.headwater_virtual_length_threshold:
            st.non_nextgen.add(curr_id)
            if curr_id not in st.non_nextgen_virtual_sources:
                st.non_nextgen_virtual_sources.add(curr_id)
                st.non_nextgen_virtual_pairs.append((curr_id, fp.to_id))
        elif curr_id not in st.aggregation_set:
            st.independent.add(curr_id)
    else:
        st.non_nextgen.add(curr_id)
        if curr_id not in st.non_nextgen_virtual_sources:
            st.non_nextgen_virtual_sources.add(curr_id)
            st.non_nextgen_virtual_pairs.append((curr_id, fp.to_id))


def handle_single_upstream_flowpath(ctx: Context, st: State, curr_id: str, fp: FPInfo, up_id: str) -> None:
    """Handle a flowpath with exactly one upstream."""
    if curr_id not in ctx.div_ids:
        if fp.order <= 2:
            traverse_and_aggregate(ctx, st, curr_id)
            return

        if lacks_deep_divides(ctx, [up_id]):
            if get_ancestors(ctx, curr_id).isdisjoint(ctx.div_ids):
                if fp.to_id in st.connectors:
                    traverse_and_aggregate(ctx, st, curr_id)
                else:
                    mark_virtual_tree(ctx, st, curr_id, fp.to_id)
                    st.independent.discard(fp.to_id)
                return

    cum_area = st.cumulative_areas.get(curr_id, 0.0) + fp.area

    if fp.area < 0.005:
        aggregate(st, curr_id, up_id)
        if fp.to_id in ctx.fp_lookup:
            st.cumulative_areas[up_id] = ctx.fp_lookup[fp.to_id]["areasqkm"] + fp.area
    elif cum_area < ctx.threshold:
        aggregate(st, curr_id, up_id)
        st.cumulative_areas[up_id] = cum_area
    else:
        if curr_id in ctx.div_ids:
            if up_id not in ctx.div_ids:
                aggregate(st, curr_id, up_id)
            elif curr_id not in st.aggregation_set:
                st.independent.add(curr_id)
        else:
            aggregate(st, curr_id, up_id)

    enqueue(st, [up_id])


def handle_multi_upstream_flowpath(
    ctx: Context, st: State, curr_id: str, fp: FPInfo, upstreams: list[str]
) -> None:
    """Handle a flowpath with multiple upstreams that has a divide."""
    ups_info = [get_info(ctx, u) for u in upstreams if u in ctx.fp_lookup and u not in st.processed]
    order_1 = [u for u in ups_info if u.order == 1]
    higher = [u for u in ups_info if u.order > 1]
    cum_area = st.cumulative_areas.get(curr_id, 0.0) + fp.area

    if not higher:
        best = max(order_1, key=lambda x: (x.order, x.area, x.id))
        if fp.area < 0.005:
            tgt = best.id if fp.to_id == "0" else fp.to_id
            aggregate(st, curr_id, tgt)
            st.independent.discard(tgt)
            if fp.to_id != "0":
                if fp.to_id in st.connectors:
                    st.connectors.remove(fp.to_id)
            enqueue(st, upstreams)
        elif cum_area < ctx.threshold:
            aggregate(st, curr_id, best.id)
            st.cumulative_areas[best.id] = cum_area
            for u in order_1:
                if u.id != best.id:
                    mark_virtual_tree(ctx, st, u.id, curr_id)
            enqueue(st, [best.id])
        else:
            if curr_id not in st.aggregation_set:
                st.connectors.add(curr_id)
            enqueue(st, upstreams)
        return

    if len(upstreams) == 2:
        if len(higher) > 1:
            if curr_id not in st.aggregation_set:
                st.connectors.add(curr_id)
            enqueue(st, upstreams)
        else:
            high_id, o1_id = higher[0].id, order_1[0].id
            if fp.area < 0.005:
                if fp.to_id == "0":
                    aggregate(st, curr_id, high_id)
                    st.independent.discard(high_id)
                    mark_virtual_tree(ctx, st, o1_id, curr_id)
                    enqueue(st, [high_id])
                else:
                    if fp.to_id in st.connectors:
                        st.connectors.remove(fp.to_id)
                    aggregate(st, curr_id, fp.to_id)
                    st.independent.discard(fp.to_id)
                    enqueue(st, upstreams)
            elif cum_area < ctx.threshold:
                aggregate(st, curr_id, high_id)
                st.cumulative_areas[high_id] = cum_area
                mark_virtual_tree(ctx, st, o1_id, curr_id)
                enqueue(st, [h.id for h in higher])
            else:
                if curr_id not in st.aggregation_set:
                    st.connectors.add(curr_id)
                enqueue(st, upstreams)
        return

    # 3+ upstreams
    if fp.area < 0.005:
        if fp.to_id in st.connectors:
            st.connectors.remove(fp.to_id)
        aggregate(st, curr_id, fp.to_id)
        st.independent.discard(fp.to_id)
    elif curr_id not in st.aggregation_set:
        st.connectors.add(curr_id)
    enqueue(st, upstreams)


def handle_multi_upstream_flowpath_no_divide(
    ctx: Context, st: State, curr_id: str, fp: FPInfo, upstreams: list[str]
) -> None:
    """Handle a flowpath with multiple upstreams that lacks a divide."""
    ups_info = [get_info(ctx, u) for u in upstreams if u in ctx.fp_lookup and u not in st.processed]

    if fp.order == 1:
        if fp.to_id in st.connectors:
            traverse_and_aggregate(ctx, st, curr_id)
        else:
            mark_virtual_tree(ctx, st, curr_id, fp.to_id)
            st.independent.discard(fp.to_id)
        return

    if fp.to_id == "0":
        best = max(ups_info, key=lambda x: (x.order, x.area, x.id))
        aggregate(st, curr_id, best.id)
        for u in ups_info:
            if u.id != best.id:
                mark_virtual_tree(ctx, st, u.id, curr_id)
        enqueue(st, [best.id])
        return

    ds_ups = get_upstreams(ctx, fp.to_id)
    other_laterals = [uid for uid in ds_ups if uid != curr_id]

    if not other_laterals:
        if fp.to_id in st.connectors:
            st.connectors.remove(fp.to_id)
        aggregate(st, curr_id, fp.to_id)
        st.independent.discard(fp.to_id)

        all_ups_no_div = all(uid not in ctx.div_ids for uid in upstreams)
        some_ups_no_div = any(uid not in ctx.div_ids for uid in upstreams)

        if all_ups_no_div:
            if get_ancestors(ctx, curr_id).isdisjoint(ctx.div_ids):
                traverse_and_aggregate(ctx, st, curr_id)
                return
            best = max(ups_info, key=lambda x: (x.order, x.area, x.id))
            aggregate(st, curr_id, best.id)
            for uid in upstreams:
                if uid != best.id:
                    st.force_queue.add(uid)
                    mark_virtual_tree(ctx, st, uid, curr_id)
            enqueue(st, [best.id])
            return

        if some_ups_no_div:
            best = max(ups_info, key=lambda x: (x.order, x.area, x.id))
            if best.id not in ctx.div_ids:
                aggregate(st, curr_id, best.id)
                for uid in upstreams:
                    if uid != best.id:
                        st.force_queue.add(uid)
                        mark_virtual_tree(ctx, st, uid, curr_id)
                enqueue(st, [best.id])
                return
            if curr_id not in st.aggregation_set:
                st.connectors.add(curr_id)
            enqueue(st, upstreams)
            return

        # All upstreams have divides
        if curr_id not in st.aggregation_set:
            st.connectors.add(curr_id)
        for u in ups_info:
            if u.order == 1:
                mark_virtual_tree(ctx, st, u.id, curr_id)
        enqueue(st, [u.id for u in ups_info if u.order > 1])
        return

    if lacks_deep_divides(ctx, upstreams):
        if fp.to_id in st.connectors:
            traverse_and_aggregate(ctx, st, curr_id)
        else:
            mark_virtual_tree(ctx, st, curr_id, fp.to_id)
            st.independent.discard(fp.to_id)
        return

    o_1_2 = [u for u in ups_info if u.order <= 2]
    higher = [u for u in ups_info if u.order > 2]

    if o_1_2:
        best = max(ups_info, key=lambda x: (x.order, x.area, x.id))
        aggregate(st, curr_id, best.id)

        if higher:
            for u in o_1_2:
                st.force_queue.add(u.id)
                mark_virtual_tree(ctx, st, u.id, curr_id)
            enqueue(st, [u.id for u in higher])
        else:
            best_sub = max(o_1_2, key=lambda x: (x.order, x.area, x.id))
            aggregate(st, curr_id, best_sub.id)
            for u in o_1_2:
                if u.id != best_sub.id:
                    st.force_queue.add(u.id)
                    mark_virtual_tree(ctx, st, u.id, curr_id)
            enqueue(st, [best_sub.id])
    else:
        if fp.to_id in st.connectors:
            st.connectors.remove(fp.to_id)
        aggregate(st, curr_id, fp.to_id)
        st.independent.discard(fp.to_id)
        enqueue(st, upstreams)


def _trace_stack(
    start_id: str,
    div_ids: set[str],
    cfg: HFConfig,
    partition_data: dict[str, Any],
) -> Classifications:
    """Classify hydrofabric flowpaths.

    Args:
        start_id: Starting flowpath ID
        div_ids: Set of divide flowpath IDs
        cfg: Configuration
        partition_data: Dict with subgraph, node_indices, fp_lookup

    Returns
    -------
        Classifications result
    """
    # import here to avoid circular import
    from hydrofabric_builds.hydrofabric.anomalies import ANOMALY_HANDLERS

    ctx = Context(
        graph=partition_data["subgraph"],
        node_indices=partition_data["node_indices"],
        fp_lookup=partition_data["fp_lookup"],
        div_ids=div_ids,
        threshold=cfg.build.divide_aggregation_threshold,
        headwater_virtual_length_threshold=cfg.build.headwater_virtual_length_threshold,
    )

    st = State(queue=[start_id])

    if div_ids:
        while st.queue:
            curr_id = st.queue.pop(0)
            if curr_id in st.processed:
                continue
            st.processed.add(curr_id)

            if handler := ANOMALY_HANDLERS.get(curr_id):
                handler(ctx, st, curr_id, get_info(ctx, curr_id).to_id)
                continue

            fp = get_info(ctx, curr_id)
            upstreams = get_upstreams(ctx, curr_id)

            match (len(upstreams), curr_id in ctx.div_ids):
                case (0, _):
                    handle_headwater(ctx, st, curr_id, fp)
                case (1, _):
                    handle_single_upstream_flowpath(ctx, st, curr_id, fp, upstreams[0])
                case (_, True):
                    handle_multi_upstream_flowpath(ctx, st, curr_id, fp, upstreams)
                case (_, False):
                    handle_multi_upstream_flowpath_no_divide(ctx, st, curr_id, fp, upstreams)

    res = Classifications()
    res.processed_flowpaths = st.processed
    res.independent_flowpaths = st.independent
    res.connector_segments = list(st.connectors)
    res.non_nextgen_flowpaths = st.non_nextgen
    res.aggregation_pairs = list(st.aggregations)
    res.aggregation_set = st.aggregation_set
    res.non_nextgen_virtual_flowpath_pairs = st.non_nextgen_virtual_pairs
    res.force_queue_flowpaths = st.force_queue
    return res


def _trace_single_flowpath_attributes(
    outlet_fp_id: str, partition_data: dict[str, Any], id_offset: int, hydroseq_offset: int
) -> tuple[pl.DataFrame, int, int]:
    """Trace flowpath attributes for a single outlet's drainage basin.

    Parameters
    ----------
    outlet_fp_id : str
        The outlet flowpath ID for this basin
    partition_data : dict[str, Any]
        Contains:
        - "subgraph": rx.PyDiGraph (only this outlet's tree)
        - "node_indices": dict (fp_id -> node index in subgraph)
        - "flowpaths": pl.DataFrame (filtered to this outlet)
        - "fp_lookup": dict (flowpath attributes)
    id_offset : int
        Starting ID for mainstem numbering
    hydroseq_offset : int
        Starting hydroseq value

    Returns
    -------
    tuple[pl.DataFrame, int, int]
        Updated flowpaths with total_da_sqkm, mainstem_lp, path_length,
        dn_hydroseq, hydroseq, and stream_order columns; next tributary offset;
        next hydroseq value.
    """
    basin_graph = partition_data["subgraph"]
    basin_node_indices = partition_data["node_indices"]
    fp_lookup = partition_data["fp_lookup"]

    # Initialize node data in the basin graph
    for node_idx in basin_graph.node_indices():
        fp_id = str(basin_graph[node_idx])

        basin_graph[node_idx] = {
            "fp_id": fp_id,
            "area_sqkm": fp_lookup[fp_id]["area_sqkm"],
            "length_km": fp_lookup[fp_id]["length_km"],
            "total_da_sqkm": None,
            "mainstem_lp": None,
            "path_length": None,
            "dn_hydroseq": None,
            "hydroseq": None,
            "streamorder": None,
        }

    outlet_idx = basin_node_indices[outlet_fp_id]

    # Get topological order for this basin
    try:
        topo_order = rx.topological_sort(basin_graph)
    except rx.DAGHasCycle as e:
        raise AssertionError(f"Basin {outlet_fp_id} contains cycles") from e

    # PASS 1: Traverse from ancestors to OUTLET (forward topo order)
    for node_idx in topo_order:
        in_edges = basin_graph.in_edges(node_idx)

        upstream_total = sum(basin_graph[src_idx]["total_da_sqkm"] for src_idx, _, _ in in_edges)

        basin_graph[node_idx]["total_da_sqkm"] = upstream_total + basin_graph[node_idx]["area_sqkm"]

        # Calculate stream order (Strahler order)
        if not in_edges:
            # Headwater - order 1
            basin_graph[node_idx]["streamorder"] = 1
        else:
            upstream_orders = [basin_graph[src_idx]["streamorder"] for src_idx, _, _ in in_edges]
            max_order = max(upstream_orders)
            count_max = upstream_orders.count(max_order)

            # If two or more streams of same order meet, increment order
            if count_max >= 2:
                basin_graph[node_idx]["streamorder"] = max_order + 1
            else:
                basin_graph[node_idx]["streamorder"] = max_order

    # PASS 2: Traverse from OUTLET to ANCESTORS (reverse topo order)
    current_mainstem_id = id_offset
    current_hydroseq = hydroseq_offset
    processed: set[int] = set()

    # Initialize outlet
    basin_graph[outlet_idx]["path_length"] = 0.0
    basin_graph[outlet_idx]["dn_hydroseq"] = 0
    basin_graph[outlet_idx]["hydroseq"] = current_hydroseq
    basin_graph[outlet_idx]["flowpath_toid"] = "0"
    basin_graph[outlet_idx]["mainstem_lp"] = current_mainstem_id
    current_hydroseq += 1
    current_mainstem_id += 1

    # Calculate path lengths and hydroseq (reverse topo order)
    for node_idx in reversed(topo_order):
        if node_idx == outlet_idx:
            continue

        # Assign hydroseq (increases going upstream)
        basin_graph[node_idx]["hydroseq"] = current_hydroseq
        current_hydroseq += 1

        # get the downstream node; if multiple downstream nodes, get the one with longest path_length (dist to outlet)
        downstream_nodes = [tgt_idx for _, tgt_idx, _ in basin_graph.out_edges(node_idx)]
        downstream_idx = max(
            downstream_nodes,
            key=lambda idx: basin_graph[idx]["path_length"]
            if basin_graph[idx]["path_length"] is not None
            else -1,
        )
        if basin_graph[downstream_idx]["hydroseq"] is None:
            raise ValueError(
                f"Downstream node {downstream_idx} hydroseq is None when processing node {node_idx}. This indicates an error in the topological sorting or traversal logic."
            )

        # downstream connection
        basin_graph[node_idx]["dn_hydroseq"] = basin_graph[downstream_idx]["hydroseq"]

        # path_length to outlet
        basin_graph[node_idx]["path_length"] = (
            basin_graph[downstream_idx]["path_length"] + basin_graph[downstream_idx]["length_km"]
        )

        # update mainstem_lp for current node and other branches if this node is a confluence
        if downstream_idx in processed:
            continue
        mainstem_lp = basin_graph[downstream_idx]["mainstem_lp"]
        upstream_nodes = [src_idx for src_idx, _, _ in basin_graph.in_edges(downstream_idx)]
        if len(upstream_nodes) > 1:  # confluence
            # mainstem is the upstream node with highest stream order, then by largest total_da_sqkm as tiebreaker
            mainstem_node = max(
                upstream_nodes,
                key=lambda idx: (basin_graph[idx]["streamorder"], basin_graph[idx]["total_da_sqkm"]),
            )
        else:  # no confluence, just one upstream node
            mainstem_node = upstream_nodes[0]
            assert mainstem_node == node_idx, "If only one upstream node, it should be the current node"
        for up_idx in upstream_nodes:
            if up_idx == mainstem_node:
                basin_graph[up_idx]["mainstem_lp"] = mainstem_lp
            else:
                basin_graph[up_idx]["mainstem_lp"] = current_mainstem_id
                current_mainstem_id += 1
        processed.add(downstream_idx)

    # Extract results from graph into lists
    fp_ids = []
    total_das = []
    mainstems = []
    path_lengths = []
    dn_hydroseqs = []
    hydroseqs = []
    streamorders = []

    for node_idx in basin_graph.node_indices():
        node_data = basin_graph[node_idx]
        fp_ids.append(node_data["fp_id"])
        total_das.append(node_data["total_da_sqkm"])
        mainstems.append(node_data["mainstem_lp"])
        path_lengths.append(node_data["path_length"])
        dn_hydroseqs.append(node_data["dn_hydroseq"])
        hydroseqs.append(node_data["hydroseq"])
        streamorders.append(node_data["streamorder"])

    traced_df = pl.DataFrame(
        {
            "fp_id": fp_ids,
            "total_da_sqkm": total_das,
            "mainstem_lp": mainstems,
            "path_length": path_lengths,
            "dn_hydroseq": dn_hydroseqs,
            "hydroseq": hydroseqs,
            "stream_order": streamorders,
            "terminalpa": hydroseq_offset,
        }
    )

    return traced_df, current_mainstem_id, current_hydroseq
