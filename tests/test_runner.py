"""Tests for the local runner"""

from typing import Any

import numpy as np
import pandas as pd
import rustworkx as rx
from scipy import sparse

from hydrofabric_builds import (
    HFConfig,
    build_graph,
    build_nhf_graph,
    download_reference_data,
    map_build_hydrofabric,
    map_trace_and_aggregate,
    reduce_combine_base_hydrofabric,
    trace_hydrofabric_attributes,
    write_base_hydrofabric,
)
from scripts.hf_runner import LocalRunner, TaskInstance


class TestTaskInstance:
    """Tests for TaskInstance XCom functionality."""

    def test_xcom_push_and_pull(self, task_instance: TaskInstance) -> None:
        """Test basic push and pull operations."""
        task_instance.xcom_push("task1.result", {"data": 42})
        result = task_instance.xcom_pull("task1", key="result")
        assert result == {"data": 42}

    def test_xcom_pull_default_key(self, task_instance: TaskInstance) -> None:
        """Test pulling with default 'return_value' key."""
        task_instance.xcom_push("task1.return_value", "output.gpkg")
        result = task_instance.xcom_pull("task1")
        assert result == "output.gpkg"

    def test_xcom_pull_nonexistent(self, task_instance: TaskInstance) -> None:
        """Test pulling non-existent key returns None."""
        result = task_instance.xcom_pull("nonexistent_task")
        assert result is None

    def test_xcom_push_overwrite(self, task_instance: TaskInstance) -> None:
        """Test that pushing to same key overwrites previous value."""
        task_instance.xcom_push("task1.data", "first")
        task_instance.xcom_push("task1.data", "second")
        result = task_instance.xcom_pull("task1", key="data")
        assert result == "second"

    def test_xcom_multiple_tasks(self, task_instance: TaskInstance) -> None:
        """Test XCom with multiple tasks."""
        task_instance.xcom_push("download.return_value", "/tmp/data.zip")
        task_instance.xcom_push("process.return_value", "/tmp/data.gpkg")
        task_instance.xcom_push("export.return_value", "/tmp/output.json")

        assert task_instance.xcom_pull("download") == "/tmp/data.zip"
        assert task_instance.xcom_pull("process") == "/tmp/data.gpkg"
        assert task_instance.xcom_pull("export") == "/tmp/output.json"

    def test_xcom_complex_types(self, task_instance: TaskInstance) -> None:
        """Test XCom with complex data types."""
        complex_data = {
            "paths": ["/path/1", "/path/2"],
            "config": {"divide_aggregation_threshold": 3.0, "enabled": True},
            "stats": {"count": 100, "area": 45.6},
        }

        task_instance.xcom_push("task1.return_value", complex_data)
        result = task_instance.xcom_pull("task1")
        assert result == complex_data
        assert result["stats"]["count"] == 100


class TestLocalRunner:
    """Tests for LocalRunner task execution."""

    def test_initialization(self, sample_config: HFConfig) -> None:
        """Test LocalRunner initialization."""
        with LocalRunner(sample_config) as runner:
            assert runner.config == sample_config
            assert runner.run_id is not None
            assert isinstance(runner.ti, TaskInstance)
            assert runner.results == {}

    def test_custom_run_id(self, sample_config: HFConfig) -> None:
        """Test LocalRunner with custom run_id."""
        custom_id = "test_run_12345"
        with LocalRunner(sample_config, run_id=custom_id) as runner:
            assert runner.run_id == custom_id

    def test_run_simple_task(self, sample_config: HFConfig) -> None:
        """Test running a simple task."""

        def simple_task(value: int, **context: dict[str, Any]) -> dict[str, Any]:
            return {"value": value * 2}

        with LocalRunner(sample_config) as runner:
            _ = runner.run_task(task_id="simple", python_callable=simple_task, op_kwargs={"value": 21})

            assert runner.get_result("simple")["status"] == "success"
            assert runner.get_result("simple")["result"]["value"] == 42

    def test_task_context_injection(self, sample_config: HFConfig) -> None:
        """Test that context is properly injected into tasks."""

        def task_with_context(**context: dict[str, Any]) -> dict[str, Any]:
            return {
                "has_ti": "ti" in context,
                "has_task_id": "task_id" in context,
                "has_run_id": "run_id" in context,
                "has_config": "config" in context,
                "has_ds": "ds" in context,
                "has_execution_date": "execution_date" in context,
            }

        with LocalRunner(sample_config) as runner:
            result = runner.run_task("check_context", task_with_context)

            assert all(result.values()), "Missing context keys"
            assert result["has_ti"]
            assert result["has_config"]

    def test_task_accesses_config(self, sample_config: HFConfig) -> None:
        """Test that tasks can access config from context."""

        def task_using_config(**context: dict[str, Any]) -> dict:
            config = context["config"]
            assert config.build.divide_aggregation_threshold == 3.0  # type: ignore
            return {}

        with LocalRunner(sample_config) as runner:
            _ = runner.run_task("use_config", task_using_config)

    def test_task_uses_xcom(self, sample_config: HFConfig) -> None:
        """Test tasks using XCom to pass data."""

        def task1(**context: dict[str, Any]) -> dict[str, Any]:
            return {"path": "/tmp/output.gpkg"}

        def task2(**context: dict[str, Any]) -> dict:
            ti = context["ti"]
            input_file = ti.xcom_pull("task1", key="path")  # type: ignore
            assert input_file == "/tmp/output.gpkg"
            return {}

        with LocalRunner(sample_config) as runner:
            runner.run_task("task1", task1)
            _ = runner.run_task("task2", task2)

    def test_multiple_tasks_sequential(self, sample_config: HFConfig) -> None:
        """Test running multiple tasks sequentially."""

        def task1(value: int, **context: dict[str, Any]) -> dict[str, Any]:
            return {"value": value + 10}

        def task2(**context: dict[str, Any]) -> dict[str, Any]:
            ti = context["ti"]
            prev = ti.xcom_pull("task1", key="value")  # type: ignore
            return {"value": prev * 2}

        def task3(**context: dict[str, Any]) -> dict[str, Any]:
            ti = context["ti"]
            prev = ti.xcom_pull("task2", key="value")  # type: ignore
            return {"value": prev * 5}

        with LocalRunner(sample_config) as runner:
            runner.run_task("task1", task1, {"value": 5})
            runner.run_task("task2", task2)
            _ = runner.run_task("task3", task3)
            assert len(runner.results) == 3
            assert all(r["status"] == "success" for r in runner.results.values())

    def test_task_return_value_stored_in_xcom(self, sample_config: HFConfig) -> None:
        """Test that task return values are automatically stored in XCom."""

        def task_with_return(**context: dict[str, Any]) -> dict[str, Any]:
            return {"output": "test.gpkg", "count": 100}

        with LocalRunner(sample_config) as runner:
            runner.run_task("test", task_with_return)
            assert runner.ti.xcom_pull("test", key="output") == "test.gpkg"
            assert runner.ti.xcom_pull("test", key="count") == 100


class TestIntegration:
    """Integration tests simulating real pipeline scenarios."""

    def test_full_pipeline(self, sample_config: HFConfig, expected_graph: dict[str, Any]) -> None:
        """Test full pipeline with real download and build_graph functions."""

        with LocalRunner(sample_config) as runner:
            runner.run_task("download", download_reference_data)
            runner.run_task("build_graph", build_graph)
            runner.run_task(task_id="map_flowpaths", python_callable=map_trace_and_aggregate, op_kwargs={})
            runner.run_task(task_id="map_build_base", python_callable=map_build_hydrofabric, op_kwargs={})
            runner.run_task(
                task_id="reduce_base", python_callable=reduce_combine_base_hydrofabric, op_kwargs={}
            )
            runner.run_task(
                task_id="trace_attributes", python_callable=trace_hydrofabric_attributes, op_kwargs={}
            )
            runner.run_task(task_id="write_base", python_callable=write_base_hydrofabric, op_kwargs={})

            assert all(r["status"] == "success" for r in runner.results.values())

            download_result = runner.get_result("download")
            assert download_result["status"] == "success"

            reference_flowpaths = runner.ti.xcom_pull("download", key="reference_flowpaths")
            reference_divides = runner.ti.xcom_pull("download", key="reference_divides")

            assert reference_flowpaths is not None
            assert reference_divides is not None
            assert len(reference_flowpaths) > 0
            assert len(reference_divides) > 0

            graph_result = runner.get_result("build_graph")
            assert graph_result["status"] == "success"

            outlets = runner.ti.xcom_pull("build_graph", key="outlets")
            assert "6720879" in outlets  # expected outlet

            final_flowpaths = runner.ti.xcom_pull(task_id="trace_attributes", key="flowpaths_with_attributes")
            final_divides = runner.ti.xcom_pull(task_id="reduce_base", key="divides")
            # final_nexus = runner.ti.xcom_pull(task_id="reduce_base", key="nexus")

            # commenting out until builds are final
            # assert len(final_flowpaths) == 40
            # assert len(final_divides) == 40
            # assert len(final_nexus) == 34

            flowpath_ids = set(final_flowpaths["fp_id"])
            divide_ids = set(final_divides["div_id"])
            assert flowpath_ids == divide_ids, (
                f"Flowpath IDs and Divide IDs don't match.\n"
                f"Missing in divides: {flowpath_ids - divide_ids}\n"
                f"Missing in flowpaths: {divide_ids - flowpath_ids}"
            )
            # Test connectivity using nexus points
            graph = build_nhf_graph(final_flowpaths)
            self._verify_graph_structure(graph)
            self._verify_cumulative_drainage_area(graph, final_flowpaths)
            self._verify_path_length_logic(graph, final_flowpaths)
            self._verify_downstream_hydroseq(graph, final_flowpaths)
            self._verify_mainstem_tracing(graph, final_flowpaths)

    def _verify_graph_structure(self, graph: rx.PyDiGraph) -> None:
        """Verify dendritic structure and topological ordering.

        Parameters
        ----------
        graph : rx.PyDiGraph
            Graph representing flowpath connectivity

        Raises
        ------
        AssertionError
            If the network has cycles, is not dendritic, or is not in lower triangular ordering
        """
        # Check for cycles
        try:
            ts_order = rx.topological_sort(graph)
        except rx.DAGHasCycle as e:
            raise AssertionError("Network contains cycles - not a valid dendritic network") from e

        id_order = [graph.get_node_data(gidx) for gidx in ts_order]
        idx_map = {id_val: idx for idx, id_val in enumerate(id_order)}

        col = []
        row = []

        for node in ts_order:
            if graph.out_degree(node) == 0:  # terminal node
                continue

            node_id = graph.get_node_data(node)
            successor_indices = graph.successor_indices(node)

            # Verify dendritic structure (each flowpath has at most one downstream)
            assert len(successor_indices) == 1, (
                f"Flowpath {node_id} has {len(successor_indices)} successors, "
                f"network is not dendritic (should have exactly 1 downstream)"
            )

            downstream_node_idx = successor_indices[0]
            downstream_id = graph.get_node_data(downstream_node_idx)

            col.append(idx_map[node_id])
            row.append(idx_map[downstream_id])

        matrix = sparse.coo_matrix(
            (np.ones(len(row), dtype=np.uint8), (row, col)),
            shape=(len(ts_order), len(ts_order)),
            dtype=np.uint8,
        )
        # Ensure matrix is lower triangular (proper topological ordering)
        assert np.all(matrix.row >= matrix.col), (
            "Adjacency matrix is not lower triangular - flowpaths are not in proper topological order"
        )

    def _verify_hydroseq(self, graph: rx.PyDiGraph, flowpaths_gdf: pd.DataFrame) -> rx.PyDiGraph:
        """verify hydroseq ordering.

        Parameters
        ----------
        graph : rx.PyDiGraph
            Graph where nodes are fp_id values
        flowpaths_gdf : pd.DataFrame
            Flowpaths with fp_id, area_sqkm, hydroseq columns

        Returns
        -------
        rx.PyDiGraph
            Graph with edges containing total_da_sqkm attribute

        Raises
        ------
        AssertionError
            If hydroseq doesn't decrease downstream
        """
        # Create lookup for flowpath attributes
        fp_lookup = flowpaths_gdf.set_index("fp_id")[["area_sqkm", "hydroseq"]].to_dict("index")

        # Get topological order (processes upstream nodes first)
        try:
            topo_order = rx.topological_sort(graph)
        except rx.DAGHasCycle as e:
            raise AssertionError("Graph contains cycles - cannot be a dendritic network") from e

        # Initialize node data with local area and hydroseq
        for node_idx in graph.node_indices():
            fp_id = graph[node_idx]
            graph[node_idx] = {
                "fp_id": fp_id,
                "area_sqkm": fp_lookup[fp_id]["area_sqkm"],
                "hydroseq": fp_lookup[fp_id]["hydroseq"],
                "total_da_sqkm": 0.0,  # Will be computed
            }

        # Process nodes in topological order to accumulate drainage area
        for node_idx in topo_order:
            node_data = graph[node_idx]
            in_edges = graph.in_edges(node_idx)

            # Verify hydroseq decreases downstream
            for src_idx, _, _ in in_edges:
                upstream_hydroseq = graph[src_idx]["hydroseq"]
                current_hydroseq = node_data["hydroseq"]

                assert upstream_hydroseq > current_hydroseq, (
                    f"Hydroseq ordering violated: upstream fp_id={graph[src_idx]['fp_id']} "
                    f"(hydroseq={upstream_hydroseq}) -> downstream fp_id={node_data['fp_id']} "
                    f"(hydroseq={current_hydroseq}). Hydroseq should decrease downstream."
                )

        return graph

    def _verify_cumulative_drainage_area(self, graph: rx.PyDiGraph, flowpaths: pd.DataFrame) -> None:
        """Verify total drainage area accumulates correctly through topological sort.

        For each node: total_da_sqkm = sum(upstream total_da_sqkm) + area_sqkm
        """
        try:
            topo_order = rx.topological_sort(graph)
        except rx.DAGHasCycle as e:
            raise AssertionError("Graph contains cycles") from e

        # Create lookup
        fp_dict = flowpaths.set_index("fp_id").to_dict("index")

        # Create node_indices mapping
        node_indices = {}
        for node_idx in graph.node_indices():
            fp_id = graph[node_idx]
            node_indices[fp_id] = node_idx

        # Verify accumulation for each node
        for node_idx in topo_order:
            fp_id = graph[node_idx]
            fp_data = fp_dict[fp_id]

            # Get upstream total drainage areas
            in_edges = graph.in_edges(node_idx)
            upstream_total = sum(fp_dict[graph[src_idx]]["total_da_sqkm"] for src_idx, _, _ in in_edges)

            expected_total = upstream_total + fp_data["area_sqkm"]
            actual_total = fp_data["total_da_sqkm"]

            assert abs(expected_total - actual_total) < 0.001, (
                f"Drainage area mismatch for fp_id={fp_id}: "
                f"expected {expected_total:.3f}, got {actual_total:.3f}"
            )

    def _verify_path_length_logic(self, graph: rx.PyDiGraph, flowpaths: pd.DataFrame) -> None:
        """Verify path_length = downstream_path_length + downstream_length_km.

        Outlets should have path_length = 0.
        """
        fp_dict = flowpaths.set_index("fp_id").to_dict("index")

        for node_idx in graph.node_indices():
            fp_id = graph[node_idx]
            fp_data = fp_dict[fp_id]

            out_edges = list(graph.out_edges(node_idx))

            if not out_edges:
                # Outlet - should have path_length = 0
                assert fp_data["path_length"] == 0.0, (
                    f"Outlet fp_id={fp_id} should have path_length=0, got {fp_data['path_length']}"
                )
            else:
                # Should have exactly one downstream (dendritic)
                assert len(out_edges) == 1, f"fp_id={fp_id} has {len(out_edges)} downstream connections"

                _, downstream_idx, _ = out_edges[0]
                dn_flowpath_id = graph[downstream_idx]
                downstream_data = fp_dict[dn_flowpath_id]

                expected_path = downstream_data["path_length"] + downstream_data["length_km"]
                actual_path = fp_data["path_length"]

                assert abs(expected_path - actual_path) < 0.001, (
                    f"Path length mismatch for fp_id={fp_id}: "
                    f"expected {expected_path:.3f} (downstream_path={downstream_data['path_length']:.3f} + "
                    f"downstream_length={downstream_data['length_km']:.3f}), got {actual_path:.3f}"
                )

    def _verify_downstream_hydroseq(self, graph: rx.PyDiGraph, flowpaths: pd.DataFrame) -> None:
        """Verify dn_hydroseq points to downstream node's hydroseq (0 for outlets)."""
        fp_dict = flowpaths.set_index("fp_id").to_dict("index")

        for node_idx in graph.node_indices():
            fp_id = graph[node_idx]
            fp_data = fp_dict[fp_id]

            out_edges = list(graph.out_edges(node_idx))

            if not out_edges:
                # Outlet - dn_hydroseq should be 0
                assert fp_data["dn_hydroseq"] == 0, (
                    f"Outlet fp_id={fp_id} should have dn_hydroseq=0, got {fp_data['dn_hydroseq']}"
                )
            else:
                # Should point to downstream's hydroseq
                _, downstream_idx, _ = out_edges[0]
                dn_flowpath_id = graph[downstream_idx]
                downstream_data = fp_dict[dn_flowpath_id]

                expected_dn_hydroseq = downstream_data["hydroseq"]
                actual_dn_hydroseq = fp_data["dn_hydroseq"]

                assert expected_dn_hydroseq == actual_dn_hydroseq, (
                    f"dn_hydroseq mismatch for fp_id={fp_id}: "
                    f"expected {expected_dn_hydroseq} (downstream hydroseq), got {actual_dn_hydroseq}"
                )

    def _verify_mainstem_tracing(self, graph: rx.PyDiGraph, flowpaths: pd.DataFrame) -> None:
        """Verify mainstems are properly traced and follow longest paths."""
        fp_dict = flowpaths.set_index("fp_id").to_dict("index")

        # Get all unique mainstem IDs
        mainstem_ids = flowpaths["mainstem_lp"].unique()

        # Should have at least one mainstem
        assert len(mainstem_ids) > 0, "Should have at least one mainstem"

        # Each mainstem should form a connected path
        for mainstem_id in mainstem_ids:
            mainstem_fps = flowpaths[flowpaths["mainstem_lp"] == mainstem_id]

            # Should have at least one flowpath
            assert len(mainstem_fps) > 0, f"Mainstem {mainstem_id} has no flowpaths"

            # Verify connectivity - flowpaths on same mainstem should form connected paths
            # Get all node indices for this mainstem
            mainstem_nodes = [
                idx for idx in graph.node_indices() if graph[idx] in mainstem_fps["fp_id"].values
            ]

            # At least one should be reachable from any other via downstream traversal
            # (they form a connected tree structure)
            for node_idx in mainstem_nodes:
                # Follow downstream until we hit outlet or leave this mainstem
                current = node_idx
                visited_mainstem_nodes = {current}

                while True:
                    out_edges = list(graph.out_edges(current))
                    if not out_edges:
                        break  # Reached outlet

                    _, downstream_idx, _ = out_edges[0]
                    dn_flowpath_id = graph[downstream_idx]
                    downstream_mainstem = fp_dict[dn_flowpath_id]["mainstem_lp"]

                    if downstream_mainstem == mainstem_id:
                        visited_mainstem_nodes.add(downstream_idx)
                        current = downstream_idx
                    else:
                        break  # Left this mainstem

        # Verify mainstem follows longest path at confluences
        confluence_count = 0
        for node_idx in graph.node_indices():
            in_edges = list(graph.in_edges(node_idx))

            if len(in_edges) > 1:
                # This is a confluence
                confluence_count += 1
                fp_id = graph[node_idx]
                current_mainstem = fp_dict[fp_id]["mainstem_lp"]

                # Get path lengths of all upstream segments
                upstream_paths = [
                    (
                        graph[src_idx],
                        fp_dict[graph[src_idx]]["path_length"],
                        fp_dict[graph[src_idx]]["mainstem_lp"],
                    )
                    for src_idx, _, _ in in_edges
                ]

                # The upstream with longest path should determine the mainstem
                longest_upstream_id, longest_path, longest_mainstem = max(upstream_paths, key=lambda x: x[1])

                # Either:
                # 1. The longest upstream continues the current mainstem (longest_mainstem == current_mainstem)
                # 2. OR the current node is where the longest tributary joins (forming new mainstem)
                # The key is: the upstream with LONGEST path should influence mainstem assignment

                # Verify that if all upstreams are on different mainstems, we made a choice
                upstream_mainstems = [ms for _, _, ms in upstream_paths]

                if len(set(upstream_mainstems)) > 1:
                    # Multiple mainstems joining - verify the longest one continues or current is new
                    assert (
                        current_mainstem == longest_mainstem or current_mainstem not in upstream_mainstems
                    ), (
                        f"At confluence fp_id={fp_id}, current mainstem {current_mainstem} doesn't match "
                        f"longest upstream mainstem {longest_mainstem} (path={longest_path}), and current "
                        f"is not a new mainstem. Upstream mainstems: {upstream_mainstems}"
                    )

        # Verify we actually tested confluence logic (should have at least 1 confluence)
        # unless it's a completely linear network
        if len(flowpaths) > 3:
            assert confluence_count > 0, (
                f"Expected at least one confluence in network of {len(flowpaths)} flowpaths, found none"
            )
