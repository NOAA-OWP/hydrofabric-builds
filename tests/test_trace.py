"""Test cases to ensure functionality works for the trace functions"""

from typing import Any

import geopandas as gpd
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


def _check_virtual_flowpath_area_contributions(
    virtual_fp_pl: pl.DataFrame,
    reference_fp_pl: pl.DataFrame | None = None,
) -> None:
    """Check that virtual flowpath area contributions are valid.

    Each percentage must be in [0, 1], and per-divide sums must be close to 1.0.
    Zero percentage should only occur for flowpaths with zero area (no-divide).

    Parameters
    ----------
    virtual_fp_pl : pl.DataFrame
        Virtual flowpath dataframe with percentage_area_contribution
    reference_fp_pl : pl.DataFrame, optional
        Reference flowpath table used to derive div_id for each VFP
    """
    pct = virtual_fp_pl["percentage_area_contribution"]

    # No nulls
    assert not pct.is_null().any(), "Found null percentage_area_contribution values"

    # Each percentage in [0, 1]
    assert (pct >= 0).all(), "Found negative percentage_area_contribution"
    assert (pct <= 1.0).all(), "Found percentage_area_contribution > 1.0"

    # Zero percentage iff zero area (no-divide flowpaths)
    area = virtual_fp_pl["area_sqkm"]
    zero_pct_ids = set(virtual_fp_pl.filter(pct == 0)["virtual_fp_id"].to_list())
    zero_area_ids = set(virtual_fp_pl.filter(area == 0)["virtual_fp_id"].to_list())
    assert zero_pct_ids == zero_area_ids, (
        "Mismatch between zero-percentage and zero-area virtual flowpaths: "
        f"zero pct only: {zero_pct_ids - zero_area_ids}, zero area only: {zero_area_ids - zero_pct_ids}"
    )

    # Per-divide sums must be close to 1.0
    # Join div_id from reference_flowpaths via virtual_fp_id
    if reference_fp_pl is not None:
        vfp_div = (
            reference_fp_pl.filter(pl.col("virtual_fp_id").is_not_null())
            .select("virtual_fp_id", "div_id")
            .unique(subset=["virtual_fp_id"])
        )
        vfp_with_div = virtual_fp_pl.join(vfp_div, on="virtual_fp_id", how="left")
    else:
        vfp_with_div = virtual_fp_pl

    per_div = (
        vfp_with_div.filter(pl.col("div_id").is_not_null())
        .group_by("div_id")
        .agg(pl.col("percentage_area_contribution").sum().alias("total_pct"))
    )

    tolerance = 0.01
    over = per_div.filter(pl.col("total_pct") > 1.0 + tolerance)
    if len(over) > 0:
        raise AssertionError(
            f"Found {len(over)} divides where virtual flowpath area percentages exceed 1.0: "
            f"{over.sort('total_pct', descending=True).head(5)}"
        )

    under = per_div.filter(pl.col("total_pct") < 1.0 - tolerance)
    if len(under) > 0:
        raise AssertionError(
            f"Found {len(under)} divides where virtual flowpath area percentages sum to less than 1.0: "
            f"{under.sort('total_pct').head(5)}"
        )


def _check_no_coincident_nexuses(
    nex_gdf: gpd.GeoDataFrame,
    virtual_nex_gdf: gpd.GeoDataFrame | None = None,
    virtual_fp_gdf: gpd.GeoDataFrame | None = None,
) -> None:
    """Check that no two nexuses share the same location.

    Virtual nexuses may be coincident when merging them would create a cycle
    (i.e. a VFP uses one as up_nex and the other as dn_nex).

    Parameters
    ----------
    nex_gdf : gpd.GeoDataFrame
        Regular nexus GeoDataFrame
    virtual_nex_gdf : gpd.GeoDataFrame, optional
        Virtual nexus GeoDataFrame
    virtual_fp_gdf : gpd.GeoDataFrame, optional
        Virtual flowpaths GeoDataFrame (used to determine allowed coincident pairs)
    """
    # Regular nexuses must never be coincident
    if nex_gdf is not None and len(nex_gdf) > 0:
        coords = nex_gdf.geometry.apply(lambda g: (round(g.x, 6), round(g.y, 6)))
        dupes = coords.duplicated(keep=False)
        n_dupes = dupes.sum()
        if n_dupes > 0:
            dup_ids = nex_gdf.loc[dupes, "nex_id"].tolist()
            raise AssertionError(f"Found {n_dupes} coincident nexus records. IDs: {dup_ids[:20]}")

    # Virtual nexuses may be coincident when merging them would create a
    # cycle (up_nex == dn_nex on some VFP) or a divergence (two VFPs sharing
    # the same up_nex). All other coincident pairs are errors.
    if virtual_nex_gdf is not None and len(virtual_nex_gdf) > 0:
        vcoords = virtual_nex_gdf.geometry.apply(lambda g: (round(g.x, 6), round(g.y, 6)))
        vdupes = vcoords.duplicated(keep=False)
        if vdupes.any():
            # Build allowed pairs from VFP up/dn nexus references
            no_merge_pairs: set[frozenset[int]] = set()
            up_nex_ids: set[int] = set()
            if virtual_fp_gdf is not None:
                for _, row in virtual_fp_gdf.iterrows():
                    up = row.get("up_virtual_nex_id")
                    dn = row.get("dn_virtual_nex_id")
                    if pd.notna(up):
                        no_merge_pairs.add(frozenset((int(up), int(dn))))
                        up_nex_ids.add(int(up))

            dup_groups = virtual_nex_gdf[vdupes].groupby(vcoords[vdupes])["virtual_nex_id"].apply(list)
            bad_ids = []
            for nex_ids in dup_groups:
                for i, a in enumerate(nex_ids):
                    for b in nex_ids[i + 1 :]:
                        # Allowed: merging would create a cycle
                        if frozenset((a, b)) in no_merge_pairs:
                            continue
                        # Allowed: both are up_nex for different VFPs (divergence)
                        if a in up_nex_ids and b in up_nex_ids:
                            continue
                        bad_ids.extend([a, b])
            if bad_ids:
                raise AssertionError(
                    f"Found {len(bad_ids)} unexplained coincident virtual_nexus records. "
                    f"IDs: {sorted(set(bad_ids))[:20]}"
                )


def _check_nexus_relational_integrity(
    fp_pl: pl.DataFrame,
    nex_pl: pl.DataFrame,
    virtual_fp_pl: pl.DataFrame | None = None,
    virtual_nex_pl: pl.DataFrame | None = None,
    reference_fp_pl: pl.DataFrame | None = None,
) -> None:
    """Check relational integrity between nexuses and flowpaths.

    Validates:
    - Every nexus dn_fp_id points to a valid fp_id (or null for outlets)
    - Every flowpath dn_nex_id points to a valid nex_id
    - Every virtual nexus dn_virtual_fp_id points to a valid virtual_fp_id (or null for outlets)
    - Every virtual flowpath dn_virtual_nex_id points to a valid virtual_nex_id
    - Every VFP appears in at least one ref FP's virtual_fp_id
    """
    # Regular nexus -> flowpath
    valid_fp_ids = set(fp_pl["fp_id"].to_list())
    nex_refs = set(nex_pl["dn_fp_id"].drop_nulls().to_list())
    dangling_nex = nex_refs - valid_fp_ids
    assert len(dangling_nex) == 0, f"Nexus dn_fp_id references non-existent fp_ids: {dangling_nex}"

    # Flowpath -> nexus
    valid_nex_ids = set(nex_pl["nex_id"].to_list())
    fp_dn_refs = set(fp_pl["dn_nex_id"].drop_nulls().to_list())
    dangling_fp_dn = fp_dn_refs - valid_nex_ids
    assert len(dangling_fp_dn) == 0, f"Flowpath dn_nex_id references non-existent nex_ids: {dangling_fp_dn}"

    if virtual_fp_pl is None or virtual_nex_pl is None or len(virtual_nex_pl) == 0:
        return

    valid_vfp_ids = set(virtual_fp_pl["virtual_fp_id"].to_list())
    valid_vnex_ids = set(virtual_nex_pl["virtual_nex_id"].to_list())

    # Virtual nexus -> VFP
    vnex_refs = set(virtual_nex_pl["dn_virtual_fp_id"].drop_nulls().to_list())
    dangling_vnex = vnex_refs - valid_vfp_ids
    assert len(dangling_vnex) == 0, (
        f"Virtual nexus dn_virtual_fp_id references non-existent VFPs: {dangling_vnex}"
    )

    # VFP -> virtual nexus
    vfp_dn_refs = set(virtual_fp_pl["dn_virtual_nex_id"].drop_nulls().to_list())
    dangling_vfp_dn = vfp_dn_refs - valid_vnex_ids
    assert len(dangling_vfp_dn) == 0, (
        f"VFP dn_virtual_nex_id references non-existent vnex_ids: {dangling_vfp_dn}"
    )

    vfp_up_refs = set(virtual_fp_pl["up_virtual_nex_id"].drop_nulls().to_list())
    dangling_vfp_up = vfp_up_refs - valid_vnex_ids
    assert len(dangling_vfp_up) == 0, (
        f"VFP up_virtual_nex_id references non-existent vnex_ids: {dangling_vfp_up}"
    )

    # Every VFP has at least one ref FP row with virtual_fp_id pointing to it
    if reference_fp_pl is not None:
        covered_vfp_ids = set(reference_fp_pl["virtual_fp_id"].drop_nulls().to_list())
        orphan_vfps = valid_vfp_ids - covered_vfp_ids
        assert len(orphan_vfps) == 0, (
            f"Found {len(orphan_vfps)} VFPs with no ref FP row: {sorted(orphan_vfps)[:20]}"
        )

    # Every fp_id in the flowpaths table has at least one ref_fp_id in reference_flowpaths
    if reference_fp_pl is not None:
        ref_fp_ids = set(reference_fp_pl["fp_id"].drop_nulls().to_list())
        fps_without_ref = valid_fp_ids - ref_fp_ids
        assert len(fps_without_ref) == 0, (
            f"Found {len(fps_without_ref)} fp_ids with no ref FP entry: {sorted(fps_without_ref)[:20]}"
        )

    # segment_order must be non-null for all ref FP rows that have a virtual_fp_id
    if reference_fp_pl is not None and "segment_order" in reference_fp_pl.columns:
        vfp_rows = reference_fp_pl.filter(pl.col("virtual_fp_id").is_not_null())
        null_seg_order = vfp_rows.filter(pl.col("segment_order").is_null())
        assert len(null_seg_order) == 0, (
            f"Found {len(null_seg_order)} ref FP rows with virtual_fp_id but null segment_order"
        )

    # Flowpaths without up_nex_id should only be headwaters (no other fp drains to them)
    if "up_nex_id" in fp_pl.columns:
        downstream_targets = set(fp_pl["fp_to_id"].drop_nulls().to_list())
        no_up_nex = fp_pl.filter(pl.col("up_nex_id").is_null())
        non_headwater_no_up = [fid for fid in no_up_nex["fp_id"].to_list() if fid in downstream_targets]
        assert len(non_headwater_no_up) == 0, (
            f"Found {len(non_headwater_no_up)} non-headwater flowpaths with null up_nex_id: "
            f"{non_headwater_no_up[:20]}"
        )

    # VFPs without up_virtual_nex_id should only be headwaters (no nexus drains to them)
    if virtual_fp_pl is not None and virtual_nex_pl is not None:
        vnex_downstream_vfps = set(virtual_nex_pl["dn_virtual_fp_id"].drop_nulls().to_list())
        no_up_vnex = virtual_fp_pl.filter(pl.col("up_virtual_nex_id").is_null())
        non_headwater_no_up_vfp = [
            vid for vid in no_up_vnex["virtual_fp_id"].to_list() if vid in vnex_downstream_vfps
        ]
        assert len(non_headwater_no_up_vfp) == 0, (
            f"Found {len(non_headwater_no_up_vfp)} non-headwater VFPs with null up_virtual_nex_id: "
            f"{non_headwater_no_up_vfp[:20]}"
        )

    # All non-outlet nexuses must have a downstream flowpath
    # Every nexus referenced as dn_nex_id by a non-outlet flowpath should have dn_fp_id
    non_outlet_fps = fp_pl.filter(pl.col("fp_to_id").is_not_null())
    internal_nex_ids = set(non_outlet_fps["dn_nex_id"].drop_nulls().to_list())
    internal_nex_missing_dn = [
        nid
        for nid in internal_nex_ids
        if nid in set(nex_pl.filter(pl.col("dn_fp_id").is_null())["nex_id"].to_list())
    ]
    assert len(internal_nex_missing_dn) == 0, (
        f"Found {len(internal_nex_missing_dn)} internal nexuses without dn_fp_id: "
        f"{internal_nex_missing_dn[:20]}"
    )

    # Same for virtual nexuses: non-outlet vnexes must have dn_virtual_fp_id
    if virtual_nex_pl is not None and len(virtual_nex_pl) > 0:
        vnex_no_dn = virtual_nex_pl.filter(pl.col("dn_virtual_fp_id").is_null())
        # These should only be outlet virtual nexuses
        assert len(vnex_no_dn) >= 0, "Unexpected error"  # just for flow
        # Every vnex referenced as dn_virtual_nex_id by a non-terminal VFP should have dn_virtual_fp_id
        if virtual_fp_pl is not None:
            # A VFP is terminal if its dn_virtual_nex_id nexus has no dn_virtual_fp_id
            vnex_with_dn = set(
                virtual_nex_pl.filter(pl.col("dn_virtual_fp_id").is_not_null())["virtual_nex_id"].to_list()
            )
            non_terminal_vfps = virtual_fp_pl.filter(pl.col("dn_virtual_nex_id").is_in(list(vnex_with_dn)))
            internal_vnex_ids = set(non_terminal_vfps["dn_virtual_nex_id"].drop_nulls().to_list())
            vnex_null_dn_ids = set(vnex_no_dn["virtual_nex_id"].to_list())
            internal_vnex_missing = internal_vnex_ids & vnex_null_dn_ids
            assert len(internal_vnex_missing) == 0, (
                f"Found {len(internal_vnex_missing)} internal virtual nexuses without dn_virtual_fp_id: "
                f"{sorted(internal_vnex_missing)[:20]}"
            )


def _check_fp_to_id(fp_pl: pl.DataFrame) -> None:
    """Check that fp_to_id is present and points to valid fp_ids or null (for outlets)."""
    assert "fp_to_id" in fp_pl.columns, "fp_to_id column missing from flowpaths"
    valid_fp_ids = set(fp_pl["fp_id"].to_list())
    non_null = fp_pl.filter(pl.col("fp_to_id").is_not_null())
    invalid = [row for row in non_null["fp_to_id"].to_list() if row not in valid_fp_ids]
    assert len(invalid) == 0, f"fp_to_id references non-existent fp_ids: {invalid}"

    # At least one outlet should have null fp_to_id
    null_count = fp_pl.filter(pl.col("fp_to_id").is_null()).height
    assert null_count >= 1, "Expected at least one outlet flowpath with null fp_to_id"


def _check_virtual_nexus_meets_flowpath(
    virtual_nex_pl: pl.DataFrame,
    virtual_fp_pl: pl.DataFrame,
    reference_fp_pl: pl.DataFrame,
    fp_pl: pl.DataFrame,
) -> None:
    """Check that every virtual nexus connects back to a real flowpath.

    The crosswalk path is:
    Virtual Nexus -> Virtual Flowpath -> reference_flowpaths -> Flowpath.

    Parameters
    ----------
    virtual_nex_pl : pl.DataFrame
        Virtual nexus dataframe
    virtual_fp_pl : pl.DataFrame
        Virtual flowpath dataframe
    reference_fp_pl : pl.DataFrame
        Reference flowpath crosswalk table
    fp_pl : pl.DataFrame
        Flowpath dataframe
    """
    if len(virtual_nex_pl) == 0:
        return

    # Virtual Nexus -> Virtual Flowpath (via dn_virtual_nex_id or up_virtual_nex_id)
    # A nexus connects to a VFP either as its downstream end (dn_virtual_nex_id)
    # or its upstream end (up_virtual_nex_id, for headwater nexuses)
    via_dn = set(virtual_fp_pl["dn_virtual_nex_id"].drop_nulls().to_list())
    via_up = set(virtual_fp_pl["up_virtual_nex_id"].drop_nulls().to_list())
    connected = via_dn | via_up
    all_vnex = set(virtual_nex_pl["virtual_nex_id"].to_list())
    orphans = all_vnex - connected
    assert len(orphans) == 0, f"Found {len(orphans)} virtual nexuses with no connected virtual flowpath"

    # Use dn_virtual_nex_id for the crosswalk hop (most nexuses)
    hop1 = virtual_nex_pl.join(
        virtual_fp_pl.select("virtual_fp_id", "dn_virtual_nex_id"),
        left_on="virtual_nex_id",
        right_on="dn_virtual_nex_id",
        how="left",
    )

    # Virtual Flowpath -> reference_flowpaths (via virtual_fp_id); div_id links
    # to the regular flowpath the virtual chain connects to.
    # Tributary VFPs have reference entries; main-stem VFPs do not.
    # For nexuses that have a tributary VFP, verify the reference link is valid.
    ref_vfp = reference_fp_pl.filter(pl.col("virtual_fp_id").is_not_null()).select("virtual_fp_id", "div_id")
    hop2 = hop1.join(ref_vfp, on="virtual_fp_id", how="left")

    # div_id must exist in the flowpaths table (for VFPs that have reference entries)
    valid_fp_ids = set(fp_pl["fp_id"].to_list())
    matched_fp_ids = set(hop2["div_id"].drop_nulls().to_list())
    dangling = matched_fp_ids - valid_fp_ids
    assert len(dangling) == 0, (
        f"Found {len(dangling)} div_id values from terminal virtual nexus crosswalk not in flowpaths: {dangling}"
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

    expected_df = pd.read_csv(
        here() / "tests/data/trace_cases/no_divide_fp_upstream_most_reach_nexus.csv",
        dtype={"nex_id": "int64", "dn_fp_id": "float64", "vpu_id": "object"},
    )
    expected_df["dn_fp_id"] = expected_df["dn_fp_id"].astype("Int64")
    expected_df["nex_id"] = expected_df["nex_id"].astype("Int64")
    pd.testing.assert_frame_equal(final_nexus.drop(columns=["geometry"]), expected_df)

    expected_df = pd.read_csv(
        here() / "tests/data/trace_cases/no_divide_fp_upstream_most_reach_virtual_nexus.csv",
        dtype={"virtual_nex_id": "int64", "vpu_id": "object", "dn_virtual_fp_id": "Int64"},
    )
    expected_df["virtual_nex_id"] = expected_df["virtual_nex_id"].astype("Int64")
    pd.testing.assert_frame_equal(final_virtual_nexus.drop(columns=["geometry"]), expected_df)

    _check_hydroseq_decreases_downstream(fp_pl, graph, fp_id_col="fp_id")
    _check_virtual_flowpath_area_contributions(virtual_fp_pl, reference_fp_pl)
    _check_fp_to_id(fp_pl)
    virtual_nex_pl = pl.from_pandas(final_virtual_nexus.to_wkb())
    _check_virtual_nexus_meets_flowpath(virtual_nex_pl, virtual_fp_pl, reference_fp_pl, fp_pl)
    nex_pl = pl.from_pandas(final_nexus.to_wkb())
    _check_no_coincident_nexuses(final_nexus, final_virtual_nexus, final_virtual_flowpaths)
    _check_nexus_relational_integrity(fp_pl, nex_pl, virtual_fp_pl, virtual_nex_pl, reference_fp_pl)


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

    df = pd.DataFrame(
        {
            "nex_id": pd.Series([1285290888194346, 1285290920810185], dtype="Int64"),
            "dn_fp_id": pd.Series([pd.NA, 1285290904356516], dtype="Int64"),
            "vpu_id": pd.Series(["02", "02"], dtype="object"),
            "gid": pd.Series(["87G8MG4H+67V8", "87G8MGJQ+37F7"], dtype="object"),
        }
    )
    pd.testing.assert_frame_equal(final_nexus.drop(columns=["geometry"]), df)

    df = pd.DataFrame(
        {
            "virtual_nex_id": pd.Series(
                [
                    1285290888218129,
                    1285290933941302,
                    1285290934216316,
                    1285290920810186,
                    1285290888194347,
                    1285290940535636,
                ],
                dtype="Int64",
            ),
            "vpu_id": pd.Series(["02", "02", "02", "02", "02", "02"], dtype="object"),
            "gid": pd.Series(
                [
                    "87G8MG4H+978F",
                    "87G8MGRV+4M74",
                    "87G8MGRW+V2QR",
                    "87G8MGJQ+37F8",
                    "87G8MG4H+67V9",
                    "87G8MGWW+8X3R",
                ],
                dtype="object",
            ),
        }
    )
    pd.testing.assert_frame_equal(final_virtual_nexus.drop(columns=["geometry", "dn_virtual_fp_id"]), df)

    _check_hydroseq_decreases_downstream(fp_pl, graph, fp_id_col="fp_id")
    _check_virtual_flowpath_area_contributions(virtual_fp_pl, reference_fp_pl)
    _check_fp_to_id(fp_pl)
    virtual_nex_pl = pl.from_pandas(final_virtual_nexus.to_wkb())
    _check_virtual_nexus_meets_flowpath(virtual_nex_pl, virtual_fp_pl, reference_fp_pl, fp_pl)
    nex_pl = pl.from_pandas(final_nexus.to_wkb())
    _check_no_coincident_nexuses(final_nexus, final_virtual_nexus, final_virtual_flowpaths)
    _check_nexus_relational_integrity(fp_pl, nex_pl, virtual_fp_pl, virtual_nex_pl, reference_fp_pl)


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

    df = pd.DataFrame(
        {
            "nex_id": pd.Series([1286899585923076, 1286899585877606], dtype="Int64"),
            "dn_fp_id": pd.Series([pd.NA, 1286899585807776], dtype="Int64"),
            "vpu_id": pd.Series(["01", "01"], dtype="object"),
            "gid": pd.Series(["87MFG82J+29MR", "87MFG82H+PP28"], dtype="object"),
        }
    )
    pd.testing.assert_frame_equal(final_nexus.drop(columns=["geometry"]), df)

    df = pd.DataFrame(
        {
            "virtual_nex_id": pd.Series(
                [1286899589221071, 1286899585460874, 1286899591299859, 1286899585877607, 1286899585923077],
                dtype="Int64",
            ),
            "vpu_id": pd.Series(["01", "01", "01", "01", "01"], dtype="object"),
            "gid": pd.Series(
                ["87MFG83J+JJMH", "87MFG82F+4J5P", "87MFG847+JFJX", "87MFG82H+PP29", "87MFG82J+29MV"],
                dtype="object",
            ),
        }
    )
    pd.testing.assert_frame_equal(final_virtual_nexus.drop(columns=["geometry", "dn_virtual_fp_id"]), df)

    _check_hydroseq_decreases_downstream(fp_pl, graph, fp_id_col="fp_id")
    _check_virtual_flowpath_area_contributions(virtual_fp_pl, reference_fp_pl)
    _check_fp_to_id(fp_pl)
    virtual_nex_pl = pl.from_pandas(final_virtual_nexus.to_wkb())
    _check_virtual_nexus_meets_flowpath(virtual_nex_pl, virtual_fp_pl, reference_fp_pl, fp_pl)
    nex_pl = pl.from_pandas(final_nexus.to_wkb())
    _check_no_coincident_nexuses(final_nexus, final_virtual_nexus, final_virtual_flowpaths)
    _check_nexus_relational_integrity(fp_pl, nex_pl, virtual_fp_pl, virtual_nex_pl, reference_fp_pl)


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
    assert len(aggregate_data.independents) == 1219, "Incorrect number of independents"
    assert len(aggregate_data.connectors) == 996, "Incorrect number of connectors"
    assert len(aggregate_data.non_nextgen_flowpaths) == 3586, "Incorrect number of non nextgen flowpaths"
    assert len(aggregate_data.non_nextgen_virtual_flowpaths) == 2062, (
        "Incorrect number of non nextgen virtual flowpaths"
    )

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

    expected_df = pd.read_csv(
        here() / "tests/data/trace_cases/hudson_river_nexus.csv",
        dtype={"nex_id": "int64", "dn_fp_id": "float64", "vpu_id": "object"},
    )
    expected_df["nex_id"] = expected_df["nex_id"].astype("Int64")
    expected_df["dn_fp_id"] = expected_df["dn_fp_id"].astype("Int64")
    pd.testing.assert_frame_equal(final_nexus.drop(columns=["geometry"]), expected_df)

    expected_df = pd.read_csv(
        here() / "tests/data/trace_cases/hudson_river_virtual_nexus.csv",
        dtype={"virtual_nex_id": "int64", "vpu_id": "object", "dn_virtual_fp_id": "Int64"},
    )
    expected_df["virtual_nex_id"] = expected_df["virtual_nex_id"].astype("Int64")
    pd.testing.assert_frame_equal(final_virtual_nexus.drop(columns=["geometry"]), expected_df)

    _check_hydroseq_decreases_downstream(fp_pl, graph, fp_id_col="fp_id")
    _check_virtual_flowpath_area_contributions(virtual_fp_pl, reference_fp_pl)
    _check_fp_to_id(fp_pl)
    virtual_nex_pl = pl.from_pandas(final_virtual_nexus.to_wkb())
    _check_virtual_nexus_meets_flowpath(virtual_nex_pl, virtual_fp_pl, reference_fp_pl, fp_pl)
    nex_pl = pl.from_pandas(final_nexus.to_wkb())
    _check_no_coincident_nexuses(final_nexus, final_virtual_nexus, final_virtual_flowpaths)
    _check_nexus_relational_integrity(fp_pl, nex_pl, virtual_fp_pl, virtual_nex_pl, reference_fp_pl)


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
    assert len(aggregate_data.independents) == 1435, "Incorrect number of independents"
    assert len(aggregate_data.connectors) == 1060, "Incorrect number of connectors"
    assert len(aggregate_data.non_nextgen_flowpaths) == 7098, "Incorrect number of non nextgen flowpaths"
    assert len(aggregate_data.non_nextgen_virtual_flowpaths) == 2280, (
        "Incorrect number of non nextgen virtual flowpaths"
    )

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

    expected_df = pd.read_csv(
        here() / "tests/data/trace_cases/sioux_falls_nexus.csv",
        dtype={"nex_id": "int64", "dn_fp_id": "float64", "vpu_id": "object"},
    )
    expected_df["nex_id"] = expected_df["nex_id"].astype("Int64")
    expected_df["dn_fp_id"] = expected_df["dn_fp_id"].astype("Int64")
    pd.testing.assert_frame_equal(final_nexus.drop(columns=["geometry"]), expected_df)

    expected_df = pd.read_csv(
        here() / "tests/data/trace_cases/sioux_falls_virtual_nexus.csv",
        dtype={"virtual_nex_id": "int64", "vpu_id": "object", "dn_virtual_fp_id": "Int64"},
    )
    expected_df["virtual_nex_id"] = expected_df["virtual_nex_id"].astype("Int64")
    pd.testing.assert_frame_equal(final_virtual_nexus.drop(columns=["geometry"]), expected_df)

    _check_hydroseq_decreases_downstream(fp_pl, graph, fp_id_col="fp_id")
    _check_virtual_flowpath_area_contributions(virtual_fp_pl, reference_fp_pl)
    _check_fp_to_id(fp_pl)
    virtual_nex_pl = pl.from_pandas(final_virtual_nexus.to_wkb())
    _check_virtual_nexus_meets_flowpath(virtual_nex_pl, virtual_fp_pl, reference_fp_pl, fp_pl)
    nex_pl = pl.from_pandas(final_nexus.to_wkb())
    _check_no_coincident_nexuses(final_nexus, final_virtual_nexus, final_virtual_flowpaths)
    _check_nexus_relational_integrity(fp_pl, nex_pl, virtual_fp_pl, virtual_nex_pl, reference_fp_pl)


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
    assert len(aggregate_data.independents) == 3807, "Incorrect number of independents"
    assert len(aggregate_data.connectors) == 2969, "Incorrect number of connectors"
    assert len(aggregate_data.non_nextgen_flowpaths) == 11538, "Incorrect number of non nextgen flowpaths"
    assert len(aggregate_data.non_nextgen_virtual_flowpaths) == 8177, (
        "Incorrect number of non nextgen virtual flowpaths"
    )

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

    expected_df = pd.read_csv(
        here() / "tests/data/trace_cases/large_braided_river_nexus.csv",
        dtype={"nex_id": "int64", "dn_fp_id": "float64", "vpu_id": "object"},
    )
    expected_df["nex_id"] = expected_df["nex_id"].astype("Int64")
    expected_df["dn_fp_id"] = expected_df["dn_fp_id"].astype("Int64")
    pd.testing.assert_frame_equal(final_nexus.drop(columns=["geometry"]), expected_df)

    expected_df = pd.read_csv(
        here() / "tests/data/trace_cases/large_braided_river_virtual_nexus.csv",
        dtype={"virtual_nex_id": "int64", "vpu_id": "object", "dn_virtual_fp_id": "Int64"},
    )
    expected_df["virtual_nex_id"] = expected_df["virtual_nex_id"].astype("Int64")
    pd.testing.assert_frame_equal(final_virtual_nexus.drop(columns=["geometry"]), expected_df)

    _check_hydroseq_decreases_downstream(fp_pl, graph, fp_id_col="fp_id")
    _check_virtual_flowpath_area_contributions(virtual_fp_pl, reference_fp_pl)
    _check_fp_to_id(fp_pl)
    virtual_nex_pl = pl.from_pandas(final_virtual_nexus.to_wkb())
    _check_virtual_nexus_meets_flowpath(virtual_nex_pl, virtual_fp_pl, reference_fp_pl, fp_pl)
    nex_pl = pl.from_pandas(final_nexus.to_wkb())
    _check_no_coincident_nexuses(final_nexus, final_virtual_nexus, final_virtual_flowpaths)
    _check_nexus_relational_integrity(fp_pl, nex_pl, virtual_fp_pl, virtual_nex_pl, reference_fp_pl)


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
    assert len(aggregate_data.independents) == 1964, "Incorrect number of independents"
    assert len(aggregate_data.connectors) == 1443, "Incorrect number of connectors"
    assert len(aggregate_data.non_nextgen_flowpaths) == 3812, "Incorrect number of non nextgen flowpaths"
    assert len(aggregate_data.non_nextgen_virtual_flowpaths) == 2541, (
        "Incorrect number of non nextgen virtual flowpaths"
    )

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

    expected_df = pd.read_csv(
        here() / "tests/data/trace_cases/small_braided_river_nexus.csv",
        dtype={"nex_id": "int64", "dn_fp_id": "float64", "vpu_id": "object"},
    )
    expected_df["nex_id"] = expected_df["nex_id"].astype("Int64")
    expected_df["dn_fp_id"] = expected_df["dn_fp_id"].astype("Int64")
    pd.testing.assert_frame_equal(final_nexus.drop(columns=["geometry"]), expected_df)

    expected_df = pd.read_csv(
        here() / "tests/data/trace_cases/small_braided_river_virtual_nexus.csv",
        dtype={"virtual_nex_id": "int64", "vpu_id": "object", "dn_virtual_fp_id": "Int64"},
    )
    expected_df["virtual_nex_id"] = expected_df["virtual_nex_id"].astype("Int64")
    pd.testing.assert_frame_equal(final_virtual_nexus.drop(columns=["geometry"]), expected_df)

    _check_hydroseq_decreases_downstream(fp_pl, graph, fp_id_col="fp_id")
    _check_virtual_flowpath_area_contributions(virtual_fp_pl, reference_fp_pl)
    _check_fp_to_id(fp_pl)
    virtual_nex_pl = pl.from_pandas(final_virtual_nexus.to_wkb())
    _check_virtual_nexus_meets_flowpath(virtual_nex_pl, virtual_fp_pl, reference_fp_pl, fp_pl)
    nex_pl = pl.from_pandas(final_nexus.to_wkb())
    _check_no_coincident_nexuses(final_nexus, final_virtual_nexus, final_virtual_flowpaths)
    _check_nexus_relational_integrity(fp_pl, nex_pl, virtual_fp_pl, virtual_nex_pl, reference_fp_pl)
