"""Tests for graph building and attribute tracing."""

import geopandas as gpd
import polars as pl
import pytest
from shapely.geometry import LineString, Point

from hydrofabric_builds.hydrofabric.graph import (
    _build_rustworkx_object,
    _build_upstream_dict_from_nexus,
    _partition_all_outlet_subgraphs,
)
from hydrofabric_builds.hydrofabric.trace import _trace_single_flowpath_attributes


class TestTraceFlowpathAttributes:
    """Tests for _trace_single_flowpath_attributes function."""

    @pytest.fixture
    def simple_linear_network(self) -> dict:
        """Create a simple 3-flowpath linear network for testing."""
        # Create flowpaths: 1 -> 2 -> 3 (outlet)
        flowpaths = gpd.GeoDataFrame(
            {
                "fp_id": [1, 2, 3],
                "area_sqkm": [5.0, 3.0, 2.0],
                "length_km": [2.0, 1.5, 1.0],
                "up_nex_id": [10, 11, 12],
                "dn_nex_id": [11, 12, 13],
                "hydroseq": [30, 20, 10],  # Decreasing downstream
                "geometry": [LineString([(i, i), (i + 1, i + 1)]) for i in range(3)],
            },
            crs="EPSG:5070",
        )

        # Create nexus - only nexus 13 has no downstream (outlet)
        nexus = gpd.GeoDataFrame(
            {
                "nex_id": [10, 11, 12, 13],
                "dn_fp_id": [1, 2, 3, None],  # 13 is outlet (NaN downstream)
                "geometry": [Point(i, i) for i in range(4)],
            },
            crs="EPSG:5070",
        )

        # Convert to polars for processing
        fp_pl = pl.from_pandas(flowpaths.to_wkb())

        # Build graph structure
        upstream_dict = _build_upstream_dict_from_nexus(fp_pl)
        graph, node_indices = _build_rustworkx_object(upstream_dict)

        # Get outlet ID (where dn_nex_id has no corresponding up_nex_id)
        outlet_nexus_ids = set(nexus[nexus["dn_fp_id"].isna()]["nex_id"])
        outlet_fp_id = int(flowpaths[flowpaths["dn_nex_id"].isin(outlet_nexus_ids)]["fp_id"].iloc[0])

        # Partition subgraph for the outlet
        outlet_subgraphs = _partition_all_outlet_subgraphs(
            outlets=[outlet_fp_id],
            graph=graph,
            node_indices=node_indices,
            reference_flowpaths=fp_pl,
            reference_divides=None,
            _id="fp_id",
        )

        partition_data = outlet_subgraphs[outlet_fp_id]  # type: ignore[index]

        return {
            "outlet_fp_id": outlet_fp_id,
            "partition_data": partition_data,
            "flowpaths": flowpaths,
            "nexus": nexus,
        }

    @pytest.fixture
    def branching_network(self) -> dict:
        """Create a branching network with tributaries."""
        # Network structure:
        #     1 (order 1) \
        #     2 (order 1)  ->  4 (order 2)  ->  5 (order 2, outlet)
        #     3 (order 1) /
        flowpaths = gpd.GeoDataFrame(
            {
                "fp_id": [1, 2, 3, 4, 5],
                "area_sqkm": [2.0, 3.0, 1.5, 4.0, 5.0],
                "length_km": [1.0, 1.5, 0.8, 2.0, 2.5],
                "up_nex_id": [10, 11, 12, 13, 14],
                "dn_nex_id": [13, 13, 13, 14, 15],  # 1,2,3 all flow to nexus 13 (4's up_nex)
                "hydroseq": [50, 40, 45, 30, 10],  # 5 is outlet (lowest)
                "geometry": [LineString([(i, i), (i + 1, i + 1)]) for i in range(5)],
            },
            crs="EPSG:5070",
        )

        nexus = gpd.GeoDataFrame(
            {
                "nex_id": [10, 11, 12, 13, 14, 15],
                "dn_fp_id": [1, 2, 3, 4, 5, None],  # 15 is outlet
                "geometry": [Point(i, i) for i in range(6)],
            },
            crs="EPSG:5070",
        )

        # Convert to polars for processing
        fp_pl = pl.from_pandas(flowpaths.to_wkb())

        # Build graph structure
        upstream_dict = _build_upstream_dict_from_nexus(fp_pl)
        graph, node_indices = _build_rustworkx_object(upstream_dict)

        # Get outlet ID
        outlet_nexus_ids = set(nexus[nexus["dn_fp_id"].isna()]["nex_id"])
        outlet_fp_id = int(flowpaths[flowpaths["dn_nex_id"].isin(outlet_nexus_ids)]["fp_id"].iloc[0])

        # Partition subgraph for the outlet
        outlet_subgraphs = _partition_all_outlet_subgraphs(
            outlets=[outlet_fp_id],
            graph=graph,
            node_indices=node_indices,
            reference_flowpaths=fp_pl,
            reference_divides=None,
            _id="fp_id",
        )

        partition_data = outlet_subgraphs[outlet_fp_id]  # type: ignore[index]

        return {
            "outlet_fp_id": outlet_fp_id,
            "partition_data": partition_data,
            "flowpaths": flowpaths,
            "nexus": nexus,
        }

    def test_total_drainage_area_accumulation(self, simple_linear_network: dict) -> None:
        """Test that total drainage area accumulates correctly upstream to downstream."""
        outlet_fp_id = simple_linear_network["outlet_fp_id"]
        partition_data = simple_linear_network["partition_data"]

        result, next_mainstem_id, next_hydroseq = _trace_single_flowpath_attributes(
            outlet_fp_id=outlet_fp_id, partition_data=partition_data, id_offset=0, hydroseq_offset=1
        )

        # Verify next values are correct
        assert next_mainstem_id > 0, "next_mainstem_id should be incremented"
        assert next_hydroseq == 4, f"next_hydroseq should be 4 (1 + 3 flowpaths), got {next_hydroseq}"

        # Convert result to pandas
        result_pd = result.to_pandas()
        result_pd["fp_id"] = result_pd["fp_id"].astype("Int64")

        # Expected cumulative drainage areas:
        # fp_id 1 (most upstream): 5.0 (only its own)
        # fp_id 2: 5.0 + 3.0 = 8.0
        # fp_id 3 (outlet): 8.0 + 2.0 = 10.0
        fp1_da = result_pd[result_pd["fp_id"] == 1]["total_da_sqkm"].iloc[0]
        fp2_da = result_pd[result_pd["fp_id"] == 2]["total_da_sqkm"].iloc[0]
        fp3_da = result_pd[result_pd["fp_id"] == 3]["total_da_sqkm"].iloc[0]

        assert fp1_da == 5.0, f"Headwater (fp1) should have only its own area: expected 5.0, got {fp1_da}"
        assert fp2_da == 8.0, f"Middle (fp2) should have 5+3=8: expected 8.0, got {fp2_da}"
        assert fp3_da == 10.0, f"Outlet (fp3) should have total 10: expected 10.0, got {fp3_da}"

        # Verify values increase monotonically downstream (when sorted by hydroseq from result)
        result_sorted = result_pd.sort_values("hydroseq", ascending=True)  # Lowest (outlet) first
        drainage_areas = result_sorted["total_da_sqkm"].values

        # Each downstream segment should have >= drainage area than upstream
        # Since we sorted by hydroseq ascending (outlet first), drainage should decrease
        for i in range(len(drainage_areas) - 1):
            assert drainage_areas[i] >= drainage_areas[i + 1], (
                f"Drainage area should decrease going upstream (sorted by hydroseq asc): "
                f"position {i} has {drainage_areas[i]}, position {i + 1} has {drainage_areas[i + 1]}"
            )

    def test_path_length_calculation(self, simple_linear_network: dict) -> None:
        """Test that path_length = downstream_path + downstream_length."""
        outlet_fp_id = simple_linear_network["outlet_fp_id"]
        partition_data = simple_linear_network["partition_data"]

        result, next_mainstem_id, next_hydroseq = _trace_single_flowpath_attributes(
            outlet_fp_id=outlet_fp_id, partition_data=partition_data, id_offset=0, hydroseq_offset=1
        )

        # Convert result to pandas for easier testing
        result_pd = result.to_pandas()
        result_pd["fp_id"] = result_pd["fp_id"].astype("Int64")
        # Expected path lengths (distance to outlet):
        # fp_id 3 (outlet): 0.0
        # fp_id 2: 0.0 + 1.0 (fp3's length) = 1.0
        # fp_id 1: 1.0 + 1.5 (fp2's length) = 2.5
        fp1_path = result_pd[result_pd["fp_id"] == 1]["path_length"].iloc[0]
        fp2_path = result_pd[result_pd["fp_id"] == 2]["path_length"].iloc[0]
        fp3_path = result_pd[result_pd["fp_id"] == 3]["path_length"].iloc[0]

        assert fp3_path == 0.0, f"Outlet path_length should be 0: {fp3_path}"
        assert fp2_path == 1.0, f"Expected 1.0, got {fp2_path}"
        assert fp1_path == 2.5, f"Expected 2.5, got {fp1_path}"

    def test_downstream_hydroseq(self, simple_linear_network: dict) -> None:
        """Test that dn_hydroseq correctly points to downstream hydroseq."""
        outlet_fp_id = simple_linear_network["outlet_fp_id"]
        partition_data = simple_linear_network["partition_data"]

        result, next_mainstem_id, next_hydroseq = _trace_single_flowpath_attributes(
            outlet_fp_id=outlet_fp_id, partition_data=partition_data, id_offset=0, hydroseq_offset=1
        )

        # Convert result to pandas for easier testing
        result_pd = result.to_pandas()
        result_pd["fp_id"] = result_pd["fp_id"].astype("Int64")

        # Get the new hydroseq values assigned by the function
        # Outlet (fp_id 3) should have hydroseq=1 (first assigned)
        # Middle (fp_id 2) should have hydroseq=2
        # Headwater (fp_id 1) should have hydroseq=3
        fp3_hydroseq = result_pd[result_pd["fp_id"] == 3]["hydroseq"].iloc[0]
        fp2_hydroseq = result_pd[result_pd["fp_id"] == 2]["hydroseq"].iloc[0]

        # Expected dn_hydroseq:
        # fp_id 1 -> fp_id 2
        # fp_id 2 -> fp_id 3
        # fp_id 3 -> outlet (dn_hydroseq=0)
        fp1_dn = result_pd[result_pd["fp_id"] == 1]["dn_hydroseq"].iloc[0]
        fp2_dn = result_pd[result_pd["fp_id"] == 2]["dn_hydroseq"].iloc[0]
        fp3_dn = result_pd[result_pd["fp_id"] == 3]["dn_hydroseq"].iloc[0]

        assert fp1_dn == fp2_hydroseq, f"fp1 should point to fp2's hydroseq ({fp2_hydroseq}): {fp1_dn}"
        assert fp2_dn == fp3_hydroseq, f"fp2 should point to fp3's hydroseq ({fp3_hydroseq}): {fp2_dn}"
        assert fp3_dn == 0, f"Outlet dn_hydroseq should be 0: {fp3_dn}"

    def test_hydroseq_decreases_downstream(self, simple_linear_network: dict) -> None:
        """Test that hydroseq values increase from outlet to headwater."""
        outlet_fp_id = simple_linear_network["outlet_fp_id"]
        partition_data = simple_linear_network["partition_data"]

        result, next_mainstem_id, next_hydroseq = _trace_single_flowpath_attributes(
            outlet_fp_id=outlet_fp_id, partition_data=partition_data, id_offset=0, hydroseq_offset=1
        )

        result_pd = result.to_pandas()
        result_pd["fp_id"] = result_pd["fp_id"].astype("Int64")

        # Get hydroseq values for each flowpath
        fp1_hydroseq = result_pd[result_pd["fp_id"] == 1]["hydroseq"].iloc[0]
        fp2_hydroseq = result_pd[result_pd["fp_id"] == 2]["hydroseq"].iloc[0]
        fp3_hydroseq = result_pd[result_pd["fp_id"] == 3]["hydroseq"].iloc[0]

        # Hydroseq should increase going upstream
        assert fp3_hydroseq < fp2_hydroseq < fp1_hydroseq, (
            f"Hydroseq should increase upstream: outlet={fp3_hydroseq}, "
            f"middle={fp2_hydroseq}, headwater={fp1_hydroseq}"
        )

        # Outlet should have hydroseq=1 (since we started with offset=1)
        assert fp3_hydroseq == 1, f"Outlet should have hydroseq=1, got {fp3_hydroseq}"

    def test_mainstem_identification(self, branching_network: dict) -> None:
        """Test that mainstems are correctly identified based on longest path."""
        outlet_fp_id = branching_network["outlet_fp_id"]
        partition_data = branching_network["partition_data"]

        result, next_mainstem_id, next_hydroseq = _trace_single_flowpath_attributes(
            outlet_fp_id=outlet_fp_id, partition_data=partition_data, id_offset=0, hydroseq_offset=1
        )

        # Convert result to pandas for easier testing
        result_pd = result.to_pandas()
        result_pd["fp_id"] = result_pd["fp_id"].astype("Int64")
        # Get mainstem IDs
        mainstem_ids = result_pd["mainstem_lp"].unique()

        # Should have multiple mainstems (main + tributaries)
        assert len(mainstem_ids) > 1, "Should have multiple mainstem IDs"

        # The main mainstem should include the outlet
        outlet_mainstem = result_pd[result_pd["fp_id"] == 5]["mainstem_lp"].iloc[0]

        # Flowpaths on the main mainstem should have the same ID
        main_mainstem_fps = result_pd[result_pd["mainstem_lp"] == outlet_mainstem]
        assert len(main_mainstem_fps) >= 2, "Main mainstem should have multiple flowpaths"

    def test_mainstem_uses_longest_path(self, branching_network: dict) -> None:
        """Test that mainstem follows the longest path (by path_length)."""
        outlet_fp_id = branching_network["outlet_fp_id"]
        partition_data = branching_network["partition_data"]

        result, next_mainstem_id, next_hydroseq = _trace_single_flowpath_attributes(
            outlet_fp_id=outlet_fp_id, partition_data=partition_data, id_offset=0, hydroseq_offset=1
        )

        # Convert result to pandas for easier testing
        result_pd = result.to_pandas()
        result_pd["fp_id"] = result_pd["fp_id"].astype("Int64")
        # Get the outlet's mainstem ID
        outlet_mainstem = result_pd[result_pd["fp_id"] == 5]["mainstem_lp"].iloc[0]

        # Get all flowpaths on this mainstem
        main_fps = result_pd[result_pd["mainstem_lp"] == outlet_mainstem].sort_values(
            "path_length", ascending=False
        )

        # The mainstem should be traced by longest path
        # Verify it goes through fp4 (the confluence point) and fp5 (outlet)
        assert 4 in main_fps["fp_id"].values, "Mainstem should go through confluence (fp4)"
        assert 5 in main_fps["fp_id"].values, "Mainstem should include outlet (fp5)"

    def test_hydroseq_offset_increments(self, branching_network: dict) -> None:
        """Test that hydroseq_offset is properly incremented and returned."""
        outlet_fp_id = branching_network["outlet_fp_id"]
        partition_data = branching_network["partition_data"]

        # Start with offset of 100
        result, next_mainstem_id, next_hydroseq = _trace_single_flowpath_attributes(
            outlet_fp_id=outlet_fp_id, partition_data=partition_data, id_offset=0, hydroseq_offset=100
        )

        result_pd = result.to_pandas()

        # Should have 5 flowpaths, so next_hydroseq should be 105
        assert next_hydroseq == 105, f"Expected next_hydroseq=105, got {next_hydroseq}"

        # All hydroseq values should be >= 100
        min_hydroseq = result_pd["hydroseq"].min()
        max_hydroseq = result_pd["hydroseq"].max()

        assert min_hydroseq >= 100, f"Minimum hydroseq should be >= 100, got {min_hydroseq}"
        assert max_hydroseq < 105, f"Maximum hydroseq should be < 105, got {max_hydroseq}"
