"""Tests for graph partitioning functionality."""

import geopandas as gpd
import polars as pl

from hydrofabric_builds.hydrofabric.graph import (
    _build_graph,
    _build_rustworkx_object,
    _find_outlets_by_hydroseq,
    _partition_all_outlet_subgraphs,
)


def test_partition_contains_correct_flowpath_ids(
    sample_flowpaths: gpd.GeoDataFrame, sample_divides: gpd.GeoDataFrame
) -> None:
    """Test that partitions contain the correct flowpath IDs."""
    # Convert GeoDataFrames to Polars DataFrames (this is what the actual pipeline does)
    fp_pl = pl.from_pandas(sample_flowpaths.to_wkb())
    div_pl = pl.from_pandas(sample_divides.to_wkb())

    # Build graph
    upstream_dict = _build_graph(fp_pl)
    digraph, node_indices = _build_rustworkx_object(upstream_dict)
    outlets = _find_outlets_by_hydroseq(fp_pl)

    # Partition
    partitions = _partition_all_outlet_subgraphs(
        outlets,
        digraph,
        node_indices,
        fp_pl,
        div_pl,
    )

    # Each partition should exist
    assert len(partitions) > 0, "No partitions created"

    for outlet, partition in partitions.items():
        # Check partition structure
        assert "subgraph" in partition, f"Outlet {outlet} missing subgraph"
        assert "node_indices" in partition, f"Outlet {outlet} missing node_indices"
        assert "flowpaths" in partition, f"Outlet {outlet} missing flowpaths"
        assert "divides" in partition, f"Outlet {outlet} missing divides"

        # Get IDs from subgraph
        subgraph = partition["subgraph"]
        subgraph_ids = {str(subgraph.get_node_data(i)) for i in range(len(subgraph))}

        # Get IDs from filtered flowpaths
        flowpath_ids = set(partition["flowpaths"]["flowpath_id"].to_list())

        print(f"\nOutlet {outlet}:")
        print(f"  Subgraph IDs: {subgraph_ids}")
        print(f"  Flowpath IDs: {flowpath_ids}")
        print(f"  Missing from flowpaths: {subgraph_ids - flowpath_ids}")
        print(f"  Extra in flowpaths: {flowpath_ids - subgraph_ids}")

        # Flowpath IDs should match subgraph IDs
        assert subgraph_ids == flowpath_ids, (
            f"Outlet {outlet}: Subgraph IDs {subgraph_ids} don't match filtered flowpath IDs {flowpath_ids}"
        )


def test_partition_flowpaths_not_empty(
    sample_flowpaths: gpd.GeoDataFrame, sample_divides: gpd.GeoDataFrame
) -> None:
    """Test that partitions contain non-empty flowpaths dataframes."""
    fp_pl = pl.from_pandas(sample_flowpaths.to_wkb())
    div_pl = pl.from_pandas(sample_divides.to_wkb())

    upstream_dict = _build_graph(fp_pl)
    digraph, node_indices = _build_rustworkx_object(upstream_dict)
    outlets = _find_outlets_by_hydroseq(fp_pl)

    partitions = _partition_all_outlet_subgraphs(
        outlets,
        digraph,
        node_indices,
        fp_pl,
        div_pl,
    )

    for outlet, partition in partitions.items():
        flowpaths = partition["flowpaths"]
        subgraph = partition["subgraph"]

        print(f"\nOutlet {outlet}:")
        print(f"  Subgraph nodes: {len(subgraph)}")
        print(f"  Flowpaths rows: {flowpaths.height}")

        # Print first few node IDs from subgraph
        node_ids = [str(subgraph.get_node_data(i)) for i in range(min(5, len(subgraph)))]
        print(f"  Sample subgraph node IDs: {node_ids}")

        # Print flowpath IDs from partition
        if flowpaths.height > 0:
            fp_ids = flowpaths["flowpath_id"].head(5).to_list()
            print(f"  Sample flowpath IDs: {fp_ids}")

        assert flowpaths.height > 0, (
            f"Outlet {outlet} has empty flowpaths dataframe. Subgraph has {len(subgraph)} nodes."
        )


def test_partition_outlet_included_in_flowpaths(
    sample_flowpaths: gpd.GeoDataFrame, sample_divides: gpd.GeoDataFrame
) -> None:
    """Test that each outlet ID is included in its own partition's flowpaths."""
    fp_pl = pl.from_pandas(sample_flowpaths.to_wkb())
    div_pl = pl.from_pandas(sample_divides.to_wkb())

    upstream_dict = _build_graph(fp_pl)
    digraph, node_indices = _build_rustworkx_object(upstream_dict)
    outlets = _find_outlets_by_hydroseq(fp_pl)

    partitions = _partition_all_outlet_subgraphs(
        outlets,
        digraph,
        node_indices,
        fp_pl,
        div_pl,
    )

    for outlet, partition in partitions.items():
        flowpath_ids = set(partition["flowpaths"]["flowpath_id"].to_list())

        print(f"\nOutlet {outlet}:")
        print(f"  Outlet in flowpaths: {outlet in flowpath_ids}")
        print(f"  Flowpath IDs: {flowpath_ids}")

        assert outlet in flowpath_ids, (
            f"Outlet {outlet} not found in its own partition's flowpaths. Available IDs: {flowpath_ids}"
        )


def test_partition_type_consistency(
    sample_flowpaths: gpd.GeoDataFrame, sample_divides: gpd.GeoDataFrame
) -> None:
    """Test that all IDs are strings throughout the partition."""
    fp_pl = pl.from_pandas(sample_flowpaths.to_wkb())
    div_pl = pl.from_pandas(sample_divides.to_wkb())

    upstream_dict = _build_graph(fp_pl)
    digraph, node_indices = _build_rustworkx_object(upstream_dict)
    outlets = _find_outlets_by_hydroseq(fp_pl)

    partitions = _partition_all_outlet_subgraphs(
        outlets,
        digraph,
        node_indices,
        fp_pl,
        div_pl,
    )

    for outlet, partition in partitions.items():
        print(f"\nOutlet {outlet}:")
        print(f"  Outlet type: {type(outlet)}")

        # Check outlet ID is string
        assert isinstance(outlet, str), f"Outlet ID {outlet} is not a string (type: {type(outlet)})"

        # Check subgraph node data are strings
        subgraph = partition["subgraph"]
        for i in range(min(3, len(subgraph))):  # Check first 3
            node_data = subgraph.get_node_data(i)
            print(f"  Node {i} data: {node_data} (type: {type(node_data)})")
            assert isinstance(node_data, str), (
                f"Outlet {outlet}: Node {i} has non-string data: {node_data} (type: {type(node_data)})"
            )

        # Check node_indices keys are strings
        for flowpath_id in list(partition["node_indices"].keys())[:3]:  # Check first 3
            assert isinstance(flowpath_id, str), (
                f"Outlet {outlet}: node_indices key {flowpath_id} is not a string (type: {type(flowpath_id)})"
            )

        # Check flowpath IDs are strings
        flowpath_ids = partition["flowpaths"]["flowpath_id"].to_list()
        for fid in flowpath_ids[:3]:  # Check first few
            print(f"  Flowpath ID: {fid} (type: {type(fid)})")
            assert isinstance(fid, str), (
                f"Outlet {outlet}: flowpath_id {fid} is not a string (type: {type(fid)})"
            )

        # Check divide IDs are strings
        divide_ids = partition["divides"]["divide_id"].to_list()
        for did in divide_ids[:3]:  # Check first few
            assert isinstance(did, str), (
                f"Outlet {outlet}: divide_id {did} is not a string (type: {type(did)})"
            )


def test_node_indices_mapping(sample_flowpaths: gpd.GeoDataFrame, sample_divides: gpd.GeoDataFrame) -> None:
    """Test that node_indices correctly maps flowpath IDs to graph nodes."""
    fp_pl = pl.from_pandas(sample_flowpaths.to_wkb())
    div_pl = pl.from_pandas(sample_divides.to_wkb())

    upstream_dict = _build_graph(fp_pl)
    digraph, node_indices = _build_rustworkx_object(upstream_dict)
    outlets = _find_outlets_by_hydroseq(fp_pl)

    partitions = _partition_all_outlet_subgraphs(
        outlets,
        digraph,
        node_indices,
        fp_pl,
        div_pl,
    )

    for outlet, partition in partitions.items():
        subgraph = partition["subgraph"]
        sub_indices = partition["node_indices"]

        print(f"\nOutlet {outlet}:")
        print(f"  Subgraph size: {len(subgraph)}")
        print(f"  Node indices size: {len(sub_indices)}")

        # Check first few node_indices
        for flowpath_id, node_idx in list(sub_indices.items())[:5]:
            assert 0 <= node_idx < len(subgraph), (
                f"Outlet {outlet}: Invalid node index {node_idx} for flowpath {flowpath_id}. "
                f"Subgraph has {len(subgraph)} nodes."
            )

            # The node at that index should have the correct data
            node_data = str(subgraph.get_node_data(node_idx))
            assert node_data == str(flowpath_id), (
                f"Outlet {outlet}: Node at index {node_idx} has data '{node_data}' "
                f"but expected '{flowpath_id}'"
            )


def test_graph_node_storage_types(sample_flowpaths: gpd.GeoDataFrame) -> None:
    """Test what types are actually stored in the graph nodes."""
    fp_pl = pl.from_pandas(sample_flowpaths.drop(columns=["geometry"]))

    upstream_dict = _build_graph(fp_pl)
    digraph, node_indices = _build_rustworkx_object(upstream_dict)

    print("\nGraph node analysis:")
    print(f"Total nodes: {len(digraph)}")

    # Check first 10 nodes
    for i in range(min(10, len(digraph))):
        node_data = digraph.get_node_data(i)
        print(f"Node {i}: data='{node_data}' type={type(node_data)}")

    # Check node_indices keys
    print("\nNode indices analysis:")
    for fp_id, idx in list(node_indices.items())[:10]:
        print(f"  '{fp_id}' (type: {type(fp_id)}) -> index {idx}")


def test_flowpath_dataframe_types(sample_flowpaths: gpd.GeoDataFrame) -> None:
    """Test what types are in the flowpath_id column."""
    fp_pl = pl.from_pandas(sample_flowpaths.drop(columns=["geometry"]))

    print("\nFlowpath DataFrame analysis:")
    print(f"Total flowpaths: {fp_pl.height}")
    print(f"flowpath_id dtype: {fp_pl['flowpath_id'].dtype}")

    # Check first 10 values
    for i, fp_id in enumerate(fp_pl["flowpath_id"].head(10).to_list()):
        print(f"Row {i}: flowpath_id='{fp_id}' type={type(fp_id)}")
