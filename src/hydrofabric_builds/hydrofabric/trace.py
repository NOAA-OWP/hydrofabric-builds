"""Tracing and classification module for hydrofabric builds"""

import logging
from collections import deque
from typing import Any

import polars as pl
import rustworkx as rx

from hydrofabric_builds.config import HFConfig
from hydrofabric_builds.schemas.hydrofabric import Classifications

logger = logging.getLogger(__name__)


def _get_unprocessed_upstream_info(
    upstream_ids: list[str], fp_lookup: dict[str, dict[str, Any]], processed: set[str]
) -> list[dict[str, Any]]:
    """Get info for unprocessed upstream flowpaths.

    Parameters
    ----------
    upstream_ids : list[str]
        List of upstream flowpath IDs
    fp_lookup : dict[str, dict[str, Any]]
        Dictionary mapping flowpath_id -> flowpath attributes
    processed : set[str]
        Set of already processed flowpath IDs

    Returns
    -------
    list[dict[str, Any]]
        List of flowpath info dictionaries for unprocessed upstreams
    """
    if not upstream_ids:
        return []

    result: list[dict[str, Any]] = []
    for uid in upstream_ids:
        uid_str = str(uid)
        if uid_str in processed or uid_str not in fp_lookup:
            continue

        row = fp_lookup[uid_str]
        result.append(
            {
                "flowpath_id": uid_str,
                "areasqkm": float(row["areasqkm"]),
                "streamorder": int(row["streamorder"]),
                "lengthkm": float(row["lengthkm"]),
                "totdasqkm": float(row["totdasqkm"]),
                "mainstemlp": float(row.get("mainstemlp", 0)),
            }
        )

    return result


def _queue_upstream(
    upstream_ids: list[str], to_process: deque[str], processed: set[str], unprocessed_only: bool = False
) -> None:
    """Queue upstream flowpaths for processing.

    Parameters
    ----------
    upstream_ids : list[str]
        List of upstream flowpath IDs
    to_process : deque[str]
        Queue of flowpaths to process
    processed : set[str]
        Set of already processed flowpath IDs
    unprocessed_only : bool, optional
        If True, only queue unprocessed flowpaths, by default False
    """
    for uid in upstream_ids:
        uid_str = str(uid)
        if not unprocessed_only or uid_str not in processed:
            to_process.append(uid_str)


def _get_upstream_ids(flowpath_id: str, graph: rx.PyDiGraph, node_indices: dict[str, int]) -> list[str]:
    """Get upstream flowpath IDs from the graph.

    Parameters
    ----------
    flowpath_id : str
        The flowpath ID to get upstreams for
    graph : rx.PyDiGraph
        Network graph
    node_indices : dict[str, int]
        Mapping of flowpath IDs to node indices

    Returns
    -------
    list[str]
        List of upstream flowpath IDs
    """
    if flowpath_id not in node_indices:
        return []

    fp_idx = node_indices[flowpath_id]
    return [str(graph[idx]) for idx in graph.predecessor_indices(fp_idx)]


def _traverse_and_mark_as_non_nextgen(
    start_id: str,
    downstream_id: str,
    result: Classifications,
    graph: rx.PyDiGraph,
    node_indices: dict[str, int],
) -> None:
    """Traverse all upstream flowpaths and mark them as non_nextgen, aggregating to downstream.

    This marks flowpaths as virtual and creates aggregation pairs.
    Used for small tributaries that should be aggregated into larger streams.
    Continues traversing through ALL upstreams regardless of divides.

    Parameters
    ----------
    start_id : str
        Starting flowpath ID
    downstream_id : str
        Target downstream flowpath ID for aggregation
    result : Classifications
        Results container to update
    graph : rx.PyDiGraph
        Network graph
    node_indices : dict[str, int]
        Mapping of flowpath IDs to node indices
    """
    result.non_nextgen_flowpaths.add(start_id)
    result.aggregation_pairs.append((start_id, downstream_id))
    result.aggregation_set.add(start_id)
    result.aggregation_set.add(downstream_id)
    result.processed_flowpaths.add(start_id)
    result.non_nextgen_virtual_flowpath_pairs.append((start_id, downstream_id))

    next_upstream_ids = _get_upstream_ids(start_id, graph, node_indices)
    if len(next_upstream_ids) > 0:
        for next_id in next_upstream_ids:
            _traverse_and_mark_as_non_nextgen(next_id, downstream_id, result, graph, node_indices)


def _traverse_and_aggregate(
    start_id: str,
    result: Classifications,
    graph: rx.PyDiGraph,
    node_indices: dict[str, int],
    fp_lookup: dict[str, Any],
    div_ids: set,
) -> None:
    """Traverse all upstream flowpaths and mark them as virtual, aggregating to downstream.

    This marks flowpaths as virtual and creates aggregation pairs.
    Used for small tributaries that should be aggregated into larger streams.
    Continues traversing through ALL upstreams regardless of divides.

    Parameters
    ----------
    start_id : str
        Starting flowpath ID
    result : Classifications
        Results container to update
    graph : rx.PyDiGraph
        Network graph
    node_indices : dict[str, int]
        Mapping of flowpath IDs to node indices
    """
    if start_id not in node_indices:
        return

    _ = fp_lookup[start_id]["streamorder"]
    start_idx = node_indices[start_id]
    ancestor_indices = rx.ancestors(graph, start_idx)
    ancestor_ids = [graph[idx] for idx in ancestor_indices] + [start_id]

    # Find longest path in the ancestor subgraph
    ancestor_subgraph = graph.subgraph(list(ancestor_indices) + [start_idx])
    simple_path_pairs = rx.all_pairs_all_simple_paths(ancestor_subgraph)
    longest_path = max(
        (u for y in simple_path_pairs.values() for z in y.values() for u in z),
        key=lambda x: len(x),
    )
    longest_path_ids = {ancestor_subgraph[idx] for idx in longest_path}

    all_non_nextgen = False
    if set(ancestor_ids).isdisjoint(div_ids):
        result.non_nextgen_flowpaths.add(start_id)
        all_non_nextgen = True

    _ref_fp_stack: deque[str] = deque([start_id])

    # tracing through all segments
    while _ref_fp_stack:
        current_id = _ref_fp_stack.popleft()
        fp_info: dict[str, Any] = fp_lookup[current_id]
        upstream_ids = _get_upstream_ids(current_id, graph, node_indices)

        result.processed_flowpaths.add(current_id)
        ds_id = str(int(fp_info["flowpath_toid"]))

        if current_id not in longest_path_ids or all_non_nextgen:
            result.non_nextgen_flowpaths.add(current_id)
            result.non_nextgen_virtual_flowpath_pairs.append((current_id, ds_id))
        else:
            result.virtual_flowpath_pairs.append((current_id, ds_id))

        result.aggregation_pairs.append((current_id, start_id))
        result.aggregation_set.add(current_id)

        if upstream_ids:
            for uid in upstream_ids:
                _ref_fp_stack.append(uid)


def _fix_no_divide_anomalies(
    current_id: str,
    result: Classifications,
    fp_lookup: dict[str, Any],
    digraph: rx.PyDiGraph,
    node_indices: dict[str, int],
    to_process: deque,
    div_ids: set,
) -> bool:
    """Provides rules that are outside of the current specification to account for bad flowpaths

    Parameters
    ----------
    current_id : str
        Starting flowpath ID
    result : Classifications
        Results container to update
    fp_lookup : dict[str, Any]
        lookup for all information related to flowpaths
    graph : rx.PyDiGraph
        Network graph
    node_indices : dict[str, int]
        Mapping of flowpath IDs to node indices
    to_process: deque
        the stack of flowpaths to model
    div_ids: set
        the set of flowpath ids that have divides delineated

    Returns
    -------
    bool
        if there was a fix applied
    """
    ds_id = str(int(fp_lookup[current_id]["flowpath_toid"]))

    if current_id in ["9272756"]:
        # Flowpath in VPU 8 upstream of 9272686 that is a no-divide connector and does not create a divide
        result.virtual_flowpath_pairs.append((current_id, ds_id))
        result.aggregation_set.add(current_id)

        _traverse_and_mark_as_non_nextgen("9272732", current_id, result, digraph, node_indices)
        result.aggregation_set.add("9272732")

        result.aggregation_pairs.append((current_id, "9272706"))
        result.aggregation_set.add("9272706")
        result.virtual_flowpath_pairs.append(("9272706", current_id))

        _traverse_and_mark_as_non_nextgen("9272688", current_id, result, digraph, node_indices)

        # Chain: 9272318 → 9270812 → 9272686 → 9272706
        result.virtual_flowpath_pairs.append(("9272686", "9272706"))
        result.virtual_flowpath_pairs.append(("9270812", "9272686"))
        result.virtual_flowpath_pairs.append(("9272318", "9270812"))
        result.aggregation_pairs.append(("9272706", "9272686"))
        result.aggregation_pairs.append(("9272686", "9270812"))
        result.aggregation_pairs.append(("9270812", "9272318"))
        result.aggregation_set.add("9272686")
        result.aggregation_set.add("9270812")
        result.aggregation_set.add("9272318")
        result.independent_flowpaths.discard(ds_id)
        _queue_upstream(
            ["9270644", "9272308"],
            to_process,
            result.processed_flowpaths,
            unprocessed_only=True,
        )
        return True

    elif current_id in ["7262417"]:
        # Flowpath in 10L with too large of an upstream area within an irigated field
        _traverse_and_aggregate("7262465", result, digraph, node_indices, fp_lookup, div_ids)
        result.independent_flowpaths.add(current_id)
        result.virtual_flowpath_pairs.append((current_id, ds_id))
        _queue_upstream(
            ["7262413"],
            to_process,
            result.processed_flowpaths,
            unprocessed_only=True,
        )
        return True

    elif current_id in ["7262801"]:
        # Upstream Flowpaths in 10L with many no divides
        result.virtual_flowpath_pairs.append((current_id, ds_id))
        result.aggregation_pairs.append(("7262727", current_id))
        result.virtual_flowpath_pairs.append(("7262727", current_id))
        result.non_nextgen_flowpaths.add("7262683")
        result.non_nextgen_virtual_flowpath_pairs.append(("7262683", "7262727"))
        result.aggregation_pairs.append(("7262727", "7262683"))
        result.aggregation_set.add("7262683")
        result.aggregation_set.add("7262727")
        result.aggregation_set.add(current_id)

        result.non_nextgen_flowpaths.add("7262819")
        result.aggregation_pairs.append(("7262819", "7262727"))
        result.aggregation_set.add("7262819")
        result.non_nextgen_virtual_flowpath_pairs.append(("7262819", "7262727"))

        # Chain: 7262959 → 7262887 → 7262805 → 7262727
        result.aggregation_pairs.append(("7262727", "7262805"))
        result.aggregation_set.add("7262805")
        result.virtual_flowpath_pairs.append(("7262805", "7262727"))

        _traverse_and_mark_as_non_nextgen("7262803", "7262805", result, digraph, node_indices)

        result.aggregation_pairs.append(("7262805", "7262887"))
        result.aggregation_set.add("7262887")
        result.virtual_flowpath_pairs.append(("7262887", "7262805"))

        _traverse_and_mark_as_non_nextgen("7262933", "7262887", result, digraph, node_indices)

        result.aggregation_pairs.append(("7262887", "7262959"))
        result.virtual_flowpath_pairs.append(("7262959", "7262887"))
        result.aggregation_set.add("7262959")
        _queue_upstream(
            ["940200288"],
            to_process,
            result.processed_flowpaths,
            unprocessed_only=True,
        )
        return True

    elif current_id in ["7261789"]:
        # 7261789: upstream Flowpaths in 10L with bad flowpath delination and many non-divide flowpaths
        result.virtual_flowpath_pairs.append((current_id, ds_id))
        _traverse_and_mark_as_non_nextgen("7261795", current_id, result, digraph, node_indices)
        result.aggregation_pairs.append((current_id, "7262239"))
        result.virtual_flowpath_pairs.append(("7262239", current_id))
        result.aggregation_pairs.append(("7262239", "7262253"))
        result.virtual_flowpath_pairs.append(("7262253", "7262239"))
        result.aggregation_set.add("7262239")
        result.aggregation_set.add(current_id)
        result.aggregation_set.add(ds_id)
        result.aggregation_set.add("7262253")

        _traverse_and_mark_as_non_nextgen("7262255", "7262253", result, digraph, node_indices)
        result.aggregation_pairs.append(("7262253", "7262291"))
        result.virtual_flowpath_pairs.append(("7262291", "7262253"))
        result.aggregation_set.add("7262291")

        _queue_upstream(
            ["7262417"],
            to_process,
            result.processed_flowpaths,
            unprocessed_only=True,
        )
        return True

    elif current_id in ["7264167"]:
        # Flowpath in 10L with many flowpaths connected that have no divides. Stream order 3
        result.aggregation_pairs.append((current_id, "7260693"))
        result.aggregation_set.add(current_id)
        result.aggregation_set.add("7260693")
        result.virtual_flowpath_pairs.append(("7260693", current_id))

        _traverse_and_mark_as_non_nextgen("7264125", current_id, result, digraph, node_indices)

        # Chain: 7260303 → 7260373 → 7260693
        result.aggregation_pairs.append(("7260693", "7260373"))
        result.aggregation_set.add("7260373")
        result.virtual_flowpath_pairs.append(("7260373", "7260693"))

        result.aggregation_pairs.append(("7260373", "7260303"))
        result.aggregation_set.add("7260303")
        result.virtual_flowpath_pairs.append(("7260303", "7260373"))

        # Side branch: 7264103 → 7260373
        result.aggregation_pairs.append(("7260373", "7264103"))
        result.aggregation_set.add("7264103")
        result.non_nextgen_virtual_flowpath_pairs.append(("7264103", "7260373"))

        result.virtual_flowpath_pairs.append((current_id, ds_id))
        _queue_upstream(
            ["7264107"],
            to_process,
            result.processed_flowpaths,
            unprocessed_only=True,
        )
        return True

    elif current_id in ["13257313", "4342468"]:
        # 13257313: Flowpath in 10U with many flowpaths connected that have no divides. Stream order 3
        # 4342468: with many flowpaths connected that have no divides. Stream order 3
        _traverse_and_mark_as_non_nextgen(current_id, ds_id, result, digraph, node_indices)
        result.independent_flowpaths.discard(ds_id)
        return True

    elif current_id in ["22769238"]:
        # Flowpath in VPU 8 on the coast that doesn't have a catchment
        _traverse_and_mark_as_non_nextgen("22769236", current_id, result, digraph, node_indices)
        result.aggregation_pairs.append((current_id, "22769244"))
        result.aggregation_set.add(current_id)
        result.aggregation_set.add("22769244")
        result.virtual_flowpath_pairs.append(("22769244", current_id))
        result.virtual_flowpath_pairs.append((current_id, ds_id))
        _queue_upstream(
            ["22769244"],
            to_process,
            result.processed_flowpaths,
            unprocessed_only=True,
        )
        return True

    elif current_id in ["7257691"]:
        # Flowpath in VPU 10L whos outlet is not in a divide. Queueing the first flowpath that has a divide
        result.aggregation_pairs.append((current_id, "7258923"))
        result.aggregation_pairs.append(("7258923", "7257829"))
        result.aggregation_set.add(current_id)
        result.aggregation_set.add("7258923")
        result.aggregation_set.add("7257829")
        # Chain: 7257829 → 7258923 → current_id
        result.virtual_flowpath_pairs.append(("7258923", current_id))
        result.virtual_flowpath_pairs.append(("7257829", "7258923"))
        result.virtual_flowpath_pairs.append((current_id, ds_id))
        _queue_upstream(
            ["7257829"],
            to_process,
            result.processed_flowpaths,
            unprocessed_only=True,
        )
        return True

    elif current_id in ["19058436"]:
        # Flowpath in 10L that has messed up catchment delination
        _traverse_and_mark_as_non_nextgen("19058434", current_id, result, digraph, node_indices)

        result.aggregation_pairs.append((current_id, "19058438"))
        result.aggregation_set.add(current_id)
        result.aggregation_set.add("19058438")
        result.virtual_flowpath_pairs.append(("19058438", current_id))

        _traverse_and_mark_as_non_nextgen("19058412", current_id, result, digraph, node_indices)

        # Chain: 940180112 → 19058408 → 19058438
        result.aggregation_pairs.append(("19058438", "19058408"))
        result.aggregation_set.add("19058408")
        result.virtual_flowpath_pairs.append(("19058408", "19058438"))

        result.aggregation_pairs.append(("19058408", "940180112"))
        result.aggregation_set.add("940180112")
        result.virtual_flowpath_pairs.append(("940180112", "19058408"))

        _traverse_and_aggregate("19058240", result, digraph, node_indices, fp_lookup, div_ids)

        _traverse_and_aggregate("940180111", result, digraph, node_indices, fp_lookup, div_ids)
        result.virtual_flowpath_pairs.append((current_id, ds_id))
        return True

    elif current_id in ["21532894"]:
        # Flowpath in VPU 10U whos outlet is not in a divide and has an upstream that isn't connected. Returning true to skip this flowpath
        # 21534286: Flowpath in VPU 10U whos outlet is not in a divide. Queueing the first flowpath that has a divide
        result.aggregation_pairs.append((current_id, "21534286"))
        result.aggregation_set.add(current_id)
        result.aggregation_set.add("21534286")
        result.virtual_flowpath_pairs.append(("21534286", current_id))

        _traverse_and_mark_as_non_nextgen("21532928", current_id, result, digraph, node_indices)

        # Chain: 21533002 → 21532956 → 21532958 → 21534286
        result.aggregation_pairs.append(("21534286", "21532958"))
        result.aggregation_set.add("21532958")
        result.virtual_flowpath_pairs.append(("21532958", "21534286"))

        _traverse_and_mark_as_non_nextgen("21532950", current_id, result, digraph, node_indices)

        result.aggregation_pairs.append(("21532958", "21532956"))
        result.aggregation_set.add("21532956")
        result.virtual_flowpath_pairs.append(("21532956", "21532958"))

        _traverse_and_mark_as_non_nextgen("21532954", current_id, result, digraph, node_indices)

        result.aggregation_pairs.append(("21532956", "21533002"))
        result.aggregation_set.add("21533002")
        result.virtual_flowpath_pairs.append(("21533002", "21532956"))

        # Branch: 21534452 → 21532956
        result.aggregation_pairs.append(("21532956", "21534452"))
        result.aggregation_set.add("21534452")
        result.virtual_flowpath_pairs.append(("21534452", "21532956"))
        result.connector_segments.append("21534452")
        result.virtual_flowpath_pairs.append((current_id, ds_id))
        _queue_upstream(
            ["21534456", "21534462", "21534454"],
            to_process,
            result.processed_flowpaths,
            unprocessed_only=True,
        )
        return True

    elif current_id in ["12745197"]:
        # Flowpath in VPU 10U whos outlet is not in a divide and belongs to a null divide. Queueing the first flowpath that has a divide
        result.aggregation_pairs.append((current_id, "12744163"))
        result.aggregation_set.add(current_id)
        result.aggregation_set.add("12744163")
        result.virtual_flowpath_pairs.append(("12744163", current_id))

        result.aggregation_pairs.append((current_id, "12744931"))
        result.aggregation_set.add("12744931")
        result.virtual_flowpath_pairs.append(("12744931", current_id))

        result.aggregation_pairs.append((current_id, "12745039"))
        result.aggregation_set.add("12745039")
        result.virtual_flowpath_pairs.append(("12745039", current_id))
        result.virtual_flowpath_pairs.append((current_id, ds_id))
        _queue_upstream(
            ["12745041"],
            to_process,
            result.processed_flowpaths,
            unprocessed_only=True,
        )
        return True

    elif current_id in ["3254269"]:
        # 3254269: VPU 14 flowpath that has multiple no divides associated with a catchment
        result.aggregation_pairs.append((current_id, ds_id))
        result.aggregation_set.add(current_id)
        result.virtual_flowpath_pairs.append((current_id, ds_id))
        result.aggregation_set.add(ds_id)
        result.independent_flowpaths.discard(ds_id)

        # _traverse_and_aggregate("3254159", result, digraph, node_indices, fp_lookup, div_ids)

        result.aggregation_pairs.append(("3254167", "3258317"))
        result.aggregation_set.add("3254167")
        result.aggregation_set.add("3258317")
        result.virtual_flowpath_pairs.append(("3254167", "3258317"))

        _queue_upstream(
            ["3258317", "3254159"],
            to_process,
            result.processed_flowpaths,
            unprocessed_only=True,
        )
        return True

    elif current_id in ["7264077"]:
        # 7264077: 10L flowpath that is incorrectly delineated
        result.aggregation_pairs.append((current_id, "7259909"))
        result.aggregation_set.add(current_id)
        result.virtual_flowpath_pairs.append((current_id, "7259909"))
        _traverse_and_mark_as_non_nextgen("7259793", current_id, result, digraph, node_indices)
        _traverse_and_aggregate("7259795", result, digraph, node_indices, fp_lookup, div_ids)
        # Note: current_id already has virtual_flowpath_pair to "7259909", not ds_id
        return True

    elif current_id in ["17493533"]:
        # 10L flowpath that is incorrectly delineated
        result.virtual_flowpath_pairs.append((current_id, "17493589"))

        # Chain: 17493261 → 17493529 → current_id
        result.virtual_flowpath_pairs.append(("17493529", current_id))
        result.aggregation_pairs.append((current_id, "17493529"))
        result.aggregation_set.add(current_id)
        result.aggregation_set.add("17493529")

        _traverse_and_mark_as_non_nextgen("17493279", "17493533", result, digraph, node_indices)

        result.aggregation_pairs.append(("17493529", "17493261"))
        result.virtual_flowpath_pairs.append(("17493261", "17493529"))
        result.aggregation_set.add("17493261")

        _queue_upstream(
            ["17493321", "17493245"],
            to_process,
            result.processed_flowpaths,
            unprocessed_only=True,
        )
        return True

    elif current_id in ["12327133"]:
        # 10U flowpath that is incorrectly delineated
        _traverse_and_mark_as_non_nextgen("12327137", current_id, result, digraph, node_indices)
        result.aggregation_pairs.append((current_id, "12327125"))
        result.aggregation_set.add("12327125")
        result.aggregation_set.add(current_id)
        result.virtual_flowpath_pairs.append((current_id, ds_id))
        result.virtual_flowpath_pairs.append(("12327125", current_id))
        _queue_upstream(
            ["12327119"],
            to_process,
            result.processed_flowpaths,
            unprocessed_only=True,
        )
        return True

    elif current_id in ["3023064"]:
        # 10U flowpath that is incorrectly delineated
        result.virtual_flowpath_pairs.append((current_id, ds_id))
        _traverse_and_mark_as_non_nextgen("3023066", current_id, result, digraph, node_indices)

        # Chain: 3023012 → 3023062 → current_id
        result.virtual_flowpath_pairs.append(("3023062", current_id))
        result.aggregation_pairs.append((current_id, "3023062"))
        result.aggregation_set.add(current_id)
        result.aggregation_set.add("3023062")

        result.aggregation_pairs.append(("3023012", "3023062"))
        result.virtual_flowpath_pairs.append(("3023012", "3023062"))
        result.aggregation_set.add("3023012")

        # Two branches from 3023012
        result.aggregation_pairs.append(("3022994", "3022998"))
        result.aggregation_set.add("3022994")
        result.aggregation_set.add("3022998")
        result.virtual_flowpath_pairs.append(("3022994", "3023012"))
        result.virtual_flowpath_pairs.append(("3022998", "3023012"))

        _queue_upstream(
            ["3022996", "3022994"],
            to_process,
            result.processed_flowpaths,
            unprocessed_only=True,
        )
        return True

    elif current_id in ["5353277"]:
        # 10U flowpath that is incorrectly delineated
        _traverse_and_mark_as_non_nextgen("5353283", current_id, result, digraph, node_indices)
        result.aggregation_set.add("5353277")
        result.aggregation_pairs.append((current_id, "5353281"))
        result.aggregation_set.add("5353281")
        result.virtual_flowpath_pairs.append(("5353281", current_id))
        result.virtual_flowpath_pairs.append((current_id, ds_id))
        _queue_upstream(
            ["5352717"],
            to_process,
            result.processed_flowpaths,
            unprocessed_only=True,
        )
        return True

    elif current_id in [
        "17245948",
        "4386267",
        "8367918",
        "7195127",
        "12778333",
        "12615764",
        "12444133",
        "3053522",
        "18852694",
        "7312267",
    ]:
        # 17245948: very large catchments in 10L that have an incorrect delineations
        # 4386267: 10L a catchment with a farm upstream incorrectly delineated
        # 8367918: 10L catchment with a long mainstem flowpath with no divide
        # 7195127: 10U catchment with a long no-divide connector due to incorrect delineations
        # 12778333: 10U catchment with incorrect delineations
        # 12615764: 10U catchment with incorrect delineations
        # 12444133: 10U catchment with incorrect delineations
        # 3053522: 10U catchment with incorrect delineations
        # 18852694: 10U catchment with incorrect delineations
        # 7312267: 10L catchment with no-divides at the head of the catchment
        _traverse_and_aggregate(current_id, result, digraph, node_indices, fp_lookup, div_ids)
        return True

    elif current_id in [
        "8367540",
        "14625948",
    ]:
        # 8367540: 10L flowpath that is incorrectly delineated
        # 14625948: 10L flowpath that is incorrectly delineated
        _traverse_and_mark_as_non_nextgen(current_id, ds_id, result, digraph, node_indices)
        result.aggregation_set.add(current_id)
        return True

    else:
        return False


def _trace_stack(
    start_id: str,
    div_ids: set[str],
    cfg: HFConfig,
    partition_data: dict[str, Any],
) -> Classifications:
    """Trace upstream from a starting flowpath and classify segments according to aggregation rules.

    Rules are applied in order:
    1. No upstream → Independent headwater
    2. Order 1 without divide → No-divide connector (queue upstream)
    3. Order 2+ without divide → Enhanced to handle multiple upstream cases
       - Multiple upstreams with divides (connector):
         a. Mix of order 1 and higher-order → aggregate with higher-order, mark order 1 as virtual
       - Single upstream or no special connector case → check same-order chain logic
    4. Large area → Independent
    5. Nested no-divide connector → Aggregate based on drainage area
    6. Multiple upstream with 2+ higher-order → Connector
    7. Order 2 with all Order 1s → Subdivide candidate
    8. Mixed upstream orders (higher-order + order 1) → Aggregate with rules
    9. Single upstream → Aggregate based on order and area threshold

    Parameters
    ----------
    start_id : str
        The outlet flowpath ID
    div_ids : set[str]
        All IDs from the reference divides dataframe
    cfg : HFConfig
        The Hydrofabric config file
    partition_data : dict[str, Any]
        Contains subgraph, node_indices, and fp_lookup

    Returns
    -------
    Classifications
        A Pydantic BaseModel containing all of the flowpaths and their classifications

    Raises
    ------
    ValueError
        If one of the flowpaths doesn't pass a rule the workflow will be stopped
    """
    digraph: rx.PyDiGraph = partition_data["subgraph"]
    node_indices: dict[str, int] = partition_data["node_indices"]
    fp_lookup: dict[str, dict[str, Any]] = partition_data["fp_lookup"]

    result = Classifications()
    to_process: deque[str] = deque([start_id])
    updated_cumulative_areas: dict[str, float] = {}

    if not div_ids:
        # No valid flowpaths in outlet (none have divides)
        return result

    while to_process:
        current_id = to_process.popleft()

        if current_id in result.processed_flowpaths:
            continue

        fp_info: dict[str, Any] = fp_lookup[current_id]
        upstream_ids = _get_upstream_ids(current_id, digraph, node_indices)
        upstream_info = _get_unprocessed_upstream_info(upstream_ids, fp_lookup, result.processed_flowpaths)

        result.processed_flowpaths.add(current_id)
        if _fix_no_divide_anomalies(
            current_id, result, fp_lookup, digraph, node_indices, to_process, div_ids
        ):
            continue

        ds_id = str(int(fp_info["flowpath_toid"]))

        # Rule 1: No upstream (headwater)
        if not upstream_ids:
            if current_id in div_ids:
                if current_id in result.aggregation_set:
                    result.virtual_flowpath_pairs.append((current_id, ds_id))
                    continue
                result.independent_flowpaths.add(current_id)
                result.virtual_flowpath_pairs.append((current_id, ds_id))
            else:
                result.non_nextgen_flowpaths.add(current_id)
                result.non_nextgen_virtual_flowpath_pairs.append((current_id, ds_id))
            continue

        # Rule 2: Single upstream
        if len(upstream_ids) == 1:
            if current_id not in div_ids:
                # checking to see if any divides in flowpath
                if fp_info["streamorder"] == 1 or fp_info["streamorder"] == 2:
                    # if ds_id in result.connector_segments:
                    _traverse_and_aggregate(current_id, result, digraph, node_indices, fp_lookup, div_ids)
                    # else:
                    #     _traverse_and_mark_as_non_nextgen(current_id, ds_id, result, digraph, node_indices)
                    #     result.independent_flowpaths.discard(ds_id)
                    continue
                all_upstreams_lack_deep_divides = True
                has_divide_layer1 = any(upstream_id in div_ids for upstream_id in upstream_ids)
                if has_divide_layer1:
                    all_upstreams_lack_deep_divides = False
                for uid in upstream_ids:
                    layer2_ids = _get_upstream_ids(uid, digraph, node_indices)
                    has_divide_layer2 = any(l2_id in div_ids for l2_id in layer2_ids)
                    if has_divide_layer2:
                        all_upstreams_lack_deep_divides = False
                        break
                if all_upstreams_lack_deep_divides:
                    start_idx = node_indices[current_id]
                    ancestor_indices = rx.ancestors(digraph, start_idx)
                    ancestor_ids = {digraph[idx] for idx in ancestor_indices}
                    if ancestor_ids.isdisjoint(div_ids):
                        # No divides anywhere upstream - mark everything as virtual
                        if ds_id in result.connector_segments:
                            _traverse_and_aggregate(
                                current_id, result, digraph, node_indices, fp_lookup, div_ids
                            )
                        else:
                            _traverse_and_mark_as_non_nextgen(
                                current_id, ds_id, result, digraph, node_indices
                            )
                            result.independent_flowpaths.discard(ds_id)
                        continue

            upstream_id = upstream_ids[0]

            # Get cumulative area
            current_area = fp_info["areasqkm"]
            cumulative = updated_cumulative_areas.get(current_id, 0.0) + current_area

            # If the drainage area close to nothing, we have a BAD reference line. This should be aggregated downstream
            if current_area < 0.005:
                result.aggregation_pairs.append((current_id, upstream_id))
                result.aggregation_set.add(upstream_id)
                result.aggregation_set.add(current_id)
                if ds_id in fp_lookup:
                    updated_cumulative_areas[upstream_id] = fp_lookup[ds_id]["areasqkm"] + current_area
            # Check if we should aggregate
            elif cumulative < cfg.build.divide_aggregation_threshold:
                # Too small - aggregate
                result.aggregation_pairs.append((current_id, upstream_id))
                result.aggregation_set.add(current_id)
                result.aggregation_set.add(upstream_id)
                updated_cumulative_areas[upstream_id] = cumulative
            else:
                # Big enough - independent
                if current_id in div_ids:
                    if upstream_id not in div_ids:
                        result.aggregation_pairs.append((current_id, upstream_id))
                        result.aggregation_set.add(current_id)
                        result.aggregation_set.add(upstream_id)
                    else:
                        if current_id not in result.aggregation_set:
                            result.independent_flowpaths.add(current_id)
                # If no divide and big, still aggregate to avoid orphans
                else:
                    result.aggregation_pairs.append((current_id, upstream_id))
                    result.aggregation_set.add(current_id)
                    result.aggregation_set.add(upstream_id)

            result.virtual_flowpath_pairs.append((current_id, ds_id))
            _queue_upstream([upstream_id], to_process, result.processed_flowpaths, unprocessed_only=True)
            continue

        # Rule 3: Multiple upstream (connector case)
        if len(upstream_ids) > 1:
            # Case A: Current HAS divide - Connector logic based on stream order
            if current_id in div_ids:
                # Separate by stream order
                order_1_upstreams = [info for info in upstream_info if info["streamorder"] == 1]
                higher_order_upstreams = [info for info in upstream_info if info["streamorder"] > 1]

                if len(higher_order_upstreams) == 0:
                    best_upstream = max(
                        order_1_upstreams, key=lambda x: (x["streamorder"], x["areasqkm"], x["flowpath_id"])
                    )
                    current_area = fp_info["areasqkm"]
                    cumulative = updated_cumulative_areas.get(current_id, 0.0) + current_area
                    # If the drainage area is nothing, we have a BAD reference line. This should be aggregated downstream
                    if current_area < 0.005:
                        if ds_id == "0":
                            result.aggregation_pairs.append((current_id, best_upstream["flowpath_id"]))
                            result.aggregation_set.add(best_upstream["flowpath_id"])
                            result.independent_flowpaths.discard(best_upstream["flowpath_id"])
                        else:
                            if ds_id in result.connector_segments:
                                result.connector_segments.remove(ds_id)
                            result.aggregation_pairs.append((current_id, ds_id))
                            result.aggregation_set.add(ds_id)
                            result.independent_flowpaths.discard(ds_id)

                        result.aggregation_set.add(current_id)
                        result.virtual_flowpath_pairs.append((current_id, ds_id))
                        _queue_upstream(
                            upstream_ids,
                            to_process,
                            result.processed_flowpaths,
                            unprocessed_only=True,
                        )
                        continue
                    elif cumulative < cfg.build.divide_aggregation_threshold:
                        result.aggregation_pairs.append((current_id, best_upstream["flowpath_id"]))
                        result.aggregation_set.add(current_id)
                        result.aggregation_set.add(best_upstream["flowpath_id"])
                        updated_cumulative_areas[best_upstream["flowpath_id"]] = cumulative
                        for _info in order_1_upstreams:
                            if _info["flowpath_id"] != best_upstream["flowpath_id"]:
                                _traverse_and_mark_as_non_nextgen(
                                    _info["flowpath_id"], current_id, result, digraph, node_indices
                                )
                        result.virtual_flowpath_pairs.append((current_id, ds_id))
                        _queue_upstream(
                            [best_upstream["flowpath_id"]],
                            to_process,
                            result.processed_flowpaths,
                            unprocessed_only=True,
                        )
                        continue
                    else:
                        if current_id not in result.aggregation_set:
                            result.connector_segments.append(current_id)
                        result.virtual_flowpath_pairs.append((current_id, ds_id))
                        _queue_upstream(
                            upstream_ids,
                            to_process,
                            result.processed_flowpaths,
                            unprocessed_only=True,
                        )
                        continue

                else:
                    if len(upstream_ids) == 2:
                        # If 2+ higher-order streams meet, this is a connector. else we aggregate to the higher-order
                        if len(higher_order_upstreams) > 1:
                            if current_id not in result.aggregation_set:
                                result.connector_segments.append(current_id)
                            result.virtual_flowpath_pairs.append((current_id, ds_id))
                            _queue_upstream(
                                upstream_ids, to_process, result.processed_flowpaths, unprocessed_only=True
                            )
                            continue
                        else:  # one higher order, one order 1 stream. 2 total upstream
                            higher_order_ids = [info["flowpath_id"] for info in higher_order_upstreams]
                            higher_order_id = higher_order_upstreams[0]["flowpath_id"]
                            order_1_id = order_1_upstreams[0]["flowpath_id"]
                            current_area = fp_info["areasqkm"]
                            cumulative = updated_cumulative_areas.get(current_id, 0.0) + current_area
                            # If the drainage area is nothing, we have a BAD reference line. This should be aggregated downstream
                            if current_area < 0.005:
                                if ds_id == "0":
                                    result.aggregation_pairs.append((current_id, higher_order_id))
                                    result.aggregation_set.add(higher_order_id)
                                    result.independent_flowpaths.discard(higher_order_id)
                                    result.aggregation_set.add(current_id)
                                    result.virtual_flowpath_pairs.append((current_id, higher_order_id))
                                    _traverse_and_mark_as_non_nextgen(
                                        order_1_id, current_id, result, digraph, node_indices
                                    )
                                    _queue_upstream(
                                        [higher_order_id],
                                        to_process,
                                        result.processed_flowpaths,
                                        unprocessed_only=True,
                                    )
                                    continue
                                else:
                                    if ds_id in result.connector_segments:
                                        result.connector_segments.remove(ds_id)
                                    result.aggregation_pairs.append((current_id, ds_id))
                                    result.aggregation_set.add(ds_id)
                                    result.independent_flowpaths.discard(ds_id)
                                    result.aggregation_set.add(current_id)
                                    result.virtual_flowpath_pairs.append((current_id, ds_id))
                                    _queue_upstream(
                                        upstream_ids,
                                        to_process,
                                        result.processed_flowpaths,
                                        unprocessed_only=True,
                                    )
                                continue
                            elif cumulative < cfg.build.divide_aggregation_threshold:
                                result.aggregation_pairs.append(
                                    (current_id, higher_order_upstreams[0]["flowpath_id"])
                                )
                                result.aggregation_set.add(current_id)
                                result.aggregation_set.add(higher_order_upstreams[0]["flowpath_id"])
                                updated_cumulative_areas[higher_order_upstreams[0]["flowpath_id"]] = (
                                    cumulative
                                )
                                # Mark all order 1 upstreams as virtual
                                for order_1 in order_1_upstreams:
                                    upstream_id = order_1["flowpath_id"]
                                    _traverse_and_mark_as_non_nextgen(
                                        upstream_id, current_id, result, digraph, node_indices
                                    )
                                result.virtual_flowpath_pairs.append((current_id, ds_id))
                                _queue_upstream(
                                    higher_order_ids,
                                    to_process,
                                    result.processed_flowpaths,
                                    unprocessed_only=True,
                                )
                                continue
                            else:
                                if current_id not in result.aggregation_set:
                                    result.connector_segments.append(current_id)
                                result.virtual_flowpath_pairs.append((current_id, ds_id))
                                _queue_upstream(
                                    upstream_ids,
                                    to_process,
                                    result.processed_flowpaths,
                                    unprocessed_only=True,
                                )
                                continue
                    else:
                        # If the drainage area is nothing, we have a BAD reference line. This should be aggregated downstream
                        current_area = fp_info["areasqkm"]
                        if current_area < 0.005:
                            if ds_id in result.connector_segments:
                                result.connector_segments.remove(ds_id)
                            result.aggregation_pairs.append((current_id, ds_id))
                            result.aggregation_set.add(current_id)
                            result.aggregation_set.add(ds_id)
                            result.independent_flowpaths.discard(ds_id)
                            result.virtual_flowpath_pairs.append((current_id, ds_id))
                            _queue_upstream(
                                upstream_ids,
                                to_process,
                                result.processed_flowpaths,
                                unprocessed_only=True,
                            )
                            continue

                        # 3+ upstream IDs. Mark as connector
                        if current_id not in result.aggregation_set:
                            result.connector_segments.append(current_id)
                        result.virtual_flowpath_pairs.append((current_id, ds_id))
                        _queue_upstream(
                            upstream_ids, to_process, result.processed_flowpaths, unprocessed_only=True
                        )
                        continue

            # Case B: Current LACKS divide - Rule 3 logic
            else:
                # Get downstream to see if we should aggregate downstream or to best upstream
                if fp_info["streamorder"] == 1:
                    if ds_id in result.connector_segments:
                        _traverse_and_aggregate(current_id, result, digraph, node_indices, fp_lookup, div_ids)
                    else:
                        _traverse_and_mark_as_non_nextgen(current_id, ds_id, result, digraph, node_indices)
                        result.independent_flowpaths.discard(ds_id)
                    continue
                # If a coastal outlet, we're aggregating to the higher order
                if ds_id == "0":
                    best_upstream = max(
                        upstream_info, key=lambda x: (x["streamorder"], x["areasqkm"], x["flowpath_id"])
                    )
                    result.aggregation_pairs.append((current_id, best_upstream["flowpath_id"]))
                    result.aggregation_set.add(best_upstream["flowpath_id"])
                    result.aggregation_set.add(current_id)
                    upstream_info.remove(best_upstream)
                    for _stream in upstream_info:
                        _traverse_and_mark_as_non_nextgen(
                            _stream["flowpath_id"], current_id, result, digraph, node_indices
                        )
                    result.virtual_flowpath_pairs.append((current_id, ds_id))
                    _queue_upstream(
                        [best_upstream["flowpath_id"]],
                        to_process,
                        result.processed_flowpaths,
                        unprocessed_only=True,
                    )
                    continue

                # Step 1: Check if we can aggregate downstream
                lateral_ids = _get_upstream_ids(ds_id, digraph, node_indices)
                other_laterals = [lid for lid in lateral_ids if lid != current_id]
                if len(other_laterals) == 0:
                    if ds_id in result.connector_segments:
                        result.connector_segments.remove(ds_id)
                    result.aggregation_pairs.append((current_id, ds_id))
                    result.aggregation_set.add(current_id)
                    result.aggregation_set.add(ds_id)
                    result.independent_flowpaths.discard(ds_id)
                    # Check: do the upstream segments have divides
                    # Case A: No divides for flowpaths upstream. Need to only use one and make the other a virtual
                    if all(uid not in div_ids for uid in upstream_ids):
                        start_idx = node_indices[current_id]
                        ancestor_indices = rx.ancestors(digraph, start_idx)
                        ancestor_ids = {digraph[idx] for idx in ancestor_indices}
                        if ancestor_ids.isdisjoint(div_ids):
                            # No divides anywhere upstream - aggregate downstream and make a longest path segment
                            _traverse_and_aggregate(
                                current_id, result, digraph, node_indices, fp_lookup, div_ids
                            )
                            result.independent_flowpaths.discard(ds_id)
                            continue
                        else:
                            result.independent_flowpaths.discard(ds_id)
                            best_upstream = max(
                                upstream_info,
                                key=lambda x: (x["streamorder"], x["areasqkm"], x["flowpath_id"]),
                            )
                            result.aggregation_pairs.append((current_id, best_upstream["flowpath_id"]))
                            result.aggregation_set.add(current_id)
                            result.aggregation_set.add(best_upstream["flowpath_id"])
                            _uids = upstream_ids.copy()
                            _uids.remove(best_upstream["flowpath_id"])
                            for _uid in _uids:
                                result.force_queue_flowpaths.add(_uid)
                                _traverse_and_mark_as_non_nextgen(
                                    _uid, current_id, result, digraph, node_indices
                                )
                            result.virtual_flowpath_pairs.append((current_id, ds_id))
                            _queue_upstream(
                                [best_upstream["flowpath_id"]],
                                to_process,
                                result.processed_flowpaths,
                                unprocessed_only=True,
                            )
                            continue

                    # Case B: if one of the flowpaths has no-divide
                    # Find the best upstream, with different actions depending on if there is a divide for it
                    elif any(uid not in div_ids for uid in upstream_ids):
                        best_upstream = max(
                            upstream_info, key=lambda x: (x["streamorder"], x["areasqkm"], x["flowpath_id"])
                        )
                        if best_upstream["flowpath_id"] not in div_ids:
                            result.aggregation_pairs.append((current_id, best_upstream["flowpath_id"]))
                            result.aggregation_set.add(current_id)
                            result.aggregation_set.add(best_upstream["flowpath_id"])
                            _uids = upstream_ids.copy()
                            _uids.remove(best_upstream["flowpath_id"])
                            for _uid in _uids:
                                result.force_queue_flowpaths.add(_uid)
                                _traverse_and_mark_as_non_nextgen(
                                    _uid, current_id, result, digraph, node_indices
                                )
                            result.virtual_flowpath_pairs.append((current_id, ds_id))
                            _queue_upstream(
                                [best_upstream["flowpath_id"]],
                                to_process,
                                result.processed_flowpaths,
                                unprocessed_only=True,
                            )
                            continue
                        else:
                            if current_id not in result.aggregation_set:
                                result.connector_segments.append(current_id)
                            result.virtual_flowpath_pairs.append((current_id, ds_id))
                            _queue_upstream(
                                upstream_ids, to_process, result.processed_flowpaths, unprocessed_only=True
                            )
                            continue

                    # Case C: all upstreams have divs. This is a connector
                    else:
                        if current_id not in result.aggregation_set:
                            result.connector_segments.append(current_id)

                        for up_info in upstream_info:
                            if up_info["streamorder"] == 1:
                                _traverse_and_mark_as_non_nextgen(
                                    up_info["flowpath_id"], current_id, result, digraph, node_indices
                                )
                        # Queue non-order-1 upstreams
                        higher_order_ids = [
                            info["flowpath_id"] for info in upstream_info if info["streamorder"] > 1
                        ]
                        result.virtual_flowpath_pairs.append((current_id, ds_id))
                        _queue_upstream(
                            higher_order_ids, to_process, result.processed_flowpaths, unprocessed_only=True
                        )
                        continue

                # Step 2: Check if both upstreams have no divides in many layers
                all_upstreams_lack_deep_divides = True
                has_divide_layer1 = any(l1_id in div_ids for l1_id in upstream_ids)
                if has_divide_layer1:
                    all_upstreams_lack_deep_divides = False
                for uid in upstream_ids:
                    layer2_ids = _get_upstream_ids(uid, digraph, node_indices)
                    has_divide_layer2 = any(l2_id in div_ids for l2_id in layer2_ids)
                    if has_divide_layer2:
                        all_upstreams_lack_deep_divides = False
                        break
                if all_upstreams_lack_deep_divides:
                    if ds_id in result.connector_segments:
                        _traverse_and_aggregate(current_id, result, digraph, node_indices, fp_lookup, div_ids)
                    else:
                        _traverse_and_mark_as_non_nextgen(current_id, ds_id, result, digraph, node_indices)
                        result.independent_flowpaths.discard(ds_id)
                    continue

                # Step 3: Check if there are order 1s or 2s that can be made virtual
                order_1_2_upstreams = [info for info in upstream_info if info["streamorder"] <= 2]
                higher_order_upstreams = [info for info in upstream_info if info["streamorder"] > 2]
                if len(order_1_2_upstreams) > 0:
                    # Aggregate to best upstream
                    best_upstream = max(
                        upstream_info, key=lambda x: (x["streamorder"], x["areasqkm"], x["flowpath_id"])
                    )
                    result.aggregation_pairs.append((current_id, best_upstream["flowpath_id"]))
                    result.aggregation_set.add(current_id)
                    result.aggregation_set.add(best_upstream["flowpath_id"])
                    if len(higher_order_upstreams) > 0:
                        for up_info in order_1_2_upstreams:
                            result.force_queue_flowpaths.add(up_info["flowpath_id"])
                            _traverse_and_mark_as_non_nextgen(
                                up_info["flowpath_id"], current_id, result, digraph, node_indices
                            )
                        # Queue non-virtual upstreams
                        non_virtual_ids = [
                            info["flowpath_id"] for info in upstream_info if info["streamorder"] > 2
                        ]
                        result.virtual_flowpath_pairs.append((current_id, ds_id))
                        _queue_upstream(
                            non_virtual_ids, to_process, result.processed_flowpaths, unprocessed_only=True
                        )
                        continue
                    else:
                        # no higher order upstreams
                        best_upstream = max(
                            order_1_2_upstreams,
                            key=lambda x: (x["streamorder"], x["areasqkm"], x["flowpath_id"]),
                        )
                        result.aggregation_pairs.append((current_id, best_upstream["flowpath_id"]))
                        result.aggregation_set.add(current_id)
                        result.aggregation_set.add(best_upstream["flowpath_id"])
                        order_1_2_upstreams.remove(best_upstream)
                        for up_info in order_1_2_upstreams:
                            result.force_queue_flowpaths.add(up_info["flowpath_id"])
                            _traverse_and_mark_as_non_nextgen(
                                up_info["flowpath_id"], current_id, result, digraph, node_indices
                            )
                        result.virtual_flowpath_pairs.append((current_id, ds_id))
                        _queue_upstream(
                            [best_upstream["flowpath_id"]],
                            to_process,
                            result.processed_flowpaths,
                            unprocessed_only=True,
                        )
                        continue
                else:
                    # This is an awkward connector. Two divides upstream, two downstream, no divide in the flowpath, all upstream are high order
                    if ds_id in result.connector_segments:
                        result.connector_segments.remove(ds_id)
                    result.aggregation_pairs.append((current_id, ds_id))
                    result.aggregation_set.add(current_id)
                    result.aggregation_set.add(ds_id)
                    result.independent_flowpaths.discard(ds_id)
                    result.virtual_flowpath_pairs.append((current_id, ds_id))
                    # result.connector_segments.append(ds_id)
                    _queue_upstream(
                        upstream_ids, to_process, result.processed_flowpaths, unprocessed_only=True
                    )
                    continue

        raise ValueError(f"No Rule Matched. Please debug flowpath_id: {current_id}")

    return result


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

    Returns
    -------
    pl.DataFrame
        Updated flowpaths with total_da_sqkm, mainstem_lp, path_length, dn_hydroseq, hydroseq, and streamorder columns
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
            "total_da_sqkm": 0.0,
            "mainstem_lp": None,
            "path_length": 0.0,
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

    # PASS 1: Traverse from OUTLET to ANCESTORS (reverse topo order)
    current_mainstem_id = id_offset
    current_hydroseq = hydroseq_offset

    # Initialize outlet
    basin_graph[outlet_idx]["path_length"] = 0.0
    basin_graph[outlet_idx]["dn_hydroseq"] = 0
    basin_graph[outlet_idx]["hydroseq"] = current_hydroseq
    current_hydroseq += 1

    # Calculate path lengths and hydroseq (reverse topo order)
    for node_idx in reversed(topo_order):
        if node_idx == outlet_idx:
            continue

        # Assign hydroseq (increases going upstream)
        basin_graph[node_idx]["hydroseq"] = current_hydroseq
        current_hydroseq += 1

        out_edges = basin_graph.out_edges(node_idx)

        if out_edges:
            downstream_nodes = [tgt_idx for _, tgt_idx, _ in out_edges]

            if downstream_nodes:
                downstream_idx = max(downstream_nodes, key=lambda idx: basin_graph[idx]["path_length"])
                basin_graph[node_idx]["path_length"] = (
                    basin_graph[downstream_idx]["path_length"] + basin_graph[downstream_idx]["length_km"]
                )

    # Trace main mainstem (longest path from outlet to headwater)
    current_idx = outlet_idx
    mainstem_nodes = []

    while True:
        mainstem_nodes.append(current_idx)
        basin_graph[current_idx]["mainstem_lp"] = current_mainstem_id

        in_edges = list(basin_graph.in_edges(current_idx))

        if not in_edges:
            break

        upstream_candidates = [src_idx for src_idx, _, _ in in_edges]

        if not upstream_candidates:
            break

        upstream_idx = max(
            upstream_candidates,
            key=lambda idx: (basin_graph[idx]["path_length"], basin_graph[idx]["total_da_sqkm"]),
        )
        current_idx = upstream_idx

    # Assign tributary mainstems
    tributary_offset = current_mainstem_id + 1
    processed = set(mainstem_nodes)

    for node_idx in basin_graph.node_indices():
        if node_idx not in processed:
            tributary_id = tributary_offset
            tributary_offset += 1

            trib_current = node_idx
            while trib_current not in processed:
                basin_graph[trib_current]["mainstem_lp"] = tributary_id
                processed.add(trib_current)

                in_edges = list(basin_graph.in_edges(trib_current))
                upstream_in_basin = [src_idx for src_idx, _, _ in in_edges]

                if not upstream_in_basin:
                    break

                trib_current = max(
                    upstream_in_basin,
                    key=lambda idx: (basin_graph[idx]["path_length"], basin_graph[idx]["total_da_sqkm"]),
                )

    # Assign dn_hydroseq based on graph edges (now that hydroseq is calculated)
    for node_idx in basin_graph.node_indices():
        if node_idx == outlet_idx:
            continue

        out_edges = basin_graph.out_edges(node_idx)
        downstream_nodes = [tgt_idx for _, tgt_idx, _ in out_edges]

        if downstream_nodes:
            downstream_idx = downstream_nodes[0]
            basin_graph[node_idx]["dn_hydroseq"] = basin_graph[downstream_idx]["hydroseq"]
        else:
            basin_graph[node_idx]["dn_hydroseq"] = 0

    # PASS 2: Traverse from ancestors to OUTLET (forward topo order)
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
        }
    )

    return traced_df, tributary_offset + 1, current_hydroseq
