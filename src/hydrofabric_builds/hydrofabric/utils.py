"""A utils file for common hydrofabric building functions"""

import logging
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import rustworkx as rx

logger = logging.getLogger(__name__)


def _get_upstream_ids_for_outlet(
    outlet: str,
    graph: rx.PyDiGraph,
    node_indices: dict[str, int],
) -> set[str]:
    """Get all upstream flowpath IDs for an outlet using rustworkx graph traversal.

    Parameters
    ----------
    outlet : str
        The outlet flowpath ID
    graph : rx.PyDiGraph
        The rustworkx graph object
    node_indices : dict[str, int]
        Mapping of flowpath_id to graph node index

    Returns
    -------
    set[str]
        Set of all flowpath IDs in this outlet's upstream tree
    """
    if outlet not in node_indices:
        return {outlet}

    outlet_node = node_indices[outlet]
    upstream_nodes = rx.ancestors(graph, outlet_node)
    upstream_nodes.add(outlet_node)
    upstream_ids = {graph.get_node_data(node) for node in upstream_nodes}

    return upstream_ids


def _combine_hydrofabrics(
    built_hydrofabrics: dict[str, dict[str, Any]], crs: str
) -> dict[str, (gpd.GeoDataFrame | pd.DataFrame)]:
    """Function to combine multiple outlet hydrofabrics.

    Parameters
    ----------
    built_hydrofabrics : dict[str, dict[str, Any]]
        Dictionary mapping outlet_id -> hydrofabric data containing
        "flowpaths", "divides", "nexus", "virtual_flowpaths", and "virtual_nexus" GeoDataFrames
    crs : str
        Coordinate reference system for output GeoDataFrames

    Returns
    -------
    dict[str, gpd.GeoDataFrame | pd.DataFrame]
        Dictionary with keys:
        - "flowpaths": Combined GeoDataFrame of all flowpaths
        - "divides": Combined GeoDataFrame of all divides
        - "nexus": Combined GeoDataFrame of all nexus points
        - "reference_flowpaths": Combined DataFrame of reference flowpaths
        - "virtual_flowpaths": Combined GeoDataFrame of virtual flowpaths (if present)
        - "virtual_nexus": Combined GeoDataFrame of virtual nexus (if present)
        - "reference_virtual_flowpaths": Combined DataFrame of reference virtual flowpaths (if present)

    Raises
    ------
    ValueError
        If built_hydrofabrics is empty or None
    KeyError
        If required keys missing from hydrofabric data
    """
    if not built_hydrofabrics:
        raise ValueError("No built hydrofabrics provided")

    all_flowpaths = []
    all_divides = []
    all_nexus = []
    all_reference_flowpaths = []
    all_virtual_flowpaths = []
    all_virtual_nexus = []
    # all_reference_virtual_flowpaths = []

    valid_hydrofabrics = {k: v for k, v in built_hydrofabrics.items() if v is not None}
    if not valid_hydrofabrics:
        raise ValueError("No valid hydrofabrics to combine (all results were None)")

    for outlet_id, hf_data in valid_hydrofabrics.items():
        # Check required keys
        if "flowpaths" not in hf_data:
            raise KeyError(f"Missing 'flowpaths' for outlet {outlet_id}")
        if "divides" not in hf_data:
            raise KeyError(f"Missing 'divides' for outlet {outlet_id}")
        if "nexus" not in hf_data:
            raise KeyError(f"Missing 'nexus' for outlet {outlet_id}")
        if "reference_flowpaths" not in hf_data:
            raise KeyError(f"Missing 'reference_flowpaths' for outlet {outlet_id}")

        # Collect regular layers
        if hf_data["flowpaths"] is not None:
            if not hf_data["flowpaths"].empty:
                all_flowpaths.append(hf_data["flowpaths"])

        if hf_data["nexus"] is not None:
            if not hf_data["nexus"].empty:
                all_nexus.append(hf_data["nexus"])

        if hf_data["reference_flowpaths"] is not None:
            if not hf_data["reference_flowpaths"].empty:
                all_reference_flowpaths.append(hf_data["reference_flowpaths"])

        if hf_data["divides"] is not None:
            # Making sure that the divides layer geometry isn't empty
            if not hf_data["divides"].empty and not hf_data["divides"].geometry.is_empty.iloc[0]:
                all_divides.append(hf_data["divides"])

        # Collect virtual layers (if they exist)
        if "virtual_flowpaths" in hf_data and hf_data["virtual_flowpaths"] is not None:
            if not hf_data["virtual_flowpaths"].empty:
                all_virtual_flowpaths.append(hf_data["virtual_flowpaths"])

        if "virtual_nexus" in hf_data and hf_data["virtual_nexus"] is not None:
            if not hf_data["virtual_nexus"].empty:
                all_virtual_nexus.append(hf_data["virtual_nexus"])

        # if "reference_virtual_flowpaths" in hf_data and hf_data["reference_virtual_flowpaths"] is not None:
        #     if not hf_data["reference_virtual_flowpaths"].empty:
        #         all_reference_virtual_flowpaths.append(hf_data["reference_virtual_flowpaths"])

    if not all_flowpaths:
        raise ValueError("No non-empty flowpaths to combine across all outlets")

    # Combine regular layers
    combined_flowpaths = pd.concat(all_flowpaths, ignore_index=True)
    combined_divides = pd.concat(all_divides, ignore_index=True)
    combined_nexus = pd.concat(all_nexus, ignore_index=True)
    combined_reference_flowpaths = pd.concat(all_reference_flowpaths, ignore_index=True)

    # Merge coincident nexuses from different outlets, but only when they
    # share the same dn_fp_id (or both are null outlets). Nexuses at the same
    # location with different downstream flowpaths are distinct junctions.
    combined_nexus["_coord"] = combined_nexus.geometry.apply(lambda g: (round(g.x, 6), round(g.y, 6)))
    combined_nexus["_dn_key"] = combined_nexus["dn_fp_id"].astype("Int64").fillna(-1)
    nex_dupes = combined_nexus.duplicated(subset=["_coord", "_dn_key"], keep="first")
    if nex_dupes.any():
        nex_remap: dict[int, int] = {}
        for (_coord, _dn_key), group in combined_nexus.groupby(["_coord", "_dn_key"]):
            if len(group) <= 1:
                continue
            keep_id = group["nex_id"].iloc[0]
            for nex_id in group["nex_id"].iloc[1:]:
                nex_remap[nex_id] = keep_id
        combined_nexus = combined_nexus[~nex_dupes].copy()
        if nex_remap:
            combined_flowpaths["dn_nex_id"] = combined_flowpaths["dn_nex_id"].map(
                lambda x: nex_remap.get(x, x) if pd.notna(x) else x
            )
            combined_flowpaths["up_nex_id"] = combined_flowpaths["up_nex_id"].map(
                lambda x: nex_remap.get(x, x) if pd.notna(x) else x
            )
    combined_nexus = combined_nexus.drop(columns=["_coord", "_dn_key"])

    final_flowpaths = gpd.GeoDataFrame(combined_flowpaths)
    final_divides = gpd.GeoDataFrame(combined_divides)
    # NOTE: Emit a warning if non-polygon divides are present
    non_polygon_divides_count = len(
        final_divides[
            ~((final_divides.geometry.type == "MultiPolygon") | (final_divides.geometry.type == "Polygon"))
        ]
    )
    if non_polygon_divides_count > 0:
        raise AttributeError(
            f"combine_hydrofabrics: {non_polygon_divides_count} non-polygon divides present in `final_divides`"
        )

    final_nexus = gpd.GeoDataFrame(combined_nexus)

    # Combine virtual layers (if any exist)
    final_virtual_flowpaths = None
    final_virtual_nexus = None
    # final_reference_virtual_flowpaths = None

    if all_virtual_flowpaths:
        combined_virtual_flowpaths = pd.concat(all_virtual_flowpaths, ignore_index=True)
        final_virtual_flowpaths = gpd.GeoDataFrame(combined_virtual_flowpaths)

    if all_virtual_nexus:
        combined_virtual_nexus = pd.concat(all_virtual_nexus, ignore_index=True)
        # Merge coincident nexuses from different outlets
        combined_virtual_nexus["_coord"] = combined_virtual_nexus.geometry.apply(
            lambda g: (round(g.x, 6), round(g.y, 6))
        )
        dupes = combined_virtual_nexus.duplicated(subset=["_coord"], keep="first")
        if dupes.any() and final_virtual_flowpaths is not None:
            # Build sets of nexuses that must not merge:
            # 1. Pairs where a VFP uses one as up_nex and the other as dn_nex (cycle)
            # 2. Nexuses both used as up_virtual_nex_id (divergence)
            no_merge_pairs: set[frozenset[int]] = set()
            up_nex_ids: set[int] = set()
            for _, vfp_row in final_virtual_flowpaths.iterrows():
                up = vfp_row["up_virtual_nex_id"]
                dn = vfp_row["dn_virtual_nex_id"]
                if pd.notna(up):
                    no_merge_pairs.add(frozenset((int(up), int(dn))))
                    up_nex_ids.add(int(up))

            # Build remap: duplicate nex_id -> surviving nex_id
            vnex_remap: dict[int, int] = {}
            for _coord, group in combined_virtual_nexus.groupby("_coord"):
                if len(group) <= 1:
                    continue
                keep_id = group["virtual_nex_id"].iloc[0]
                for nex_id in group["virtual_nex_id"].iloc[1:]:
                    if frozenset((nex_id, keep_id)) in no_merge_pairs:
                        continue
                    if nex_id in up_nex_ids and keep_id in up_nex_ids:
                        continue
                    vnex_remap[nex_id] = keep_id
                    if nex_id in up_nex_ids:
                        up_nex_ids.add(keep_id)
            merged_ids = set(vnex_remap.keys())
            combined_virtual_nexus = combined_virtual_nexus[
                ~combined_virtual_nexus["virtual_nex_id"].isin(merged_ids)
            ].copy()
            # Rewrite VFP references in the combined virtual flowpaths
            if vnex_remap:
                final_virtual_flowpaths["dn_virtual_nex_id"] = final_virtual_flowpaths[
                    "dn_virtual_nex_id"
                ].map(lambda x: vnex_remap.get(x, x))
                final_virtual_flowpaths["up_virtual_nex_id"] = final_virtual_flowpaths[
                    "up_virtual_nex_id"
                ].map(lambda x: vnex_remap.get(x, x) if pd.notna(x) else x)
        combined_virtual_nexus = combined_virtual_nexus.drop(columns=["_coord"])
        final_virtual_nexus = gpd.GeoDataFrame(combined_virtual_nexus)

    # if all_reference_virtual_flowpaths:
    #     combined_reference_virtual_flowpaths = pd.concat(all_reference_virtual_flowpaths, ignore_index=True)
    #     final_reference_virtual_flowpaths = pd.DataFrame(combined_reference_virtual_flowpaths)

    # Set/fix CRS for regular layers
    if final_flowpaths.crs is not None and final_flowpaths.crs != crs:
        final_flowpaths = final_flowpaths.to_crs(crs)
        final_divides = final_divides.to_crs(crs)
        final_nexus = final_nexus.to_crs(crs)
    elif final_flowpaths.crs is None:
        final_flowpaths = final_flowpaths.set_crs(crs)
        final_divides = final_divides.set_crs(crs)
        final_nexus = final_nexus.set_crs(crs)

    # Set/fix CRS for virtual layers
    if final_virtual_flowpaths is not None:
        if final_virtual_flowpaths.crs is not None and final_virtual_flowpaths.crs != crs:
            final_virtual_flowpaths = final_virtual_flowpaths.to_crs(crs)
        elif final_virtual_flowpaths.crs is None:
            final_virtual_flowpaths = final_virtual_flowpaths.set_crs(crs)
    else:
        # Some areas (e.g. Alaska) produce no virtual flowpaths; create empty GeoDataFrame with expected schema
        final_virtual_flowpaths = gpd.GeoDataFrame(
            columns=[
                "virtual_fp_id",
                "dn_virtual_nex_id",
                "length_km",
                "area_sqkm",
                "percentage_area_contribution",
                "vpu_id",
                "geometry",
            ],
            crs=crs,
        )

    if final_virtual_nexus is not None:
        if final_virtual_nexus.crs is not None and final_virtual_nexus.crs != crs:
            final_virtual_nexus = final_virtual_nexus.to_crs(crs)
        elif final_virtual_nexus.crs is None:
            final_virtual_nexus = final_virtual_nexus.set_crs(crs)
    else:
        final_virtual_nexus = gpd.GeoDataFrame(
            columns=[
                "virtual_nex_id",
                "dn_virtual_fp_id",
                "vpu_id",
                "geometry",
            ],
            crs=crs,
        )

    # Line merge flowpaths to remove multilinestring geometries
    final_flowpaths = final_flowpaths.assign(geometry=final_flowpaths.line_merge())

    # Line merge virtual flowpaths if they exist
    if final_virtual_flowpaths is not None:
        final_virtual_flowpaths = final_virtual_flowpaths.assign(
            geometry=final_virtual_flowpaths.line_merge()
        )

    # Build return dictionary
    result = {
        "flowpaths": final_flowpaths,
        "divides": final_divides,
        "nexus": final_nexus,
        "reference_flowpaths": combined_reference_flowpaths,
    }

    # Add virtual layers if they exist
    if final_virtual_flowpaths is not None:
        result["virtual_flowpaths"] = final_virtual_flowpaths

    if final_virtual_nexus is not None:
        result["virtual_nexus"] = final_virtual_nexus

    return result


def _check_network_cycles(
    fp_gdf: gpd.GeoDataFrame,
    nex_gdf: gpd.GeoDataFrame,
    vfp_gdf: gpd.GeoDataFrame | None,
    vnex_gdf: gpd.GeoDataFrame | None,
    n_outlets: int = 100,
    min_flowpaths: int = 100,
    seed: int = 42,
) -> None:
    """Check flowpath and virtual flowpath networks for cycles.

    Samples the largest outlets (by flowpath count) and builds directed graphs
    from their flowpath/nexus connectivity to detect strongly connected
    components (cycles) using rustworkx.

    Parameters
    ----------
    fp_gdf : gpd.GeoDataFrame
        Flowpaths layer
    nex_gdf : gpd.GeoDataFrame
        Nexus layer
    vfp_gdf : gpd.GeoDataFrame | None
        Virtual flowpaths layer
    vnex_gdf : gpd.GeoDataFrame | None
        Virtual nexus layer
    n_outlets : int
        Number of outlets to sample (default 100)
    min_flowpaths : int
        Minimum flowpath count per outlet to be eligible for sampling (default 10)
    seed : int
        Random seed for reproducibility

    Raises
    ------
    ValueError
        If cycles are detected in either network
    """
    import random

    logger = logging.getLogger(__name__)

    # Identify outlets: flowpaths with no downstream (fp_to_id is null)
    outlet_col = "fp_to_id" if "fp_to_id" in fp_gdf.columns else None
    if outlet_col is None:
        logger.warning("No fp_to_id column found, skipping cycle check")
        return

    # Build outlet -> upstream fp_ids using the graph
    fp_to_id_map = dict(zip(fp_gdf["fp_id"].astype(int), fp_gdf["fp_to_id"], strict=False))
    # Reverse map: downstream_fp -> list of upstream fps
    upstream_map: dict[int, list[int]] = {}
    for fp_id, to_id in fp_to_id_map.items():
        if pd.notna(to_id):
            upstream_map.setdefault(int(to_id), []).append(fp_id)

    outlets = fp_gdf[fp_gdf["fp_to_id"].isna()]["fp_id"].astype(int).tolist()

    # Count flowpaths per outlet via BFS
    outlet_sizes: dict[int, int] = {}
    for outlet in outlets:
        count = 0
        stack = [outlet]
        while stack:
            node = stack.pop()
            count += 1
            stack.extend(upstream_map.get(node, []))
        outlet_sizes[outlet] = count

    # Filter outlets with enough flowpaths, then sample
    eligible = [o for o, c in outlet_sizes.items() if c >= min_flowpaths]
    rng = random.Random(seed)
    sampled = rng.sample(eligible, min(n_outlets, len(eligible)))
    logger.info(f"Cycle check: sampling {len(sampled)} outlets (min {min_flowpaths} fps each)")

    # Collect all fp_ids in sampled outlets
    sampled_fp_ids: set[int] = set()
    for outlet in sampled:
        stack = [outlet]
        while stack:
            node = stack.pop()
            sampled_fp_ids.add(node)
            stack.extend(upstream_map.get(node, []))

    # --- Flowpath network ---
    fp = fp_gdf[fp_gdf["fp_id"].astype(int).isin(sampled_fp_ids)]
    nex_ids_needed = set(fp["dn_nex_id"].dropna().astype(int)) | set(fp["up_nex_id"].dropna().astype(int))
    nex = nex_gdf[nex_gdf["nex_id"].astype(int).isin(nex_ids_needed)]

    if len(fp) > 0:
        graph = rx.PyDiGraph()
        node_map: dict[tuple[str, int], int] = {}

        def get_or_add(key: tuple[str, int]) -> int:
            if key not in node_map:
                node_map[key] = graph.add_node(key)
            return node_map[key]

        for fp_id, dn_nex, up_nex in zip(
            fp["fp_id"].astype(int), fp["dn_nex_id"], fp["up_nex_id"], strict=False
        ):
            v = get_or_add(("fp", int(fp_id)))
            if pd.notna(dn_nex):
                graph.add_edge(v, get_or_add(("nex", int(dn_nex))), None)
            if pd.notna(up_nex):
                graph.add_edge(get_or_add(("nex", int(up_nex))), v, None)

        for nex_id, dn_fp in zip(nex["nex_id"].astype(int), nex["dn_fp_id"], strict=False):
            if pd.notna(dn_fp):
                graph.add_edge(get_or_add(("nex", int(nex_id))), get_or_add(("fp", int(dn_fp))), None)

        sccs = [scc for scc in rx.strongly_connected_components(graph) if len(scc) > 1]
        if sccs:
            cycle_fps = [sorted(graph[n][1] for n in scc if graph[n][0] == "fp") for scc in sccs]
            raise ValueError(f"Flowpath network has {len(sccs)} cycle(s): {cycle_fps}")
        logger.info(f"Flowpath network DAG check passed ({len(fp)} flowpaths)")

    # --- Virtual flowpath network ---
    if vfp_gdf is None or vnex_gdf is None or vfp_gdf.empty or vnex_gdf.empty:
        return

    # Filter VFPs by sampled fp_ids via reference_flowpaths div_id isn't available,
    # but VFPs share vpu_id with their parent flowpaths. Use the nexus overlap instead:
    # a VFP is in scope if its dn_virtual_nex_id or up_virtual_nex_id is referenced
    # by a flowpath in sampled_fp_ids. Simpler: just use all VFPs for sampled VPUs.
    sampled_vpus = set(fp["vpu_id"].unique())
    vfp = vfp_gdf[vfp_gdf["vpu_id"].isin(sampled_vpus)]
    vnex = vnex_gdf[vnex_gdf["vpu_id"].isin(sampled_vpus)]

    if len(vfp) == 0:
        return

    vgraph = rx.PyDiGraph()
    vnode_map: dict[tuple[str, int], int] = {}

    def vget_or_add(key: tuple[str, int]) -> int:
        if key not in vnode_map:
            vnode_map[key] = vgraph.add_node(key)
        return vnode_map[key]

    for vfp_id, dn_nex, up_nex in zip(
        vfp["virtual_fp_id"].astype(int),
        vfp["dn_virtual_nex_id"].astype(int),
        vfp["up_virtual_nex_id"],
        strict=False,
    ):
        v = vget_or_add(("vfp", int(vfp_id)))
        vgraph.add_edge(v, vget_or_add(("nex", int(dn_nex))), None)
        if pd.notna(up_nex):
            vgraph.add_edge(vget_or_add(("nex", int(up_nex))), v, None)

    for nex_id, dn_fp in zip(vnex["virtual_nex_id"].astype(int), vnex["dn_virtual_fp_id"], strict=False):
        if pd.notna(dn_fp):
            vgraph.add_edge(vget_or_add(("nex", int(nex_id))), vget_or_add(("vfp", int(dn_fp))), None)

    vsccs = [scc for scc in rx.strongly_connected_components(vgraph) if len(scc) > 1]
    if vsccs:
        cycle_vfps = [sorted(vgraph[n][1] for n in scc if vgraph[n][0] == "vfp") for scc in vsccs]
        raise ValueError(f"Virtual flowpath network has {len(vsccs)} cycle(s): {cycle_vfps}")
    logger.info(f"Virtual flowpath network DAG check passed ({len(vfp)} VFPs)")

    # --- Divergence check: no nexus should feed multiple downstream VFPs ---
    up_nex_counts = vfp["up_virtual_nex_id"].dropna().astype(int).value_counts()
    divergent = up_nex_counts[up_nex_counts > 1]
    if len(divergent) > 0:
        raise ValueError(
            f"Virtual flowpath network has {len(divergent)} divergent nexus(es) "
            f"(up_virtual_nex_id shared by multiple VFPs): {divergent.index.tolist()[:20]}"
        )
    logger.info(f"Virtual flowpath network divergence check passed ({len(vfp)} VFPs)")


def _crosswalk_nexus(hf_path: Path, gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Crosswalk using flowpath and virtual flowpath to get dn_nex_id / virtual_dn_nex_id

    Parameters
    ----------
    hf_path : Path
        hydrofabric
    gdf : gpd.GeoDataFrame
        dataframe to crosswalk to

    Returns
    -------
    gpd.GeoDataFrame
        crosswalked gdf
    """
    gdf_fp = gpd.read_file(hf_path, layer="flowpaths")
    gdf_vfp = gpd.read_file(hf_path, layer="virtual_flowpaths")

    gdf = gdf.merge(gdf_fp[["fp_id", "dn_nex_id"]], on="fp_id", how="left")
    gdf = gdf.merge(gdf_vfp[["virtual_fp_id", "dn_virtual_nex_id"]], on="virtual_fp_id", how="left")

    return gdf


def _crosswalk_reference(hf_path: Path, gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Crosswalk a gdf to refernce flowpaths to get fp_id and virtual_fp_id

    Parameters
    ----------
    hf_path : Path
        hydrofabric
    gdf : gpd.GeoDataFrame
        dataframe to crosswalk to

    Returns
    -------
    gpd.GeoDataFrame
        crosswalked gdf
    """
    hf_ref = gpd.read_file(hf_path, layer="reference_flowpaths")

    # prefer casting to int as this is correct type. Handle null flowpaths and alert user
    try:
        gdf["ref_fp_id"] = pd.to_numeric(gdf["ref_fp_id"]).astype(np.int64)
    except pd.errors.IntCastingNaNError:
        hf_ref["ref_fp_id"] = hf_ref["ref_fp_id"].astype(str)
        logger.info(
            "NOTE: Layer to be crosswalked has null reference flowpath associations. Casting to string for join. Check if this is unexpected behavior."
        )

    if "segment_order" in hf_ref.columns:
        best_idx = hf_ref.groupby("ref_fp_id")["segment_order"].idxmax()
        hf_ref = hf_ref.loc[best_idx]

    return gdf.merge(hf_ref, on="ref_fp_id", how="left")


def _crosswalk_reference_to_vfp(hf_path: Path, gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Crosswalk a gdf to reference flowpaths to get virtual_fp_id only

    Parameters
    ----------
    hf_path : Path
        hydrofabric
    gdf : gpd.GeoDataFrame
        dataframe to crosswalk to

    Returns
    -------
    gpd.GeoDataFrame
        crosswalked gdf
    """
    hf_ref = gpd.read_file(hf_path, layer="reference_flowpaths")

    # prefer casting to int as this is correct type. Handle null flowpaths and alert user
    try:
        gdf["ref_fp_id"] = pd.to_numeric(gdf["ref_fp_id"]).astype(np.int64)
    except pd.errors.IntCastingNaNError:
        hf_ref["ref_fp_id"] = hf_ref["ref_fp_id"].astype(str)
        logger.info(
            "NOTE: Layer to be crosswalked has null reference flowpath associations. Casting to string for join. Check if this is unexpected behavior."
        )

    if "segment_order" in hf_ref.columns:
        best_idx = hf_ref.groupby("ref_fp_id")["segment_order"].idxmax()
        hf_ref = hf_ref.loc[best_idx]

    return gdf.merge(hf_ref[["ref_fp_id", "virtual_fp_id"]], on="ref_fp_id", how="left")


def _crosswalk_fp_nexus(
    gdf: gpd.GeoDataFrame, hf_ref: gpd.GeoDataFrame, fp: gpd.GeoDataFrame, vfp: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    """Crosswalk using flowpath and virtual flowpath to get dn_nex_id / virtual_dn_nex_id

    Parameters
    ----------
    hf_path : Path
        hydrofabric
    gdf : gpd.GeoDataFrame
        dataframe to crosswalk to

    Returns
    -------
    gpd.GeoDataFrame
        crosswalked gdf
    """
    if pd.api.types.is_object_dtype(vfp["virtual_fp_id"]):
        vfp["virtual_fp_id"] = pd.to_numeric(vfp["virtual_fp_id"]).astype(pd.Int64Dtype())

    if pd.api.types.is_object_dtype(fp["fp_id"]):
        fp["fp_id"] = pd.to_numeric(fp["fp_id"]).astype(pd.Int64Dtype())

    # extract needed data from hf_ref and create 1:1 relationship betwen ref_fp_id and virtual_fp_id
    hf_ref = (
        hf_ref[["ref_fp_id", "fp_id", "virtual_fp_id", "div_id", "segment_order"]]
        .copy()
        .reset_index(drop=True)
    )
    # use segment order first, then take first if there are still duplicates
    segment_idx = hf_ref.groupby("ref_fp_id")["segment_order"].idxmax()
    hf_ref = hf_ref.loc[segment_idx]
    hf_ref = hf_ref.drop_duplicates(subset=["virtual_fp_id"], keep="first")
    hf_ref.drop(columns=["segment_order"], inplace=True)

    # drop pre-existing ref_fp_id to ensure correct match in join
    if "ref_fp_id" in gdf.columns:
        gdf.drop(columns=["ref_fp_id"], inplace=True)

    gdf = gdf.merge(hf_ref, on="virtual_fp_id", how="left")
    gdf = gdf.merge(vfp[["virtual_fp_id", "dn_virtual_nex_id"]], on="virtual_fp_id", how="left")
    gdf = gdf.merge(fp[["fp_id", "dn_nex_id"]], on="fp_id", how="left")
    gdf["ref_fp_id"] = gdf["ref_fp_id"].astype(pd.Int64Dtype())

    return gdf
