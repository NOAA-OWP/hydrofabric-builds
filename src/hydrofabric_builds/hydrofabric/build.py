"""A file to contain any hydrofabric building functions"""

import logging
from collections import defaultdict, deque
from typing import Any

import geopandas as gpd
import pandas as pd
import rustworkx as rx
from shapely import Point
from shapely.geometry import LineString
from shapely.geometry.base import BaseGeometry
from shapely.ops import linemerge, substring

from hydrofabric_builds.config import HFConfig
from hydrofabric_builds.schemas.hydrofabric import Aggregations, Classifications

logger = logging.getLogger(__name__)


def _order_aggregates_base(
    aggregate_data: Aggregations,
) -> tuple[dict[str, dict[str, Any]], dict[str, float]]:
    """Take multiple aggregations and order them to build a base hydrofabric for NGEN.

    Parameters
    ----------
    aggregate_data : Aggregations
        The Aggregations BaseModel created by _aggregate_geometries()

    Returns
    -------
    tuple[dict[str, dict[str, Any]], dict[str, float]]
        All required aggregations for the base hydrofabric schema, mapping ref_id to unit info, and a dictionary mapping the reference id to it's weighted area percentage
    """
    ref_id_to_unit: dict[str, dict[str, Any]] = {}
    ref_fp_id_to_percentage: dict[str, float] = {}

    for unit in aggregate_data.aggregates:
        unit_info: dict[str, Any] = {
            "unit": unit,
            "type": "aggregate",
            "vpu_id": unit["vpu_id"],
            "hydroseq": unit["hydroseq"],
            "length_km": unit["length_km"],
            "area_sqkm": unit["area_sqkm"],
            "up_id": str(unit["up_id"]),
            "dn_id": str(unit["dn_id"]),
            "all_ref_ids": [str(rid) for rid in unit["ref_ids"]],  # Store all ref_ids
        }
        for ref_id in unit["ref_ids"]:
            ref_id_to_unit[str(ref_id)] = unit_info

        ref_id_to_percentage = unit.get("ref_id_to_percentage", {})
        for ref_id, percentage in ref_id_to_percentage.items():
            ref_fp_id_to_percentage[ref_id] = percentage

    for unit in aggregate_data.independents:
        ref_id = unit["ref_ids"]
        ref_id_to_unit[ref_id] = {
            "unit": unit,
            "type": "independent",
            "vpu_id": unit["vpu_id"],
            "hydroseq": unit["hydroseq"],
            "length_km": unit["length_km"],
            "area_sqkm": unit["area_sqkm"],
            "all_ref_ids": [ref_id],
        }

        ref_id_to_percentage = unit.get("ref_id_to_percentage", {})
        for ref_id_str, percentage in ref_id_to_percentage.items():
            ref_fp_id_to_percentage[ref_id_str] = percentage

    for unit in aggregate_data.connectors:
        ref_id = unit["ref_ids"]
        ref_id_to_unit[ref_id] = {
            "unit": unit,
            "type": "connectors",
            "vpu_id": unit["vpu_id"],
            "hydroseq": unit["hydroseq"],
            "length_km": unit["length_km"],
            "area_sqkm": unit["area_sqkm"],
            "all_ref_ids": [ref_id],
        }
        ref_id_to_percentage = unit.get("ref_id_to_percentage", {})
        for ref_id_str, percentage in ref_id_to_percentage.items():
            ref_fp_id_to_percentage[ref_id_str] = percentage

    return ref_id_to_unit, ref_fp_id_to_percentage


def _queue_all_unit_upstreams(
    unit_info: dict[str, Any],
    ref_ids: list[str],
    current_ref_id: str,
    graph: rx.PyDiGraph,
    node_indices: dict[str, int],
    to_process: deque[str],
) -> None:
    """Queue all upstream flowpaths for a unit.

    For aggregates, checks all ref_ids to capture all upstream branches.
    For independents/connectors, just checks the current flowpath.

    Parameters
    ----------
    unit_info : dict[str, Any]
        Info for the unit
    ref_ids : list[str]
        All reference flowpath IDs in this unit
    current_ref_id : str
        Current flowpath ID being processed
    graph : rx.PyDiGraph
        Network graph
    node_indices : dict[str, int]
        Mapping of flowpath IDs to node indices
    to_process : deque[str]
        Queue to add upstream IDs to
    """
    if unit_info["type"] == "aggregate":
        # For aggregates, check all ref_ids for upstreams
        all_upstream_ids = []
        if "0" in ref_ids:
            ref_ids.remove("0")
        for ref_id in ref_ids:
            if ref_id in node_indices:
                ref_idx = node_indices[ref_id]
                upstream_ids = [graph[idx] for idx in graph.predecessor_indices(ref_idx)]
                all_upstream_ids.extend(upstream_ids)
        unique_upstream_ids = sorted(set(all_upstream_ids))
        to_process.extend(unique_upstream_ids)
    else:
        # For independents/connectors, just use current_ref_id
        if current_ref_id in node_indices:
            current_idx = node_indices[current_ref_id]
            upstream_ids = [graph[idx] for idx in graph.predecessor_indices(current_idx)]
            to_process.extend(upstream_ids)


def _create_mainstem_virtual_flowpaths(
    nhf_fp_to_vnexuses: dict[int, list[tuple[int, Point]]],
    nhf_fp_to_ref_ids: dict[int, list[str]],
    nhf_fp_to_all_ref_ids: dict[int, list[str]],
    nhf_fp_tributary_pct: dict[int, float],
    fp_data: list[dict[str, Any]],
    fp_lookup: dict[str, dict[str, Any]],
    div_lookup: dict[str, dict[str, Any]] | None,
    nhf_id: int,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[int, int],
    dict[int, int],
    dict[str, int],
    dict[str, int],
    dict[str, int],
    int,
]:
    """Create main-stem virtual flowpath segments along NHF flowpaths at virtual nexuses.

    For each NHF flowpath that has virtual nexuses on it (from tributary virtual chains),
    creates segments between consecutive virtual nexuses and from the upstream/downstream
    ends to the nearest virtual nexus.

    Area is computed using a hybrid approach: first, actual divide areas are assigned to
    segments via reference FP projection overlap.  Any remaining area (mainstem share
    minus overlap-assigned area) is then distributed proportionally by segment length.
    This ensures mainstem + tributary percentages sum to 1.0 per divide.

    Parameters
    ----------
    nhf_fp_to_vnexuses : dict[int, list[tuple[int, Point]]]
        Mapping of NHF fp_id to list of (virtual_nex_id, nexus_point) tuples
    nhf_fp_to_ref_ids : dict[int, list[str]]
        Mapping of NHF fp_id to list of mainstem reference FP IDs (for area calculation)
    nhf_fp_to_all_ref_ids : dict[int, list[str]]
        Mapping of NHF fp_id to ALL reference FP IDs including tributaries
    nhf_fp_tributary_pct : dict[int, float]
        Accumulated tributary VFP percentage per NHF fp_id (div_id)
    fp_data : list[dict[str, Any]]
        List of NHF flowpath records
    fp_lookup : dict[str, dict[str, Any]]
        Reference flowpath lookup
    div_lookup : dict[str, dict[str, Any]] | None
        Divide lookup for area calculation
    nhf_id : int
        Current ID counter

    Returns
    -------
    tuple[list[dict[str, Any]], list[dict[str, Any]], dict[int, int], dict[int, int], dict[str, int], dict[str, int], dict[str, int], int]
        Main-stem virtual flowpath records (with ref_fp_id populated),
        new virtual nexus records,
        mapping of virtual_nex_id to downstream virtual_fp_id,
        mapping of nhf_fp_id to upstream virtual_nex_id,
        mapping of mainstem ref_fp_id to virtual_fp_id,
        mapping of mainstem ref_fp_id to segment_order,
        mapping of all ref_fp_id to mainstem virtual_fp_id, and updated ID counter
    """
    mainstem_vfp_data: list[dict[str, Any]] = []
    new_virtual_nexus_data: list[dict[str, Any]] = []
    vnex_to_dn_vfp: dict[int, int] = {}
    nhf_fp_to_upstream_vnex: dict[int, int] = {}  # nhf_fp_id -> upstream virtual_nex_id
    ref_to_vfp: dict[str, int] = {}  # mainstem ref_fp_id -> virtual_fp_id
    ref_to_segment_order: dict[str, int] = {}  # mainstem ref_fp_id -> segment_order
    all_ref_to_mainstem_vfp: dict[str, int] = {}  # all ref_fp_id -> mainstem virtual_fp_id
    fp_data_by_id: dict[int, dict[str, Any]] = {fp["fp_id"]: fp for fp in fp_data}

    for nhf_fp_id, vnexuses in nhf_fp_to_vnexuses.items():
        if nhf_fp_id not in fp_data_by_id:
            continue

        fp_entry = fp_data_by_id[nhf_fp_id]
        line_geom = fp_entry["geometry"]
        nhf_fp_vpu_id = fp_entry["vpu_id"]

        # Handle MultiLineString
        if line_geom.geom_type == "MultiLineString":
            merged = linemerge(line_geom)
            if merged.geom_type == "LineString":
                line = merged
            else:
                coords: list[tuple[float, ...]] = []
                for part in line_geom.geoms:
                    coords.extend(list(part.coords))
                line = LineString(coords)
        else:
            line = line_geom

        total_length = line.length
        if total_length == 0:
            continue

        # Project virtual nexus points onto the line and sort upstream to downstream
        projected_nexuses: list[tuple[int, float]] = []
        for vnex_id, vnex_point in vnexuses:
            dist = line.project(vnex_point)
            projected_nexuses.append((vnex_id, dist))
        projected_nexuses.sort(key=lambda x: x[1])

        # Remaining area/percentage after tributary contributions
        total_nhf_area = float(fp_entry["area_sqkm"])
        tributary_pct = nhf_fp_tributary_pct.get(nhf_fp_id, 0.0)
        remaining_pct = max(1.0 - tributary_pct, 0.0)
        remaining_area = total_nhf_area * remaining_pct

        # Build reference FP ranges for overlap-based area calculation
        ref_ids = nhf_fp_to_ref_ids.get(nhf_fp_id, [])
        ref_fp_ranges: list[tuple[float, float, float]] = []  # (start_dist, end_dist, area_km2)
        ref_fp_projected_ranges: list[tuple[str, float, float]] = []  # (ref_id, start_dist, end_dist)

        for ref_id in ref_ids:
            if ref_id not in fp_lookup:
                continue
            ref_fp = fp_lookup[ref_id]
            ref_geom = ref_fp.get("shapely_geometry")
            if ref_geom is None or ref_geom.is_empty:
                continue

            # Get area from div_lookup geometry or fp_lookup areasqkm
            ref_area = 0.0
            if div_lookup and ref_id in div_lookup:
                ref_area = div_lookup[ref_id]["shapely_geometry"].area / 1e6
            else:
                ref_area = float(ref_fp.get("areasqkm", 0.0))

            # Project reference FP endpoints onto NHF line
            if ref_geom.geom_type == "MultiLineString":
                ref_merged = linemerge(ref_geom)
                if ref_merged.geom_type == "LineString":
                    ref_line = ref_merged
                else:
                    ref_coords: list[tuple[float, ...]] = []
                    for part in ref_geom.geoms:
                        ref_coords.extend(list(part.coords))
                    ref_line = LineString(ref_coords)
            else:
                ref_line = ref_geom

            start_point = Point(ref_line.coords[0])
            end_point = Point(ref_line.coords[-1])
            ref_start_dist = line.project(start_point)
            ref_end_dist = line.project(end_point)

            if ref_start_dist > ref_end_dist:
                ref_start_dist, ref_end_dist = ref_end_dist, ref_start_dist

            ref_fp_ranges.append((ref_start_dist, ref_end_dist, ref_area))
            ref_fp_projected_ranges.append((ref_id, ref_start_dist, ref_end_dist))

        # Build all segment boundaries: upstream end, each virtual nexus, downstream end
        segment_boundaries: list[tuple[float, float, int]] = []  # (start_dist, end_dist, dn_vnex_id)

        # Create upstream nexus at the start of the NHF flowpath
        if line_geom.geom_type == "MultiLineString":
            start_coord = list(line_geom.geoms)[0].coords[0]
        else:
            start_coord = line.coords[0]

        upstream_vnex_id: int = nhf_id
        nhf_id += 1
        new_virtual_nexus_data.append(
            {
                "virtual_nex_id": upstream_vnex_id,
                "vpu_id": nhf_fp_vpu_id,
                "geometry": Point(start_coord),
            }
        )
        nhf_fp_to_upstream_vnex[nhf_fp_id] = upstream_vnex_id

        prev_dist = 0.0
        skipped_nexuses: list[tuple[int, float]] = []
        for vnex_id, vnex_dist in projected_nexuses:
            if (vnex_dist - prev_dist) >= 1.0:
                segment_boundaries.append((prev_dist, vnex_dist, vnex_id))
            else:
                skipped_nexuses.append((vnex_id, vnex_dist))
            prev_dist = vnex_dist

        # Downstream tail: from last virtual nexus to downstream end of line
        if (total_length - prev_dist) >= 1.0:
            tail_vnex_id = nhf_id
            nhf_id += 1

            if line_geom.geom_type == "MultiLineString":
                end_coord = list(line_geom.geoms)[-1].coords[-1]
            else:
                end_coord = line.coords[-1]

            new_virtual_nexus_data.append(
                {
                    "virtual_nex_id": tail_vnex_id,
                    "vpu_id": nhf_fp_vpu_id,
                    "geometry": Point(end_coord),
                }
            )
            segment_boundaries.append((prev_dist, total_length, tail_vnex_id))

        # Phase 1: compute overlap-based area for each segment from divide geometries
        segment_geometries: list[LineString] = []
        segment_overlap_areas: list[float] = []
        segment_lengths: list[float] = []
        for seg_start_dist, seg_end_dist, _ in segment_boundaries:
            segment = substring(line, seg_start_dist, seg_end_dist)
            segment_geometries.append(segment)
            seg_len = segment.length if not segment.is_empty else 0.0
            segment_lengths.append(seg_len)

            seg_area = 0.0
            for ref_start, ref_end, ref_area in ref_fp_ranges:
                ref_length = ref_end - ref_start
                if ref_length <= 0:
                    continue
                overlap_start = max(seg_start_dist, ref_start)
                overlap_end = min(seg_end_dist, ref_end)
                overlap_length = max(0.0, overlap_end - overlap_start)
                if overlap_length > 0:
                    fraction = min(overlap_length / ref_length, 1.0)
                    seg_area += ref_area * fraction
            segment_overlap_areas.append(seg_area)

        # Phase 2: distribute any remaining area by segment length
        # If overlap areas exceed the budget (e.g. braided projections), scale down
        total_overlap_area = sum(segment_overlap_areas)
        if total_overlap_area > remaining_area and total_overlap_area > 0:
            scale = remaining_area / total_overlap_area
            segment_overlap_areas = [a * scale for a in segment_overlap_areas]
            total_overlap_area = remaining_area
        deficit_area = max(remaining_area - total_overlap_area, 0.0)
        total_seg_length = sum(segment_lengths)

        # Emit segments and track vnex -> downstream VFP mapping
        emitted_segments: list[tuple[int, int]] = []  # (dn_vnex_id, virtual_fp_id)
        prev_dn_vnex_id = upstream_vnex_id  # None for headwaters
        seg_order = 0
        for i, (_seg_start_dist, _seg_end_dist, dn_vnex_id) in enumerate(segment_boundaries):
            seg_len = segment_lengths[i]
            if seg_len == 0:
                continue

            segment = segment_geometries[i]

            # Overlap area + proportional share of the deficit
            length_fraction = seg_len / total_seg_length if total_seg_length > 0 else 0.0
            segment_area = segment_overlap_areas[i] + deficit_area * length_fraction
            percentage = segment_area / total_nhf_area if total_nhf_area > 0 else 0.0

            vfp_id = nhf_id
            mainstem_vfp_data.append(
                {
                    "virtual_fp_id": vfp_id,
                    "dn_virtual_nex_id": dn_vnex_id,
                    "up_virtual_nex_id": prev_dn_vnex_id,
                    "div_id": nhf_fp_id,
                    "length_km": seg_len / 1e3,
                    "area_sqkm": segment_area,
                    "percentage_area_contribution": percentage,
                    "segment_order": seg_order,
                    "vpu_id": nhf_fp_vpu_id,
                    "geometry": segment,
                }
            )
            emitted_segments.append((dn_vnex_id, vfp_id))
            prev_dn_vnex_id = dn_vnex_id
            seg_order += 1
            nhf_id += 1

        # Upstream nexus points to the first emitted segment's VFP
        if emitted_segments and upstream_vnex_id is not None:
            vnex_to_dn_vfp[upstream_vnex_id] = emitted_segments[0][1]

        # Each vnex between consecutive segments points to the next segment's VFP
        for j in range(len(emitted_segments) - 1):
            mid_vnex_id = emitted_segments[j][0]
            downstream_vfp_id = emitted_segments[j + 1][1]
            vnex_to_dn_vfp[mid_vnex_id] = downstream_vfp_id

        # Build vfp_id -> segment_order lookup from segments just emitted for this NHF fp
        vfp_id_to_seg_order: dict[int, int] = {
            vfp_id: order for order, (_, vfp_id) in enumerate(emitted_segments)
        }

        # Assign each reference FP to the mainstem segment containing its midpoint
        if emitted_segments:
            # Build list of (seg_start, seg_end, vfp_id) from emitted segments
            emitted_by_vnex: dict[int, int] = dict(emitted_segments)
            emitted_ranges: list[tuple[float, float, int]] = []
            for i, (seg_start_dist, seg_end_dist, vnex_id) in enumerate(segment_boundaries):
                if segment_lengths[i] == 0:
                    continue
                if vnex_id in emitted_by_vnex:
                    emitted_ranges.append((seg_start_dist, seg_end_dist, emitted_by_vnex[vnex_id]))

            # Map skipped nexuses to the containing (or nearest) emitted segment
            for skipped_vnex_id, skipped_dist in skipped_nexuses:
                for seg_start, seg_end, vfp_id in emitted_ranges:
                    if seg_start <= skipped_dist <= seg_end:
                        vnex_to_dn_vfp[skipped_vnex_id] = vfp_id
                        break
                else:
                    if emitted_ranges:
                        nearest = min(
                            emitted_ranges,
                            key=lambda r: min(abs(skipped_dist - r[0]), abs(skipped_dist - r[1])),
                        )
                        vnex_to_dn_vfp[skipped_vnex_id] = nearest[2]

            # Phase 1: assign one ref FP per VFP segment (coverage guarantee)
            assigned_refs: set[str] = set()
            for seg_start, seg_end, vfp_id in emitted_ranges:
                best_ref_id: str | None = None
                best_score = float("-inf")
                for ref_id, ref_start, ref_end in ref_fp_projected_ranges:
                    if ref_id in assigned_refs:
                        continue
                    overlap = max(0.0, min(seg_end, ref_end) - max(seg_start, ref_start))
                    score = (
                        overlap
                        if overlap > 0
                        else -abs((ref_start + ref_end) / 2 - (seg_start + seg_end) / 2)
                    )
                    if score > best_score:
                        best_score = score
                        best_ref_id = ref_id
                if best_ref_id is not None:
                    ref_to_vfp[best_ref_id] = vfp_id
                    ref_to_segment_order[best_ref_id] = vfp_id_to_seg_order.get(vfp_id, 0)
                    assigned_refs.add(best_ref_id)

            # Phase 2: assign remaining ref FPs to best-matching VFP
            for ref_id, ref_start, ref_end in ref_fp_projected_ranges:
                if ref_id in assigned_refs:
                    continue
                best_vfp_id: int | None = None
                best_score = float("-inf")
                for seg_start, seg_end, vfp_id in emitted_ranges:
                    overlap = max(0.0, min(seg_end, ref_end) - max(seg_start, ref_start))
                    score = (
                        overlap
                        if overlap > 0
                        else -abs((ref_start + ref_end) / 2 - (seg_start + seg_end) / 2)
                    )
                    if score > best_score:
                        best_score = score
                        best_vfp_id = vfp_id
                if best_vfp_id is not None:
                    ref_to_vfp[ref_id] = best_vfp_id
                    ref_to_segment_order[ref_id] = vfp_id_to_seg_order.get(best_vfp_id, 0)

            # Project ALL ref FPs (including tributaries) to assign mainstem_virtual_fp_id
            all_ref_ids = nhf_fp_to_all_ref_ids.get(nhf_fp_id, [])
            for ref_id in all_ref_ids:
                if ref_id not in fp_lookup:
                    continue
                ref_fp = fp_lookup[ref_id]
                ref_geom = ref_fp.get("shapely_geometry")
                if ref_geom is None or ref_geom.is_empty:
                    continue

                if ref_geom.geom_type == "MultiLineString":
                    ref_merged = linemerge(ref_geom)
                    if ref_merged.geom_type == "LineString":
                        ref_line = ref_merged
                    else:
                        ref_coords_list: list[tuple[float, ...]] = []
                        for part in ref_geom.geoms:
                            ref_coords_list.extend(list(part.coords))
                        ref_line = LineString(ref_coords_list)
                else:
                    ref_line = ref_geom

                ref_mid = line.project(ref_line.interpolate(0.5, normalized=True))

                for seg_start, seg_end, vfp_id in emitted_ranges:
                    if seg_start <= ref_mid <= seg_end:
                        all_ref_to_mainstem_vfp[ref_id] = vfp_id
                        break
                else:
                    if emitted_ranges:
                        nearest = min(
                            emitted_ranges,
                            key=lambda r: min(abs(ref_mid - r[0]), abs(ref_mid - r[1])),
                        )
                        all_ref_to_mainstem_vfp[ref_id] = nearest[2]

    return (
        mainstem_vfp_data,
        new_virtual_nexus_data,
        vnex_to_dn_vfp,
        nhf_fp_to_upstream_vnex,
        ref_to_vfp,
        ref_to_segment_order,
        all_ref_to_mainstem_vfp,
        nhf_id,
    )


def _build_hydrofabric(
    start_id: str,
    aggregate_data: Aggregations,
    classifications: Classifications,
    partition_data: dict[str, Any],
    cfg: HFConfig,
    id_offset: int = 0,
) -> dict[str, (gpd.GeoDataFrame | pd.DataFrame) | list[dict[str, Any]] | int | None]:
    """Builds the hydrofabric connectivity

    Parameters
    ----------
    start_id: str
        The starting ID from the reference fabric for the outlet
    aggregate_data: Aggregations
        The Aggregations dataclass
    classifications: Classifications
        The Classifications dataclass
    partition_data: dict[str, Any]
        The partition data for the outlet
    cfg: HFConfig
        The config
    id_offset: int = 0
        The starting ID

    Returns
    -------
    result
        All hf layers + the ending outlet
    """
    fp_lookup: dict[str, dict[str, Any]] = partition_data["fp_lookup"]
    graph = partition_data["subgraph"]
    node_indices: dict[str, int] = partition_data["node_indices"]

    # Build hydroseq lookup for O(1) downstream lookups
    fp_by_hydroseq: dict[Any, dict[str, Any]] = {row["hydroseq"]: row for row in fp_lookup.values()}

    # Build ref_id -> unit lookup
    ref_id_to_unit, ref_fp_id_to_percentage = _order_aggregates_base(aggregate_data)

    # Initialize processing
    to_process: deque[str] = deque([start_id])
    visited_ref_ids: set[str] = set()
    processed_units: set[frozenset[str]] = set()

    fp_data: list[dict[str, Any]] = []
    div_data: list[dict[str, Any]] = []
    nexus_data: list[dict[str, Any]] = []
    reference_flowpaths_data: list[dict[str, Any]] = []
    ref_fp_id_to_index: dict[str, int] = {}

    ref_id_to_new_id: dict[str, int] = {}
    downstream_fp_to_nexus: dict[int, int] = {}

    # Single ID counter for flowpaths, divides, and virtual flowpaths
    # Nexuses reuse these IDs since they're in a different table
    nhf_id = 1 + id_offset

    to_process.extend(list(classifications.force_queue_flowpaths))

    # Main processing loop
    while to_process:
        current_ref_id = to_process.popleft()

        if current_ref_id in visited_ref_ids:
            continue
        visited_ref_ids.add(current_ref_id)

        # Skip virtual flowpaths
        if current_ref_id in classifications.non_nextgen_flowpaths:
            continue

        # Validate current flowpath
        if current_ref_id not in ref_id_to_unit:
            if not fp_data:  # Starting outlet case
                continue
            if current_ref_id in classifications.aggregation_set:  # headwater case
                continue
            logger.error(f"Flowpath {current_ref_id} not found in any unit and not virtual")
            raise ValueError(f"Flowpath {current_ref_id} not found in any unit")

        unit_info = ref_id_to_unit[current_ref_id]
        unit_ref_ids = frozenset(unit_info["all_ref_ids"])

        # Skip if already processed
        if unit_ref_ids in processed_units:
            continue
        processed_units.add(unit_ref_ids)

        # Assign ID for this unit (fp_id and div_id will be the same)
        unit_id = nhf_id
        nhf_id += 1

        # Assign new ID to all refs in this unit
        for ref_id in unit_ref_ids:
            ref_id_to_new_id[ref_id] = unit_id

        # Get unit data
        unit: dict[str, Any] = unit_info["unit"]
        unit_type: str = unit_info["type"]
        ref_ids: list[str] = list(unit_info["all_ref_ids"])

        # Get original flowpaths
        original_fps: list[dict[str, Any]] = [fp_lookup[ref_id] for ref_id in ref_ids if ref_id in fp_lookup]

        if not original_fps:
            logger.debug(f"No flowpaths found for unit {ref_ids}")
            # Queue upstream
            lookup_id = unit_info.get("up_id", current_ref_id)
            if lookup_id in node_indices:
                lookup_idx = node_indices[lookup_id]
                upstream_ids = [graph[idx] for idx in graph.predecessor_indices(lookup_idx)]
                to_process.extend(upstream_ids)
            continue

        # Find outlet flowpath (lowest hydroseq)
        if unit_type == "aggregate" and unit_info.get("dn_id"):
            outlet_fp = fp_lookup.get(unit_info["dn_id"]) or min(original_fps, key=lambda x: x["hydroseq"])
        else:
            outlet_fp = (
                original_fps[0] if len(original_fps) == 1 else min(original_fps, key=lambda x: x["hydroseq"])
            )

        # Find downstream unit
        dn_hydroseq = outlet_fp.get("dnhydroseq", 0)
        downstream_unit_id = None

        if dn_hydroseq and dn_hydroseq != 0:
            downstream_fp = fp_by_hydroseq.get(dn_hydroseq)
            if downstream_fp:
                downstream_ref_id = str(downstream_fp["flowpath_id"])
                downstream_unit_id = ref_id_to_new_id.get(downstream_ref_id)

        # Check for divide polygon
        polygon_geom: BaseGeometry | None = unit.get("polygon_geometry")
        if polygon_geom is None or polygon_geom.is_empty:
            logger.error(f"Unit {ref_ids} has no divide polygon. Will not be able to run NGEN using it")
            raise ValueError(f"Unit {ref_ids} has no divide polygon. Will not be able to run NGEN using it")

        # Get or create nexus - nexus reuses the unit_id (doesn't increment nhf_id)
        if downstream_unit_id is not None and downstream_unit_id in downstream_fp_to_nexus:
            nexus_id = downstream_fp_to_nexus[downstream_unit_id]
        else:
            # Nexus reuses the current unit_id (no new ID allocation)
            nexus_id = unit_id

            # Create nexus at downstream end of flowpath
            line_geom = unit["line_geometry"]
            if line_geom.geom_type == "MultiLineString":
                end_coord = list(line_geom.geoms)[-1].coords[-1]
            else:
                end_coord = line_geom.coords[-1]

            nexus_data.append(
                {
                    "nex_id": int(nexus_id),
                    "dn_fp_id": downstream_unit_id,
                    "vpu_id": unit["vpu_id"],
                    "geometry": Point(end_coord),
                }
            )

            if downstream_unit_id is not None:
                downstream_fp_to_nexus[downstream_unit_id] = nexus_id

        # Create flowpath with unit_id
        fp_data.append(
            {
                "fp_id": int(unit_id),
                "fp_to_id": int(downstream_unit_id) if downstream_unit_id is not None else None,
                "dn_nex_id": int(nexus_id),
                "up_nex_id": None,  # Will fix later
                "div_id": int(unit_id),
                "vpu_id": unit["vpu_id"],
                "length_km": unit["length_km"],
                "area_sqkm": unit["area_sqkm"],
                "geometry": unit["line_geometry"],
            }
        )

        # Create divide with unit_id
        div_data.append(
            {
                "div_id": int(unit_id),
                "vpu_id": unit["vpu_id"],
                "type": unit_type,
                "area_sqkm": unit["area_sqkm"],
                "geometry": polygon_geom,
            }
        )

        # Create reference entries
        for ref_id in ref_ids:
            idx = len(reference_flowpaths_data)

            reference_flowpaths_data.append(
                {
                    "ref_fp_id": int(ref_id),
                    "fp_id": int(unit_id),
                    "virtual_fp_id": None,
                    "mainstem_virtual_fp_id": None,
                    "segment_order": None,
                    "div_id": int(unit_id),
                }
            )
            ref_fp_id_to_index[ref_id] = idx

        _queue_all_unit_upstreams(unit_info, ref_ids, current_ref_id, graph, node_indices, to_process)

    # Fix up_nex_id references
    nexus_by_downstream_fp: dict[int, int] = {
        nex["dn_fp_id"]: nex["nex_id"] for nex in nexus_data if nex.get("dn_fp_id") is not None
    }
    for fp_entry in fp_data:
        fp_id = fp_entry["fp_id"]
        if fp_id in nexus_by_downstream_fp:
            fp_entry["up_nex_id"] = nexus_by_downstream_fp[fp_id]

    virtual_fp_data: list[dict[str, Any]] = []
    virtual_nexus_data_dict: dict[int, dict[str, Any]] = {}
    ref_id_to_virtual_fp_id: dict[str, int] = {}
    ref_id_to_nexus: dict[str, int] = {}
    virtual_nex_to_nhf_fp: dict[int, int] = {}  # virtual_nex_id -> NHF fp_id

    # Continue using the same nhf_id counter for virtual flowpaths
    # Virtual nexuses will also reuse virtual flowpath IDs

    # Process non_nextgen_virtual_flowpaths
    sorted_non_nextgen_virtual = sorted(
        aggregate_data.non_nextgen_virtual_flowpaths, key=lambda unit: unit["hydroseq"]
    )
    for unit in sorted_non_nextgen_virtual:
        ref_ids = unit["ref_ids"]
        line_geom = unit["line_geometry"]
        dn_ref_id = str(unit["dn_id"])
        ds_id: str = str(int(fp_lookup[dn_ref_id]["flowpath_toid"]))

        # Assign virtual flowpath ID from nhf_id
        virtual_fp_id = nhf_id
        nhf_id += 1

        ref_id_to_virtual_fp_id[dn_ref_id] = virtual_fp_id

        if dn_ref_id in ref_fp_id_to_index:
            idx = ref_fp_id_to_index[dn_ref_id]
            reference_flowpaths_data[idx]["virtual_fp_id"] = virtual_fp_id
            reference_flowpaths_data[idx]["segment_order"] = 0
        else:
            idx = len(reference_flowpaths_data)
            reference_flowpaths_data.append(
                {
                    "ref_fp_id": int(dn_ref_id),
                    "fp_id": None,
                    "virtual_fp_id": int(virtual_fp_id),
                    "mainstem_virtual_fp_id": None,
                    "segment_order": 0,
                    "div_id": int(ref_id_to_new_id[ds_id]),
                }
            )
            ref_fp_id_to_index[dn_ref_id] = idx

        # Add reference_flowpaths entries for upstream members of the virtual chain
        for ref_id in ref_ids:
            ref_id_str = str(ref_id)
            if ref_id_str == dn_ref_id:
                continue
            if ref_id_str in ref_fp_id_to_index:
                existing_idx = ref_fp_id_to_index[ref_id_str]
                reference_flowpaths_data[existing_idx]["virtual_fp_id"] = virtual_fp_id
                reference_flowpaths_data[existing_idx]["segment_order"] = 0
            else:
                new_idx = len(reference_flowpaths_data)
                reference_flowpaths_data.append(
                    {
                        "ref_fp_id": int(ref_id),
                        "fp_id": None,
                        "virtual_fp_id": int(virtual_fp_id),
                        "mainstem_virtual_fp_id": None,
                        "segment_order": 0,
                        "div_id": int(ref_id_to_new_id[ds_id]),
                    }
                )
                ref_fp_id_to_index[ref_id_str] = new_idx

        # default area contribution from is 0% as flowpaths with no divide have 0km2 of drainage area
        percentage = sum(ref_fp_id_to_percentage.get(_ref_id, 0.0) for _ref_id in ref_ids)

        if ds_id and ds_id in ref_id_to_nexus:
            virtual_nexus_id = ref_id_to_nexus[ds_id]
        else:
            if line_geom.geom_type == "MultiLineString":
                endpoint = Point(list(line_geom.geoms)[-1].coords[-1])
            else:
                endpoint = Point(line_geom.coords[-1])

            # Virtual nexus reuses the virtual_fp_id (no new ID allocation)
            virtual_nexus_id = virtual_fp_id

            virtual_nexus_data_dict[virtual_nexus_id] = {
                "virtual_nex_id": virtual_nexus_id,
                "vpu_id": unit["vpu_id"],
                "geometry": endpoint,
            }

            if dn_ref_id:
                ref_id_to_nexus[dn_ref_id] = virtual_nexus_id

        # Track which NHF flowpath this virtual nexus sits on
        if ds_id in ref_id_to_new_id:
            virtual_nex_to_nhf_fp[virtual_nexus_id] = ref_id_to_new_id[ds_id]

        virtual_fp_data.append(
            {
                "virtual_fp_id": int(virtual_fp_id),
                "dn_virtual_nex_id": int(virtual_nexus_id),
                "up_virtual_nex_id": None,
                "div_id": int(ref_id_to_new_id[ds_id]),
                "length_km": unit["length_km"],
                "area_sqkm": unit["area_sqkm"],
                "percentage_area_contribution": percentage,
                "segment_order": 0,
                "vpu_id": unit["vpu_id"],
                "geometry": line_geom,
            }
        )

    # Convert nexus dict to list
    virtual_nexus_data = list(virtual_nexus_data_dict.values())

    # Create main-stem virtual flowpath segments along NHF flowpaths
    nhf_fp_to_vnexuses: dict[int, list[tuple[int, Point]]] = defaultdict(list)
    for vnex_id, nhf_fp_id in virtual_nex_to_nhf_fp.items():
        vnex_point = virtual_nexus_data_dict[vnex_id]["geometry"]
        nhf_fp_to_vnexuses[nhf_fp_id].append((vnex_id, vnex_point))

    # Accumulate tributary VFP percentages per NHF flowpath (div_id)
    nhf_fp_tributary_pct: dict[int, float] = defaultdict(float)
    for vfp_entry in virtual_fp_data:
        nhf_fp_tributary_pct[vfp_entry["div_id"]] += vfp_entry["percentage_area_contribution"]

    nhf_fp_to_ref_ids: dict[int, list[str]] = defaultdict(list)
    for ref_id, nhf_id_val in ref_id_to_new_id.items():
        nhf_fp_to_ref_ids[nhf_id_val].append(ref_id)

    # Build mapping of ALL ref FPs per div_id (including tributaries)
    nhf_fp_to_all_ref_ids: dict[int, list[str]] = defaultdict(list)
    for entry in reference_flowpaths_data:
        nhf_fp_to_all_ref_ids[entry["div_id"]].append(str(entry["ref_fp_id"]))

    div_lookup: dict[str, dict[str, Any]] | None = partition_data.get("div_lookup")

    (
        mainstem_vfp_data,
        mainstem_vnex_data,
        vnex_to_dn_vfp,
        nhf_fp_upstream_vnex,
        mainstem_ref_to_vfp,
        mainstem_ref_to_segment_order,
        all_ref_to_mainstem_vfp,
        nhf_id,
    ) = _create_mainstem_virtual_flowpaths(
        nhf_fp_to_vnexuses=nhf_fp_to_vnexuses,
        nhf_fp_to_ref_ids=nhf_fp_to_ref_ids,
        nhf_fp_to_all_ref_ids=nhf_fp_to_all_ref_ids,
        nhf_fp_tributary_pct=nhf_fp_tributary_pct,
        fp_data=fp_data,
        fp_lookup=fp_lookup,
        div_lookup=div_lookup,
        nhf_id=nhf_id,
    )
    virtual_fp_data.extend(mainstem_vfp_data)
    virtual_nexus_data.extend(mainstem_vnex_data)

    # Update reference_flowpaths entries with mainstem virtual_fp_id mappings
    for ref_id_str, vfp_id in mainstem_ref_to_vfp.items():
        if ref_id_str in ref_fp_id_to_index:
            idx = ref_fp_id_to_index[ref_id_str]
            reference_flowpaths_data[idx]["virtual_fp_id"] = vfp_id
            reference_flowpaths_data[idx]["segment_order"] = mainstem_ref_to_segment_order.get(ref_id_str, 0)

    # Update reference_flowpaths entries with mainstem_virtual_fp_id for all ref FPs
    for ref_id_str, vfp_id in all_ref_to_mainstem_vfp.items():
        if ref_id_str in ref_fp_id_to_index:
            idx = ref_fp_id_to_index[ref_id_str]
            reference_flowpaths_data[idx]["mainstem_virtual_fp_id"] = vfp_id

    # Create a single virtual flowpath for divides that have no VFPs yet
    # Build reverse lookup: div_id -> list of indices in reference_flowpaths_data
    div_id_to_ref_indices: dict[int, list[int]] = defaultdict(list)
    for i, entry in enumerate(reference_flowpaths_data):
        div_id_to_ref_indices[entry["div_id"]].append(i)

    nhf_fp_to_fallback_vfp: dict[int, int] = {}
    divs_with_vfps: set[int] = {vfp["div_id"] for vfp in virtual_fp_data}
    for fp_entry in fp_data:
        fp_id = fp_entry["fp_id"]
        if fp_id in divs_with_vfps:
            continue

        line_geom = fp_entry["geometry"]
        if line_geom is None or line_geom.is_empty:
            continue

        # Create upstream nexus for fallback VFPs
        if line_geom.geom_type == "MultiLineString":
            start_coord = list(line_geom.geoms)[0].coords[0]
        else:
            start_coord = line_geom.coords[0]

        upstream_vnex_id_fb: int = nhf_id
        nhf_id += 1
        virtual_nexus_data.append(
            {
                "virtual_nex_id": upstream_vnex_id_fb,
                "vpu_id": fp_entry["vpu_id"],
                "geometry": Point(start_coord),
            }
        )
        nhf_fp_upstream_vnex[fp_id] = upstream_vnex_id_fb

        # Create virtual nexus at downstream end of flowpath
        if line_geom.geom_type == "MultiLineString":
            end_coord = list(line_geom.geoms)[-1].coords[-1]
        else:
            end_coord = line_geom.coords[-1]

        vnex_id = nhf_id
        nhf_id += 1

        virtual_nexus_data.append(
            {
                "virtual_nex_id": vnex_id,
                "vpu_id": fp_entry["vpu_id"],
                "geometry": Point(end_coord),
            }
        )

        fallback_vfp_id = nhf_id
        nhf_fp_to_fallback_vfp[fp_id] = fallback_vfp_id
        virtual_fp_data.append(
            {
                "virtual_fp_id": fallback_vfp_id,
                "dn_virtual_nex_id": vnex_id,
                "up_virtual_nex_id": upstream_vnex_id_fb,
                "div_id": fp_id,
                "length_km": fp_entry["length_km"],
                "area_sqkm": fp_entry["area_sqkm"],
                "percentage_area_contribution": 1.0,
                "segment_order": 0,
                "vpu_id": fp_entry["vpu_id"],
                "geometry": line_geom,
            }
        )
        nhf_id += 1

        vnex_to_dn_vfp[upstream_vnex_id_fb] = fallback_vfp_id

        # Update reference_flowpaths entries for this divide with the fallback virtual_fp_id
        for ref_idx in div_id_to_ref_indices.get(fp_id, []):
            if reference_flowpaths_data[ref_idx]["virtual_fp_id"] is None:
                reference_flowpaths_data[ref_idx]["virtual_fp_id"] = fallback_vfp_id
                reference_flowpaths_data[ref_idx]["segment_order"] = 0
            if reference_flowpaths_data[ref_idx]["mainstem_virtual_fp_id"] is None:
                reference_flowpaths_data[ref_idx]["mainstem_virtual_fp_id"] = fallback_vfp_id

    # Cross-divide virtual connectivity: wire tail nexuses to downstream divides
    fp_to_id_map: dict[int, int | None] = {fp["fp_id"]: fp.get("fp_to_id") for fp in fp_data}
    vfp_by_div: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for vfp_entry in virtual_fp_data:
        vfp_by_div[vfp_entry["div_id"]].append(vfp_entry)
    existing_vnex_ids = {vn["virtual_nex_id"] for vn in virtual_nexus_data}
    removed_vnex_ids: set[int] = set()

    for fp_id, downstream_fp_id in fp_to_id_map.items():
        if downstream_fp_id is None:
            continue

        # Case 1: Downstream divide has mainstem segments with an upstream nexus
        if downstream_fp_id in nhf_fp_upstream_vnex:
            upstream_vnex = nhf_fp_upstream_vnex[downstream_fp_id]
            for vfp_entry in vfp_by_div.get(fp_id, []):
                old_nex_id = vfp_entry["dn_virtual_nex_id"]
                if old_nex_id not in vnex_to_dn_vfp and old_nex_id in existing_vnex_ids:
                    # Don't rewire if it would create a cycle (up_nex == dn_nex)
                    if vfp_entry["up_virtual_nex_id"] == upstream_vnex:
                        continue
                    vfp_entry["dn_virtual_nex_id"] = upstream_vnex
                    removed_vnex_ids.add(old_nex_id)
                    existing_vnex_ids.discard(old_nex_id)

        # Case 2: Downstream divide has a fallback VFP (no mainstem segments)
        elif downstream_fp_id in nhf_fp_to_fallback_vfp:
            fallback_vfp_id = nhf_fp_to_fallback_vfp[downstream_fp_id]
            for vfp_entry in vfp_by_div.get(fp_id, []):
                old_nex_id = vfp_entry["dn_virtual_nex_id"]
                if old_nex_id not in vnex_to_dn_vfp and old_nex_id in existing_vnex_ids:
                    vnex_to_dn_vfp[old_nex_id] = fallback_vfp_id

    # Batch-remove replaced tail nexuses and rewrite any remaining dangling references
    if removed_vnex_ids:
        virtual_nexus_data[:] = [
            vn for vn in virtual_nexus_data if vn["virtual_nex_id"] not in removed_vnex_ids
        ]
        surviving_vnex_ids = {vn["virtual_nex_id"] for vn in virtual_nexus_data}
        for vfp_entry in virtual_fp_data:
            dn = vfp_entry["dn_virtual_nex_id"]
            if dn in removed_vnex_ids and dn not in surviving_vnex_ids:
                # Find the upstream nexus of the downstream divide this was wired to
                downstream_fp_id = fp_to_id_map.get(vfp_entry["div_id"])
                if downstream_fp_id is not None and downstream_fp_id in nhf_fp_upstream_vnex:
                    candidate = nhf_fp_upstream_vnex[downstream_fp_id]
                    if candidate != vfp_entry["up_virtual_nex_id"]:
                        vfp_entry["dn_virtual_nex_id"] = candidate

    # Set dn_virtual_fp_id on all virtual nexus records
    for vnex_entry in virtual_nexus_data:
        vnex_entry["dn_virtual_fp_id"] = vnex_to_dn_vfp.get(vnex_entry["virtual_nex_id"])

    # Merge coincident virtual nexuses: when multiple nexuses share the same
    # location (e.g. a tributary self-nexus and a mainstem tail nexus), keep
    # one and rewrite all VFP references to the survivor.
    # Skip merging nexuses that would create a self-loop (up_nex == dn_nex) on any VFP.
    coord_to_nexuses: dict[tuple[float, float], list[int]] = defaultdict(list)
    for vnex_entry in virtual_nexus_data:
        g = vnex_entry["geometry"]
        key = (round(g.x, 6), round(g.y, 6))
        coord_to_nexuses[key].append(vnex_entry["virtual_nex_id"])

    # Build sets of nexuses that must not be merged:
    # 1. Pairs where a VFP uses one as up_nex and the other as dn_nex (would create a cycle)
    # 2. Nexuses used as up_virtual_nex_id — merging two of these creates a divergence
    no_merge_pairs: set[frozenset[int]] = set()
    up_nex_ids: set[int] = set()
    for vfp_entry in virtual_fp_data:
        up = vfp_entry["up_virtual_nex_id"]
        dn = vfp_entry["dn_virtual_nex_id"]
        if up is not None:
            no_merge_pairs.add(frozenset((up, dn)))
            up_nex_ids.add(up)

    nex_remap: dict[int, int] = {}
    merged_nex_ids: set[int] = set()
    for _coord, nex_ids in coord_to_nexuses.items():
        if len(nex_ids) <= 1:
            continue
        keep = min(nex_ids)
        for nex_id in nex_ids:
            if nex_id != keep:
                if frozenset((nex_id, keep)) in no_merge_pairs:
                    continue
                # Don't merge two nexuses that are both used as up_virtual_nex_id
                if nex_id in up_nex_ids and keep in up_nex_ids:
                    continue
                nex_remap[nex_id] = keep
                merged_nex_ids.add(nex_id)
                # Transfer up_nex_id status to survivor
                if nex_id in up_nex_ids:
                    up_nex_ids.add(keep)
                # Merge dn_virtual_fp_id onto the survivor
                if vnex_to_dn_vfp.get(nex_id) is not None and vnex_to_dn_vfp.get(keep) is None:
                    vnex_to_dn_vfp[keep] = vnex_to_dn_vfp[nex_id]

    if merged_nex_ids:
        virtual_nexus_data[:] = [
            vn for vn in virtual_nexus_data if vn["virtual_nex_id"] not in merged_nex_ids
        ]
        # Update dn_virtual_fp_id on surviving nexuses
        for vnex_entry in virtual_nexus_data:
            vnex_entry["dn_virtual_fp_id"] = vnex_to_dn_vfp.get(vnex_entry["virtual_nex_id"])
        # Rewrite VFP nexus references
        for vfp_entry in virtual_fp_data:
            dn = vfp_entry["dn_virtual_nex_id"]
            if dn in nex_remap:
                vfp_entry["dn_virtual_nex_id"] = nex_remap[dn]
            up = vfp_entry["up_virtual_nex_id"]
            if up is not None and up in nex_remap:
                vfp_entry["up_virtual_nex_id"] = nex_remap[up]

    # Break 1-hop cycles: if nexus N routes to VFP V but V drains into N, clear the routing.
    vfp_dn_nex_lookup: dict[int, int] = {
        vfp_e["virtual_fp_id"]: vfp_e["dn_virtual_nex_id"] for vfp_e in virtual_fp_data
    }
    for vnex_entry in virtual_nexus_data:
        dn_vfp = vnex_entry.get("dn_virtual_fp_id")
        if dn_vfp is not None and vfp_dn_nex_lookup.get(dn_vfp) == vnex_entry["virtual_nex_id"]:
            vnex_entry["dn_virtual_fp_id"] = None
            vnex_to_dn_vfp.pop(vnex_entry["virtual_nex_id"], None)

    # Wire up_virtual_nex_id on VFPs that are missing it
    # Use remapped nexus IDs so we don't wire to merged (removed) nexuses
    # Skip if the candidate nexus is the VFP's own dn_virtual_nex_id (would create a cycle)
    dn_vfp_to_vnex: dict[int, int] = {
        vfp_id: nex_remap.get(vnex_id, vnex_id) for vnex_id, vfp_id in vnex_to_dn_vfp.items()
    }
    for vfp_entry in virtual_fp_data:
        if vfp_entry["up_virtual_nex_id"] is None and vfp_entry["virtual_fp_id"] in dn_vfp_to_vnex:
            candidate_nex = dn_vfp_to_vnex[vfp_entry["virtual_fp_id"]]
            if candidate_nex != vfp_entry["dn_virtual_nex_id"]:
                vfp_entry["up_virtual_nex_id"] = candidate_nex

    # Ensure every VFP appears in at least one ref FP's virtual_fp_id.
    # When a ref FP serves multiple VFPs (e.g. more VFPs than refs on an NHF
    # flowpath, or a tributary ref overwritten by mainstem processing),
    # duplicate the ref FP row so each VFP has its own entry.
    vfp_id_to_segment_order: dict[int, int] = {
        vfp["virtual_fp_id"]: vfp["segment_order"] for vfp in virtual_fp_data
    }
    covered_vfp_ids: set[int] = {
        entry["virtual_fp_id"] for entry in reference_flowpaths_data if entry.get("virtual_fp_id") is not None
    }
    for vfp_entry in virtual_fp_data:
        vfp_id = vfp_entry["virtual_fp_id"]
        if vfp_id in covered_vfp_ids:
            continue
        div_id = vfp_entry["div_id"]
        for ref_idx in div_id_to_ref_indices.get(div_id, []):
            donor = reference_flowpaths_data[ref_idx]
            reference_flowpaths_data.append(
                {
                    "ref_fp_id": donor["ref_fp_id"],
                    "fp_id": donor["fp_id"],
                    "virtual_fp_id": vfp_id,
                    "mainstem_virtual_fp_id": donor.get("mainstem_virtual_fp_id"),
                    "segment_order": vfp_id_to_segment_order.get(vfp_id, 0),
                    "div_id": div_id,
                }
            )
            covered_vfp_ids.add(vfp_id)
            break

    logger.debug(f"Built hydrofabric using ID range: {1 + id_offset} to {nhf_id - 1}")
    # Create GeoDataFrames
    try:
        flowpaths_gdf = gpd.GeoDataFrame(fp_data, crs=cfg.crs)
        flowpaths_gdf["fp_id"] = flowpaths_gdf["fp_id"].astype("Int64")
        flowpaths_gdf["fp_to_id"] = flowpaths_gdf["fp_to_id"].astype("Int64")
        flowpaths_gdf["dn_nex_id"] = flowpaths_gdf["dn_nex_id"].astype("Int64")
        flowpaths_gdf["up_nex_id"] = flowpaths_gdf["up_nex_id"].astype("Int64")

        divides_gdf = gpd.GeoDataFrame(div_data, crs=cfg.crs)
        nexus_gdf = gpd.GeoDataFrame(nexus_data, crs=cfg.crs)
        nexus_gdf["dn_fp_id"] = nexus_gdf["dn_fp_id"].astype("Int64")
        reference_flowpaths_df = pd.DataFrame(reference_flowpaths_data)
        reference_flowpaths_df["fp_id"] = reference_flowpaths_df["fp_id"].astype("Int64")
        reference_flowpaths_df["virtual_fp_id"] = reference_flowpaths_df["virtual_fp_id"].astype("Int64")
        reference_flowpaths_df["mainstem_virtual_fp_id"] = reference_flowpaths_df[
            "mainstem_virtual_fp_id"
        ].astype("Int64")
        reference_flowpaths_df["segment_order"] = reference_flowpaths_df["segment_order"].astype("Int64")

        # Virtual layers
        if virtual_fp_data:
            virtual_flowpaths_gdf = gpd.GeoDataFrame(virtual_fp_data, crs=cfg.crs)
            virtual_flowpaths_gdf["virtual_fp_id"] = virtual_flowpaths_gdf["virtual_fp_id"].astype("Int64")
            virtual_flowpaths_gdf["dn_virtual_nex_id"] = virtual_flowpaths_gdf["dn_virtual_nex_id"].astype(
                "Int64"
            )
            virtual_flowpaths_gdf["up_virtual_nex_id"] = virtual_flowpaths_gdf["up_virtual_nex_id"].astype(
                "Int64"
            )
            virtual_flowpaths_gdf.drop(
                columns=[c for c in ("div_id", "ref_fp_id") if c in virtual_flowpaths_gdf.columns],
                inplace=True,
            )
        else:
            virtual_flowpaths_gdf = None

        if virtual_nexus_data:
            virtual_nexus_gdf = gpd.GeoDataFrame(virtual_nexus_data, crs=cfg.crs)
            virtual_nexus_gdf["virtual_nex_id"] = virtual_nexus_gdf["virtual_nex_id"].astype("Int64")
            virtual_nexus_gdf["dn_virtual_fp_id"] = virtual_nexus_gdf["dn_virtual_fp_id"].astype("Int64")
        else:
            virtual_nexus_gdf = None

    except ValueError:
        flowpaths_gdf = None
        divides_gdf = None
        nexus_gdf = None
        reference_flowpaths_df = None
        virtual_flowpaths_gdf = None
        virtual_nexus_gdf = None

    return {
        "flowpaths": flowpaths_gdf,
        "divides": divides_gdf,
        "nexus": nexus_gdf,
        "reference_flowpaths": reference_flowpaths_df,
        "virtual_flowpaths": virtual_flowpaths_gdf,
        "virtual_nexus": virtual_nexus_gdf,
        "next_available_id": nhf_id,
    }
