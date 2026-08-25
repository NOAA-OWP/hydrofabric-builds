"""Test whether the NHF hydrofabric flowpath network forms a directed acyclic graph.

The fp_id -> to_fp_id relationships are obtained by joining the flowpaths and nexus layers of the NHF GeoPackage.

Example usage:
    python examples/test_dag.py /path/to/nhf.gpkg
"""

from __future__ import annotations

import argparse
import sqlite3

import pandas as pd
import rustworkx as rx

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test whether NHF hydrofabric flowpath network is a DAG.")
    parser.add_argument("gpkg_path", help="Path to the NHF GeoPackage file.")
    args = parser.parse_args()

    print("=== NHF Hydrofabric DAG Test ===\n")
    print(f"GeoPackage: {args.gpkg_path}\n")

    con = sqlite3.connect(args.gpkg_path)

    # Join flowpaths to nexus to resolve fp_id -> to_fp_id relationships
    # WHERE clause filters out outlets (NULL downstream) since rustworkx
    # doesn't handle NAs internally (unlike accumulate_downstream)
    edges = pd.read_sql_query(
        """
        SELECT f.fp_id, n.dn_fp_id AS to_fp_id
        FROM flowpaths f
        LEFT JOIN nexus n ON f.dn_nex_id = n.nex_id
        WHERE n.dn_fp_id IS NOT NULL
        """,
        con,
    )
    con.close()

    # Map fp_ids to sequential indices for graph construction
    all_ids = pd.concat([edges["fp_id"], edges["to_fp_id"]]).unique()
    id_to_idx = {fp_id: idx for idx, fp_id in enumerate(all_ids)}

    # Build directed graph from edge list
    graph = rx.PyDiGraph()
    graph.add_nodes_from(range(len(all_ids)))
    graph.extend_from_edge_list(
        [(id_to_idx[src], id_to_idx[dst]) for src, dst in zip(edges["fp_id"], edges["to_fp_id"], strict=True)]
    )

    is_dag = rx.is_directed_acyclic_graph(graph)
    print(f"Flowpath network is DAG: {is_dag}\n")
