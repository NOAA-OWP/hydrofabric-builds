"""Test cases to ensure functionality works for the trace functions"""

from typing import Any

import pandas as pd
import polars as pl
import rustworkx as rx
from pyprojroot import here

from hydrofabric_builds import (
    HFConfig,
    build_graph,
    download_reference_data,
    map_build_hydrofabric,
    map_trace_and_aggregate,
    reduce_combine_base_hydrofabric,
    trace_hydrofabric_attributes,
)
from hydrofabric_builds.hydrofabric.aggregate import _aggregate_geometries
from hydrofabric_builds.hydrofabric.graph import (
    _build_rustworkx_object,
    _build_upstream_dict_from_nexus,
)
from hydrofabric_builds.hydrofabric.trace import _trace_stack
from scripts.hf_runner import LocalRunner


def _check_hydroseq_decreases_downstream(
    fp_pl: pl.DataFrame,
    graph: rx.PyDiGraph,
    fp_id_col: str = "fp_id",
) -> None:
    """Check that hydroseq always decreases from upstream to downstream.

    Uses the graph structure to check all upstream-downstream relationships.

    Parameters
    ----------
    fp_pl : pl.DataFrame
        Flowpath dataframe with hydroseq
    graph : rx.PyDiGraph
        The network graph where nodes are flowpath IDs
    fp_id_col : str
        Name of flowpath ID column
    """
    fp_to_hydroseq = dict(fp_pl.select([fp_id_col, "hydroseq"]).iter_rows())

    violations = []

    # Check every node in the graph
    for node_idx in graph.node_indices():
        node_id = graph[node_idx]
        node_hydroseq = fp_to_hydroseq.get(node_id)

        if node_hydroseq is None:
            continue

        # Get all upstream nodes (predecessors)
        upstream_indices = graph.predecessor_indices(node_idx)

        for upstream_idx in upstream_indices:
            upstream_id = graph[upstream_idx]
            upstream_hydroseq = fp_to_hydroseq.get(upstream_id)

            if upstream_hydroseq is None:
                continue

            # Upstream hydroseq should be GREATER than downstream hydroseq
            if upstream_hydroseq <= node_hydroseq:
                violations.append(
                    {
                        "upstream_id": upstream_id,
                        "upstream_hydroseq": upstream_hydroseq,
                        "downstream_id": node_id,
                        "downstream_hydroseq": node_hydroseq,
                    }
                )

    if violations:
        raise AssertionError(f"Found {len(violations)} flowpaths where hydroseq does not decrease downstream")


def _check_area_percentages_sum_to_one(
    virtual_fp_pl: pl.DataFrame,
    reference_fp_pl: pl.DataFrame,
) -> None:
    """Check that area percentages sum to 1.0 within each divide.

    Parameters
    ----------
    virtual_fp_pl : pl.DataFrame
        Virtual flowpath dataframe with percentage_area_contribution
    reference_fp_pl : pl.DataFrame
        Reference flowpath mapping table with div_id
    """

    # Join virtual flowpaths with reference table to get div_id
    virtual_with_div = virtual_fp_pl.join(
        reference_fp_pl.select(["virtual_fp_id", "div_id"]).filter(pl.col("virtual_fp_id").is_not_null()),
        on="virtual_fp_id",
        how="left",
    )

    # Sum percentages by divide
    div_percentage_sums = (
        virtual_with_div.filter(pl.col("div_id").is_not_null())
        .group_by("div_id")
        .agg(
            [
                pl.col("percentage_area_contribution").sum().alias("total_percentage"),
                pl.col("virtual_fp_id").count().alias("num_virtual_fps"),
            ]
        )
        .sort("div_id")
    )

    # Check for divides where sum is not ~1.0 (allow small floating point error)
    tolerance = 1e-6
    violations = div_percentage_sums.filter(
        (pl.col("total_percentage") < 1.0 - tolerance) | (pl.col("total_percentage") > 1.0 + tolerance)
    )

    if len(violations) > 0:
        raise AssertionError(
            f"Found {len(violations)} divides where virtual flowpath percentages don't sum to 1.0"
        )


def test_no_divide_fp_upstream_most_reach(trace_case_upstream_no_divide_config: HFConfig) -> None:
    """Testing the tracing output for when there is a no-divide connector at the upstream-most point of a divide"""
    runner = LocalRunner(trace_case_upstream_no_divide_config)
    runner.run_task("download", download_reference_data)
    runner.run_task("build_graph", build_graph)

    outlets: list[str] = runner.ti.xcom_pull(task_id="build_graph", key="outlets")
    outlet_subgraphs: dict[str, dict[str, Any]] = runner.ti.xcom_pull(
        task_id="build_graph", key="outlet_subgraphs"
    )
    outlet = outlets[0]
    partition_data = outlet_subgraphs[outlet]
    filtered_divides = partition_data["divides"]
    valid_divide_ids: set[str] = set(filtered_divides["divide_id"].to_list())
    classifications = _trace_stack(
        start_id=outlet,
        div_ids=valid_divide_ids,
        cfg=trace_case_upstream_no_divide_config,
        partition_data=partition_data,
    )

    aggregate_data = _aggregate_geometries(
        classifications=classifications,
        partition_data=partition_data,
    )

    assert len(aggregate_data.aggregates) == 15, "Incorrect number of aggregates"
    assert len(aggregate_data.independents) == 0, "Incorrect number of independents"
    assert len(aggregate_data.connectors) == 4, "Incorrect number of connectors"
    assert len(aggregate_data.non_nextgen_flowpaths) == 26, "Incorrect number of non nextgen flowpaths"
    assert len(aggregate_data.non_nextgen_virtual_flowpaths) == 8, (
        "Incorrect number of non nextgen virtual flowpaths"
    )
    assert len(aggregate_data.virtual_flowpaths) == 50, "Incorrect number of virtual flowpaths"

    runner.run_task(task_id="map_flowpaths", python_callable=map_trace_and_aggregate, op_kwargs={})

    runner.run_task(task_id="map_build_base", python_callable=map_build_hydrofabric, op_kwargs={})
    runner.run_task(task_id="reduce_base", python_callable=reduce_combine_base_hydrofabric, op_kwargs={})
    runner.run_task(task_id="trace_attributes", python_callable=trace_hydrofabric_attributes, op_kwargs={})
    final_flowpaths = runner.ti.xcom_pull(task_id="trace_attributes", key="flowpaths_with_attributes")
    final_nexus = runner.ti.xcom_pull(task_id="reduce_base", key="nexus")
    final_virtual_flowpaths = runner.ti.xcom_pull(task_id="reduce_base", key="virtual_flowpaths")
    final_virtual_nexus = runner.ti.xcom_pull(task_id="reduce_base", key="virtual_nexus")
    final_reference_flowpaths = runner.ti.xcom_pull(task_id="reduce_base", key="reference_flowpaths")
    reference_fp_pl = pl.from_pandas(final_reference_flowpaths)

    fp_pl = pl.from_pandas(final_flowpaths.to_wkb())
    upstream_dict = _build_upstream_dict_from_nexus(fp_pl)
    graph, _ = _build_rustworkx_object(upstream_dict)
    assert rx.is_directed_acyclic_graph(graph), "Graph must be acyclic"
    assert rx.is_weakly_connected(graph), "All flowpaths should connect to single outlet"
    assert not rx.is_strongly_connected(graph), "DAG cannot be strongly connected"
    graph_outlets: list[int] = [node for node in graph.node_indices() if graph.out_degree(node) == 0]
    assert len(graph_outlets) == 1, f"Should have exactly 1 outlet, found {len(graph_outlets)}"
    strong_components = rx.strongly_connected_components(graph)
    assert len(strong_components) == graph.num_nodes(), "Each node should be its own SCC in a DAG"

    virtual_fp_pl = pl.from_pandas(final_virtual_flowpaths.to_wkb())
    virtual_upstream_dict = _build_upstream_dict_from_nexus(
        virtual_fp_pl, edge_id="virtual_fp_id", node_id="virtual_nex_id"
    )
    virtual_graph, _ = _build_rustworkx_object(virtual_upstream_dict)
    assert rx.is_directed_acyclic_graph(virtual_graph), "Virtual graph must be acyclic"
    assert rx.is_weakly_connected(virtual_graph), "All virtual flowpaths should connect to single outlet"
    assert not rx.is_strongly_connected(virtual_graph), "Virtual DAG cannot be strongly connected"
    virtual_outlets = [node for node in virtual_graph.node_indices() if virtual_graph.out_degree(node) == 0]
    assert len(virtual_outlets) == 1, f"Should have exactly 1 virtual outlet, found {len(virtual_outlets)}"
    virtual_strong_components = rx.strongly_connected_components(virtual_graph)
    assert len(virtual_strong_components) == virtual_graph.num_nodes(), (
        "Each virtual node should be its own SCC"
    )

    expected_df = pd.read_csv(
        here() / "tests/data/trace_cases/no_divide_fp_upstream_most_reach_nexus.csv",
        dtype={"nex_id": "int64", "dn_fp_id": "Int64", "vpu_id": "object"},
    )
    pd.testing.assert_frame_equal(final_nexus.drop(columns=["geometry"]), expected_df)

    expected_df = pd.read_csv(
        here() / "tests/data/trace_cases/no_divide_fp_upstream_most_reach_virtual_nexus.csv",
        dtype={"virtual_nex_id": "Int64", "dn_virtual_fp_id": "Int64", "vpu_id": "object"},
    )
    pd.testing.assert_frame_equal(final_virtual_nexus.drop(columns=["geometry"]), expected_df)

    _check_hydroseq_decreases_downstream(fp_pl, graph, fp_id_col="fp_id")
    _check_area_percentages_sum_to_one(virtual_fp_pl, reference_fp_pl)


def test_no_divide_coastal_outlet(trace_case_no_divide_coastal_outlet: HFConfig) -> None:
    """Testing the tracing output for when there is a no-divide connector at the upstream-most point of a divide"""
    runner = LocalRunner(trace_case_no_divide_coastal_outlet)
    runner.run_task("download", download_reference_data)
    runner.run_task("build_graph", build_graph)

    outlets: list[str] = runner.ti.xcom_pull(task_id="build_graph", key="outlets")
    outlet_subgraphs: dict[str, dict[str, Any]] = runner.ti.xcom_pull(
        task_id="build_graph", key="outlet_subgraphs"
    )
    outlet = outlets[0]
    partition_data = outlet_subgraphs[outlet]
    filtered_divides = partition_data["divides"]
    valid_divide_ids: set[str] = set(filtered_divides["divide_id"].to_list())
    classifications = _trace_stack(
        start_id=outlet,
        div_ids=valid_divide_ids,
        cfg=trace_case_no_divide_coastal_outlet,
        partition_data=partition_data,
    )

    aggregate_data = _aggregate_geometries(
        classifications=classifications,
        partition_data=partition_data,
    )

    assert len(aggregate_data.aggregates) == 2, "Incorrect number of aggregates"
    assert len(aggregate_data.independents) == 0, "Incorrect number of independents"
    assert len(aggregate_data.connectors) == 0, "Incorrect number of connectors"
    assert len(aggregate_data.non_nextgen_flowpaths) == 7, "Incorrect number of non nextgen flowpaths"
    assert len(aggregate_data.non_nextgen_virtual_flowpaths) == 3, (
        "Incorrect number of non nextgen virtual flowpaths"
    )
    assert len(aggregate_data.virtual_flowpaths) == 11, "Incorrect number of virtual flowpaths"

    runner.run_task(task_id="map_flowpaths", python_callable=map_trace_and_aggregate, op_kwargs={})

    runner.run_task(task_id="map_build_base", python_callable=map_build_hydrofabric, op_kwargs={})
    runner.run_task(task_id="reduce_base", python_callable=reduce_combine_base_hydrofabric, op_kwargs={})
    runner.run_task(task_id="trace_attributes", python_callable=trace_hydrofabric_attributes, op_kwargs={})
    final_flowpaths = runner.ti.xcom_pull(task_id="trace_attributes", key="flowpaths_with_attributes")
    final_nexus = runner.ti.xcom_pull(task_id="reduce_base", key="nexus")
    final_virtual_flowpaths = runner.ti.xcom_pull(task_id="reduce_base", key="virtual_flowpaths")
    final_virtual_nexus = runner.ti.xcom_pull(task_id="reduce_base", key="virtual_nexus")
    final_reference_flowpaths = runner.ti.xcom_pull(task_id="reduce_base", key="reference_flowpaths")
    reference_fp_pl = pl.from_pandas(final_reference_flowpaths)

    fp_pl = pl.from_pandas(final_flowpaths.to_wkb())
    upstream_dict = _build_upstream_dict_from_nexus(fp_pl)
    graph, _ = _build_rustworkx_object(upstream_dict)
    assert rx.is_directed_acyclic_graph(graph), "Graph must be acyclic"
    assert rx.is_weakly_connected(graph), "All flowpaths should connect to single outlet"
    assert not rx.is_strongly_connected(graph), "DAG cannot be strongly connected"
    graph_outlets: list[int] = [node for node in graph.node_indices() if graph.out_degree(node) == 0]
    assert len(graph_outlets) == 1, f"Should have exactly 1 outlet, found {len(graph_outlets)}"
    strong_components = rx.strongly_connected_components(graph)
    assert len(strong_components) == graph.num_nodes(), "Each node should be its own SCC in a DAG"

    virtual_fp_pl = pl.from_pandas(final_virtual_flowpaths.to_wkb())
    virtual_upstream_dict = _build_upstream_dict_from_nexus(
        virtual_fp_pl, edge_id="virtual_fp_id", node_id="virtual_nex_id"
    )
    virtual_graph, _ = _build_rustworkx_object(virtual_upstream_dict)
    assert rx.is_directed_acyclic_graph(virtual_graph), "Virtual graph must be acyclic"
    assert rx.is_weakly_connected(virtual_graph), "All virtual flowpaths should connect to single outlet"
    assert not rx.is_strongly_connected(virtual_graph), "Virtual DAG cannot be strongly connected"
    virtual_outlets = [node for node in virtual_graph.node_indices() if virtual_graph.out_degree(node) == 0]
    assert len(virtual_outlets) == 1, f"Should have exactly 1 virtual outlet, found {len(virtual_outlets)}"
    virtual_strong_components = rx.strongly_connected_components(virtual_graph)
    assert len(virtual_strong_components) == virtual_graph.num_nodes(), (
        "Each virtual node should be its own SCC"
    )

    df = pd.DataFrame(
        {
            "nex_id": pd.Series([1, 2], dtype="int64"),
            "dn_fp_id": pd.Series([pd.NA, 1], dtype="Int64"),
            "vpu_id": pd.Series(["02", "02"], dtype="object"),
        }
    )
    pd.testing.assert_frame_equal(final_nexus.drop(columns=["geometry"]), df)

    df = pd.DataFrame(
        {
            "virtual_nex_id": pd.Series([3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], dtype="Int64"),
            "dn_virtual_fp_id": pd.Series([pd.NA, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], dtype="Int64"),
            "vpu_id": pd.Series(
                ["02", "02", "02", "02", "02", "02", "02", "02", "02", "02", "02"], dtype="object"
            ),
        }
    )
    pd.testing.assert_frame_equal(final_virtual_nexus.drop(columns=["geometry"]), df)

    _check_hydroseq_decreases_downstream(fp_pl, graph, fp_id_col="fp_id")
    _check_area_percentages_sum_to_one(virtual_fp_pl, reference_fp_pl)


def test_connector_no_divide_upstream(trace_case_bad_connector_no_divide_config: HFConfig) -> None:
    """Testing the tracing output for when there is a no-divide connector at the upstream-most point of a divide"""
    runner = LocalRunner(trace_case_bad_connector_no_divide_config)
    runner.run_task("download", download_reference_data)
    runner.run_task("build_graph", build_graph)

    outlets: list[str] = runner.ti.xcom_pull(task_id="build_graph", key="outlets")
    outlet_subgraphs: dict[str, dict[str, Any]] = runner.ti.xcom_pull(
        task_id="build_graph", key="outlet_subgraphs"
    )
    outlet = outlets[0]
    partition_data = outlet_subgraphs[outlet]
    filtered_divides = partition_data["divides"]
    valid_divide_ids: set[str] = set(filtered_divides["divide_id"].to_list())
    classifications = _trace_stack(
        start_id=outlet,
        div_ids=valid_divide_ids,
        cfg=trace_case_bad_connector_no_divide_config,
        partition_data=partition_data,
    )

    aggregate_data = _aggregate_geometries(
        classifications=classifications,
        partition_data=partition_data,
    )

    assert len(aggregate_data.aggregates) == 2, "Incorrect number of aggregates"
    assert len(aggregate_data.independents) == 0, "Incorrect number of independents"
    assert len(aggregate_data.connectors) == 1, "Incorrect number of connectors"
    assert len(aggregate_data.non_nextgen_flowpaths) == 7, "Incorrect number of non nextgen flowpaths"
    assert len(aggregate_data.non_nextgen_virtual_flowpaths) == 3, (
        "Incorrect number of non nextgen virtual flowpaths"
    )
    assert len(aggregate_data.virtual_flowpaths) == 6, "Incorrect number of virtual flowpaths"

    runner.run_task(task_id="map_flowpaths", python_callable=map_trace_and_aggregate, op_kwargs={})

    runner.run_task(task_id="map_build_base", python_callable=map_build_hydrofabric, op_kwargs={})
    runner.run_task(task_id="reduce_base", python_callable=reduce_combine_base_hydrofabric, op_kwargs={})
    runner.run_task(task_id="trace_attributes", python_callable=trace_hydrofabric_attributes, op_kwargs={})
    final_flowpaths = runner.ti.xcom_pull(task_id="trace_attributes", key="flowpaths_with_attributes")
    final_nexus = runner.ti.xcom_pull(task_id="reduce_base", key="nexus")
    final_virtual_flowpaths = runner.ti.xcom_pull(task_id="reduce_base", key="virtual_flowpaths")
    final_virtual_nexus = runner.ti.xcom_pull(task_id="reduce_base", key="virtual_nexus")
    final_reference_flowpaths = runner.ti.xcom_pull(task_id="reduce_base", key="reference_flowpaths")
    reference_fp_pl = pl.from_pandas(final_reference_flowpaths)

    fp_pl = pl.from_pandas(final_flowpaths.to_wkb())
    upstream_dict = _build_upstream_dict_from_nexus(fp_pl)
    graph, _ = _build_rustworkx_object(upstream_dict)
    assert rx.is_directed_acyclic_graph(graph), "Graph must be acyclic"
    assert rx.is_weakly_connected(graph), "All flowpaths should connect to single outlet"
    assert not rx.is_strongly_connected(graph), "DAG cannot be strongly connected"
    graph_outlets: list[int] = [node for node in graph.node_indices() if graph.out_degree(node) == 0]
    assert len(graph_outlets) == 1, f"Should have exactly 1 outlet, found {len(graph_outlets)}"
    strong_components = rx.strongly_connected_components(graph)
    assert len(strong_components) == graph.num_nodes(), "Each node should be its own SCC in a DAG"

    virtual_fp_pl = pl.from_pandas(final_virtual_flowpaths.to_wkb())
    virtual_upstream_dict = _build_upstream_dict_from_nexus(
        virtual_fp_pl, edge_id="virtual_fp_id", node_id="virtual_nex_id"
    )
    virtual_graph, _ = _build_rustworkx_object(virtual_upstream_dict)
    assert rx.is_directed_acyclic_graph(virtual_graph), "Virtual graph must be acyclic"
    assert rx.is_weakly_connected(virtual_graph), "All virtual flowpaths should connect to single outlet"
    assert not rx.is_strongly_connected(virtual_graph), "Virtual DAG cannot be strongly connected"
    virtual_outlets = [node for node in virtual_graph.node_indices() if virtual_graph.out_degree(node) == 0]
    assert len(virtual_outlets) == 1, f"Should have exactly 1 virtual outlet, found {len(virtual_outlets)}"
    virtual_strong_components = rx.strongly_connected_components(virtual_graph)
    assert len(virtual_strong_components) == virtual_graph.num_nodes(), (
        "Each virtual node should be its own SCC"
    )

    df = pd.DataFrame(
        {
            "nex_id": pd.Series([1, 2], dtype="int64"),
            "dn_fp_id": pd.Series([pd.NA, 1], dtype="Int64"),
            "vpu_id": pd.Series(["01", "01"], dtype="object"),
        }
    )
    pd.testing.assert_frame_equal(final_nexus.drop(columns=["geometry"]), df)

    df = pd.DataFrame(
        {
            "virtual_nex_id": pd.Series([4, 5, 7, 8, 9, 10, 11], dtype="Int64"),
            "dn_virtual_fp_id": pd.Series([pd.NA, 4, 6, 7, 8, 5, 5], dtype="Int64"),
            "vpu_id": pd.Series(["01", "01", "01", "01", "01", "01", "01"], dtype="object"),
        }
    )
    pd.testing.assert_frame_equal(final_virtual_nexus.drop(columns=["geometry"]), df)

    _check_hydroseq_decreases_downstream(fp_pl, graph, fp_id_col="fp_id")
    _check_area_percentages_sum_to_one(virtual_fp_pl, reference_fp_pl)


def test_hudson_river_large_scale(trace_case_hudson_river_large_scale: HFConfig) -> None:
    """Testing the tracing output for when there is a no-divide connector at the upstream-most point of a divide"""
    runner = LocalRunner(trace_case_hudson_river_large_scale)
    runner.run_task("download", download_reference_data)
    runner.run_task("build_graph", build_graph)

    outlets: list[str] = runner.ti.xcom_pull(task_id="build_graph", key="outlets")
    outlet_subgraphs: dict[str, dict[str, Any]] = runner.ti.xcom_pull(
        task_id="build_graph", key="outlet_subgraphs"
    )
    outlet = outlets[0]
    partition_data = outlet_subgraphs[outlet]
    filtered_divides = partition_data["divides"]
    valid_divide_ids: set[str] = set(filtered_divides["divide_id"].to_list())
    classifications = _trace_stack(
        start_id=outlet,
        div_ids=valid_divide_ids,
        cfg=trace_case_hudson_river_large_scale,
        partition_data=partition_data,
    )

    aggregate_data = _aggregate_geometries(
        classifications=classifications,
        partition_data=partition_data,
    )

    assert len(aggregate_data.aggregates) == 2319, "Incorrect number of aggregates"
    assert len(aggregate_data.independents) == 1277, "Incorrect number of independents"
    assert len(aggregate_data.connectors) == 996, "Incorrect number of connectors"
    assert len(aggregate_data.non_nextgen_flowpaths) == 3431, "Incorrect number of non nextgen flowpaths"
    assert len(aggregate_data.non_nextgen_virtual_flowpaths) == 1907, (
        "Incorrect number of non nextgen virtual flowpaths"
    )
    assert len(aggregate_data.virtual_flowpaths) == 9048, "Incorrect number of virtual flowpaths"

    runner.run_task(task_id="map_flowpaths", python_callable=map_trace_and_aggregate, op_kwargs={})

    runner.run_task(task_id="map_build_base", python_callable=map_build_hydrofabric, op_kwargs={})
    runner.run_task(task_id="reduce_base", python_callable=reduce_combine_base_hydrofabric, op_kwargs={})
    runner.run_task(task_id="trace_attributes", python_callable=trace_hydrofabric_attributes, op_kwargs={})
    final_flowpaths = runner.ti.xcom_pull(task_id="trace_attributes", key="flowpaths_with_attributes")
    final_nexus = runner.ti.xcom_pull(task_id="reduce_base", key="nexus")
    final_virtual_flowpaths = runner.ti.xcom_pull(task_id="reduce_base", key="virtual_flowpaths")
    final_virtual_nexus = runner.ti.xcom_pull(task_id="reduce_base", key="virtual_nexus")
    final_reference_flowpaths = runner.ti.xcom_pull(task_id="reduce_base", key="reference_flowpaths")
    reference_fp_pl = pl.from_pandas(final_reference_flowpaths)

    fp_pl = pl.from_pandas(final_flowpaths.to_wkb())
    upstream_dict = _build_upstream_dict_from_nexus(fp_pl)
    graph, _ = _build_rustworkx_object(upstream_dict)
    assert rx.is_directed_acyclic_graph(graph), "Graph must be acyclic"
    assert rx.is_weakly_connected(graph), "All flowpaths should connect to single outlet"
    assert not rx.is_strongly_connected(graph), "DAG cannot be strongly connected"
    graph_outlets: list[int] = [node for node in graph.node_indices() if graph.out_degree(node) == 0]
    assert len(graph_outlets) == 1, f"Should have exactly 1 outlet, found {len(graph_outlets)}"
    strong_components = rx.strongly_connected_components(graph)
    assert len(strong_components) == graph.num_nodes(), "Each node should be its own SCC in a DAG"

    virtual_fp_pl = pl.from_pandas(final_virtual_flowpaths.to_wkb())
    virtual_upstream_dict = _build_upstream_dict_from_nexus(
        virtual_fp_pl, edge_id="virtual_fp_id", node_id="virtual_nex_id"
    )
    virtual_graph, _ = _build_rustworkx_object(virtual_upstream_dict)
    assert rx.is_directed_acyclic_graph(virtual_graph), "Virtual graph must be acyclic"
    assert rx.is_weakly_connected(virtual_graph), "All virtual flowpaths should connect to single outlet"
    assert not rx.is_strongly_connected(virtual_graph), "Virtual DAG cannot be strongly connected"
    virtual_outlets = [node for node in virtual_graph.node_indices() if virtual_graph.out_degree(node) == 0]
    assert len(virtual_outlets) == 1, f"Should have exactly 1 virtual outlet, found {len(virtual_outlets)}"
    virtual_strong_components = rx.strongly_connected_components(virtual_graph)
    assert len(virtual_strong_components) == virtual_graph.num_nodes(), (
        "Each virtual node should be its own SCC"
    )

    expected_df = pd.read_csv(
        here() / "tests/data/trace_cases/hudson_river_nexus.csv",
        dtype={"nex_id": "int64", "dn_fp_id": "Int64", "vpu_id": "object"},
    )
    pd.testing.assert_frame_equal(final_nexus.drop(columns=["geometry"]), expected_df)

    expected_df = pd.read_csv(
        here() / "tests/data/trace_cases/hudson_river_virtual_nexus.csv",
        dtype={"virtual_nex_id": "Int64", "dn_virtual_fp_id": "Int64", "vpu_id": "object"},
    )
    pd.testing.assert_frame_equal(final_virtual_nexus.drop(columns=["geometry"]), expected_df)

    _check_hydroseq_decreases_downstream(fp_pl, graph, fp_id_col="fp_id")
    _check_area_percentages_sum_to_one(virtual_fp_pl, reference_fp_pl)


def test_sioux_falls(trace_case_sioux_falls: HFConfig) -> None:
    """Testing the tracing output for when there is a no-divide connector at the upstream-most point of a divide"""
    runner = LocalRunner(trace_case_sioux_falls)
    runner.run_task("download", download_reference_data)
    runner.run_task("build_graph", build_graph)

    outlets: list[str] = runner.ti.xcom_pull(task_id="build_graph", key="outlets")
    outlet_subgraphs: dict[str, dict[str, Any]] = runner.ti.xcom_pull(
        task_id="build_graph", key="outlet_subgraphs"
    )
    outlet = outlets[0]
    partition_data = outlet_subgraphs[outlet]
    filtered_divides = partition_data["divides"]
    valid_divide_ids: set[str] = set(filtered_divides["divide_id"].to_list())
    classifications = _trace_stack(
        start_id=outlet,
        div_ids=valid_divide_ids,
        cfg=trace_case_sioux_falls,
        partition_data=partition_data,
    )

    aggregate_data = _aggregate_geometries(
        classifications=classifications,
        partition_data=partition_data,
    )

    assert len(aggregate_data.aggregates) == 1771, "Incorrect number of aggregates"
    assert len(aggregate_data.independents) == 1448, "Incorrect number of independents"
    assert len(aggregate_data.connectors) == 1060, "Incorrect number of connectors"
    assert len(aggregate_data.non_nextgen_flowpaths) == 7072, "Incorrect number of non nextgen flowpaths"
    assert len(aggregate_data.non_nextgen_virtual_flowpaths) == 2254, (
        "Incorrect number of non nextgen virtual flowpaths"
    )
    assert len(aggregate_data.virtual_flowpaths) == 7266, "Incorrect number of virtual flowpaths"

    runner.run_task(task_id="map_flowpaths", python_callable=map_trace_and_aggregate, op_kwargs={})

    runner.run_task(task_id="map_build_base", python_callable=map_build_hydrofabric, op_kwargs={})
    runner.run_task(task_id="reduce_base", python_callable=reduce_combine_base_hydrofabric, op_kwargs={})
    runner.run_task(task_id="trace_attributes", python_callable=trace_hydrofabric_attributes, op_kwargs={})
    final_flowpaths = runner.ti.xcom_pull(task_id="trace_attributes", key="flowpaths_with_attributes")
    final_nexus = runner.ti.xcom_pull(task_id="reduce_base", key="nexus")
    final_virtual_flowpaths = runner.ti.xcom_pull(task_id="reduce_base", key="virtual_flowpaths")
    final_virtual_nexus = runner.ti.xcom_pull(task_id="reduce_base", key="virtual_nexus")
    final_reference_flowpaths = runner.ti.xcom_pull(task_id="reduce_base", key="reference_flowpaths")
    reference_fp_pl = pl.from_pandas(final_reference_flowpaths)

    fp_pl = pl.from_pandas(final_flowpaths.to_wkb())
    upstream_dict = _build_upstream_dict_from_nexus(fp_pl)
    graph, _ = _build_rustworkx_object(upstream_dict)
    assert rx.is_directed_acyclic_graph(graph), "Graph must be acyclic"
    assert rx.is_weakly_connected(graph), "All flowpaths should connect to single outlet"
    assert not rx.is_strongly_connected(graph), "DAG cannot be strongly connected"
    graph_outlets: list[int] = [node for node in graph.node_indices() if graph.out_degree(node) == 0]
    assert len(graph_outlets) == 1, f"Should have exactly 1 outlet, found {len(graph_outlets)}"
    strong_components = rx.strongly_connected_components(graph)
    assert len(strong_components) == graph.num_nodes(), "Each node should be its own SCC in a DAG"

    virtual_fp_pl = pl.from_pandas(final_virtual_flowpaths.to_wkb())
    virtual_upstream_dict = _build_upstream_dict_from_nexus(
        virtual_fp_pl, edge_id="virtual_fp_id", node_id="virtual_nex_id"
    )
    virtual_graph, _ = _build_rustworkx_object(virtual_upstream_dict)
    assert rx.is_directed_acyclic_graph(virtual_graph), "Virtual graph must be acyclic"
    assert rx.is_weakly_connected(virtual_graph), "All virtual flowpaths should connect to single outlet"
    assert not rx.is_strongly_connected(virtual_graph), "Virtual DAG cannot be strongly connected"
    virtual_outlets = [node for node in virtual_graph.node_indices() if virtual_graph.out_degree(node) == 0]
    assert len(virtual_outlets) == 1, f"Should have exactly 1 virtual outlet, found {len(virtual_outlets)}"
    virtual_strong_components = rx.strongly_connected_components(virtual_graph)
    assert len(virtual_strong_components) == virtual_graph.num_nodes(), (
        "Each virtual node should be its own SCC"
    )

    expected_df = pd.read_csv(
        here() / "tests/data/trace_cases/sioux_falls_nexus.csv",
        dtype={"nex_id": "int64", "dn_fp_id": "Int64", "vpu_id": "object"},
    )
    pd.testing.assert_frame_equal(final_nexus.drop(columns=["geometry"]), expected_df)

    expected_df = pd.read_csv(
        here() / "tests/data/trace_cases/sioux_falls_virtual_nexus.csv",
        dtype={"virtual_nex_id": "Int64", "dn_virtual_fp_id": "Int64", "vpu_id": "object"},
    )
    pd.testing.assert_frame_equal(final_virtual_nexus.drop(columns=["geometry"]), expected_df)

    _check_hydroseq_decreases_downstream(fp_pl, graph, fp_id_col="fp_id")
    _check_area_percentages_sum_to_one(virtual_fp_pl, reference_fp_pl)


def test_large_braided_river(trace_case_large_braided: HFConfig) -> None:
    """Testing the tracing output for when there is a no-divide connector at the upstream-most point of a divide"""
    runner = LocalRunner(trace_case_large_braided)
    runner.run_task("download", download_reference_data)
    runner.run_task("build_graph", build_graph)

    outlets: list[str] = runner.ti.xcom_pull(task_id="build_graph", key="outlets")
    outlet_subgraphs: dict[str, dict[str, Any]] = runner.ti.xcom_pull(
        task_id="build_graph", key="outlet_subgraphs"
    )
    outlet = outlets[0]
    partition_data = outlet_subgraphs[outlet]
    filtered_divides = partition_data["divides"]
    valid_divide_ids: set[str] = set(filtered_divides["divide_id"].to_list())
    classifications = _trace_stack(
        start_id=outlet,
        div_ids=valid_divide_ids,
        cfg=trace_case_large_braided,
        partition_data=partition_data,
    )

    aggregate_data = _aggregate_geometries(
        classifications=classifications,
        partition_data=partition_data,
    )

    assert len(aggregate_data.aggregates) == 6855, "Incorrect number of aggregates"
    assert len(aggregate_data.independents) == 3941, "Incorrect number of independents"
    assert len(aggregate_data.connectors) == 2969, "Incorrect number of connectors"
    assert len(aggregate_data.non_nextgen_flowpaths) == 11238, "Incorrect number of non nextgen flowpaths"
    assert len(aggregate_data.non_nextgen_virtual_flowpaths) == 7877, (
        "Incorrect number of non nextgen virtual flowpaths"
    )
    assert len(aggregate_data.virtual_flowpaths) == 27437, "Incorrect number of virtual flowpaths"

    runner.run_task(task_id="map_flowpaths", python_callable=map_trace_and_aggregate, op_kwargs={})

    runner.run_task(task_id="map_build_base", python_callable=map_build_hydrofabric, op_kwargs={})
    runner.run_task(task_id="reduce_base", python_callable=reduce_combine_base_hydrofabric, op_kwargs={})
    runner.run_task(task_id="trace_attributes", python_callable=trace_hydrofabric_attributes, op_kwargs={})
    final_flowpaths = runner.ti.xcom_pull(task_id="trace_attributes", key="flowpaths_with_attributes")
    final_nexus = runner.ti.xcom_pull(task_id="reduce_base", key="nexus")
    final_virtual_flowpaths = runner.ti.xcom_pull(task_id="reduce_base", key="virtual_flowpaths")
    final_virtual_nexus = runner.ti.xcom_pull(task_id="reduce_base", key="virtual_nexus")
    final_reference_flowpaths = runner.ti.xcom_pull(task_id="reduce_base", key="reference_flowpaths")
    reference_fp_pl = pl.from_pandas(final_reference_flowpaths)

    fp_pl = pl.from_pandas(final_flowpaths.to_wkb())
    upstream_dict = _build_upstream_dict_from_nexus(fp_pl)
    graph, _ = _build_rustworkx_object(upstream_dict)
    assert rx.is_directed_acyclic_graph(graph), "Graph must be acyclic"
    assert rx.is_weakly_connected(graph), "All flowpaths should connect to single outlet"
    assert not rx.is_strongly_connected(graph), "DAG cannot be strongly connected"
    graph_outlets: list[int] = [node for node in graph.node_indices() if graph.out_degree(node) == 0]
    assert len(graph_outlets) == 1, f"Should have exactly 1 outlet, found {len(graph_outlets)}"
    strong_components = rx.strongly_connected_components(graph)
    assert len(strong_components) == graph.num_nodes(), "Each node should be its own SCC in a DAG"

    virtual_fp_pl = pl.from_pandas(final_virtual_flowpaths.to_wkb())
    virtual_upstream_dict = _build_upstream_dict_from_nexus(
        virtual_fp_pl, edge_id="virtual_fp_id", node_id="virtual_nex_id"
    )
    virtual_graph, _ = _build_rustworkx_object(virtual_upstream_dict)
    assert rx.is_directed_acyclic_graph(virtual_graph), "Virtual graph must be acyclic"
    assert rx.is_weakly_connected(virtual_graph), "All virtual flowpaths should connect to single outlet"
    assert not rx.is_strongly_connected(virtual_graph), "Virtual DAG cannot be strongly connected"
    virtual_outlets = [node for node in virtual_graph.node_indices() if virtual_graph.out_degree(node) == 0]
    assert len(virtual_outlets) == 1, f"Should have exactly 1 virtual outlet, found {len(virtual_outlets)}"
    virtual_strong_components = rx.strongly_connected_components(virtual_graph)
    assert len(virtual_strong_components) == virtual_graph.num_nodes(), (
        "Each virtual node should be its own SCC"
    )

    expected_df = pd.read_csv(
        here() / "tests/data/trace_cases/large_braided_river_nexus.csv",
        dtype={"nex_id": "int64", "dn_fp_id": "Int64", "vpu_id": "object"},
    )
    pd.testing.assert_frame_equal(final_nexus.drop(columns=["geometry"]), expected_df)

    expected_df = pd.read_csv(
        here() / "tests/data/trace_cases/large_braided_river_virtual_nexus.csv",
        dtype={"virtual_nex_id": "Int64", "dn_virtual_fp_id": "Int64", "vpu_id": "object"},
    )
    pd.testing.assert_frame_equal(final_virtual_nexus.drop(columns=["geometry"]), expected_df)

    _check_hydroseq_decreases_downstream(fp_pl, graph, fp_id_col="fp_id")
    _check_area_percentages_sum_to_one(virtual_fp_pl, reference_fp_pl)


def test_small_braided_river(trace_case_small_braided: HFConfig) -> None:
    """Testing the tracing output for when there is a no-divide connector at the upstream-most point of a divide"""
    runner = LocalRunner(trace_case_small_braided)
    runner.run_task("download", download_reference_data)
    runner.run_task("build_graph", build_graph)

    outlets: list[str] = runner.ti.xcom_pull(task_id="build_graph", key="outlets")
    outlet_subgraphs: dict[str, dict[str, Any]] = runner.ti.xcom_pull(
        task_id="build_graph", key="outlet_subgraphs"
    )
    outlet = outlets[0]
    partition_data = outlet_subgraphs[outlet]
    filtered_divides = partition_data["divides"]
    valid_divide_ids: set[str] = set(filtered_divides["divide_id"].to_list())
    classifications = _trace_stack(
        start_id=outlet,
        div_ids=valid_divide_ids,
        cfg=trace_case_small_braided,
        partition_data=partition_data,
    )

    aggregate_data = _aggregate_geometries(
        classifications=classifications,
        partition_data=partition_data,
    )

    assert len(aggregate_data.aggregates) == 2580, "Incorrect number of aggregates"
    assert len(aggregate_data.independents) == 2025, "Incorrect number of independents"
    assert len(aggregate_data.connectors) == 1443, "Incorrect number of connectors"
    assert len(aggregate_data.non_nextgen_flowpaths) == 3666, "Incorrect number of non nextgen flowpaths"
    assert len(aggregate_data.non_nextgen_virtual_flowpaths) == 2395, (
        "Incorrect number of non nextgen virtual flowpaths"
    )
    assert len(aggregate_data.virtual_flowpaths) == 10983, "Incorrect number of virtual flowpaths"

    runner.run_task(task_id="map_flowpaths", python_callable=map_trace_and_aggregate, op_kwargs={})
    runner.run_task(task_id="map_build_base", python_callable=map_build_hydrofabric, op_kwargs={})
    runner.run_task(task_id="reduce_base", python_callable=reduce_combine_base_hydrofabric, op_kwargs={})
    runner.run_task(task_id="trace_attributes", python_callable=trace_hydrofabric_attributes, op_kwargs={})
    final_flowpaths = runner.ti.xcom_pull(task_id="trace_attributes", key="flowpaths_with_attributes")
    final_nexus = runner.ti.xcom_pull(task_id="reduce_base", key="nexus")
    final_virtual_flowpaths = runner.ti.xcom_pull(task_id="reduce_base", key="virtual_flowpaths")
    final_virtual_nexus = runner.ti.xcom_pull(task_id="reduce_base", key="virtual_nexus")
    final_reference_flowpaths = runner.ti.xcom_pull(task_id="reduce_base", key="reference_flowpaths")
    reference_fp_pl = pl.from_pandas(final_reference_flowpaths)

    fp_pl = pl.from_pandas(final_flowpaths.to_wkb())
    upstream_dict = _build_upstream_dict_from_nexus(fp_pl)
    graph, _ = _build_rustworkx_object(upstream_dict)
    assert rx.is_directed_acyclic_graph(graph), "Graph must be acyclic"
    assert rx.is_weakly_connected(graph), "All flowpaths should connect to single outlet"
    assert not rx.is_strongly_connected(graph), "DAG cannot be strongly connected"
    graph_outlets: list[int] = [node for node in graph.node_indices() if graph.out_degree(node) == 0]
    assert len(graph_outlets) == 1, f"Should have exactly 1 outlet, found {len(graph_outlets)}"
    strong_components = rx.strongly_connected_components(graph)
    assert len(strong_components) == graph.num_nodes(), "Each node should be its own SCC in a DAG"

    virtual_fp_pl = pl.from_pandas(final_virtual_flowpaths.to_wkb())
    virtual_upstream_dict = _build_upstream_dict_from_nexus(
        virtual_fp_pl, edge_id="virtual_fp_id", node_id="virtual_nex_id"
    )
    virtual_graph, _ = _build_rustworkx_object(virtual_upstream_dict)
    assert rx.is_directed_acyclic_graph(virtual_graph), "Virtual graph must be acyclic"
    assert rx.is_weakly_connected(virtual_graph), "All virtual flowpaths should connect to single outlet"
    assert not rx.is_strongly_connected(virtual_graph), "Virtual DAG cannot be strongly connected"
    virtual_outlets = [node for node in virtual_graph.node_indices() if virtual_graph.out_degree(node) == 0]
    assert len(virtual_outlets) == 1, f"Should have exactly 1 virtual outlet, found {len(virtual_outlets)}"
    virtual_strong_components = rx.strongly_connected_components(virtual_graph)
    assert len(virtual_strong_components) == virtual_graph.num_nodes(), (
        "Each virtual node should be its own SCC"
    )

    expected_df = pd.read_csv(
        here() / "tests/data/trace_cases/small_braided_river_nexus.csv",
        dtype={"nex_id": "int64", "dn_fp_id": "Int64", "vpu_id": "object"},
    )
    pd.testing.assert_frame_equal(final_nexus.drop(columns=["geometry"]), expected_df)

    expected_df = pd.read_csv(
        here() / "tests/data/trace_cases/small_braided_river_virtual_nexus.csv",
        dtype={"virtual_nex_id": "Int64", "dn_virtual_fp_id": "Int64", "vpu_id": "object"},
    )
    pd.testing.assert_frame_equal(final_virtual_nexus.drop(columns=["geometry"]), expected_df)

    _check_hydroseq_decreases_downstream(fp_pl, graph, fp_id_col="fp_id")
    _check_area_percentages_sum_to_one(virtual_fp_pl, reference_fp_pl)
