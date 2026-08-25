"""A utils file for common hydrofabric building functions"""

from typing import Any

import geopandas as gpd
import pandas as pd
import rustworkx as rx


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

    final_flowpaths = gpd.GeoDataFrame(combined_flowpaths)
    final_divides = gpd.GeoDataFrame(combined_divides)
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

    if final_virtual_nexus is not None:
        if final_virtual_nexus.crs is not None and final_virtual_nexus.crs != crs:
            final_virtual_nexus = final_virtual_nexus.to_crs(crs)
        elif final_virtual_nexus.crs is None:
            final_virtual_nexus = final_virtual_nexus.set_crs(crs)

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
