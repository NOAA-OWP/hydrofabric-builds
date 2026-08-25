import argparse
import gc
import pickle
from pathlib import Path

import fiona
import geopandas as gpd
import yaml
from shapely.geometry import box


def main(cfg: dict) -> None:
    """
    Cinding the overlapping percentage area between reference fabric divides and merit unit basins

    :param cfg: configurations
    :return: None, but saves a pickle file in out directory
    """
    # ---- unpack config ----
    ref_fabric_path = cfg["paths"]["ref_fabric_path"]
    merit_path = cfg["paths"]["merit_path"]
    out_dir = Path(cfg["paths"]["out_dir"])

    name1_col = cfg["columns"]["name1"]
    name2_col = cfg["columns"]["name2"]

    equal_area_crs = cfg["crs"]["equal_area"]
    g2_assume_crs = cfg["crs"].get("g2_assume")  # may be None

    chunk_size = int(cfg["chunking"]["chunk_size"])

    checkpoint_name = cfg["checkpoint"]["checkpoint_name"]
    final_name = cfg["checkpoint"]["final_name"]

    out_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = out_dir / checkpoint_name
    final_path = out_dir / final_name

    # ---------- read & prepare g2 ONCE ----------
    print(f"Reading g2 from: {merit_path}")
    g2 = gpd.read_file(merit_path)

    if g2.crs is None and g2_assume_crs:
        # only do this if you KNOW g2 is really in this CRS
        print(f"Assigning CRS {g2_assume_crs} to g2 (no reproject, just set_crs)")
        g2 = g2.set_crs(g2_assume_crs, inplace=False)

    g2 = g2.to_crs(equal_area_crs)
    g2 = g2[[name2_col, "geometry"]].rename(columns={name2_col: "p2_name"})

    # build spatial index once
    _ = g2.sindex

    # ---------- count features in g1 without loading all ----------
    print(f"Counting features in g1 from: {ref_fabric_path}")
    with fiona.open(ref_fabric_path) as src:
        total_features = len(src)
        src_crs = src.crs

    print(f"Total features in g1: {total_features}")

    nested: dict[str, dict[str, float]] = {}
    start = 0
    chunk_id = 0

    while start < total_features:
        stop = min(start + chunk_size, total_features)
        chunk_id += 1
        print(f"\nReading g1 features {start}–{stop} (chunk {chunk_id})")

        # read ONLY this slice of g1
        g1_chunk = gpd.read_file(ref_fabric_path, layer="reference_divides", rows=slice(start, stop))

        # ensure CRS then reproject to equal-area
        if g1_chunk.crs is None and src_crs is not None:
            g1_chunk = g1_chunk.set_crs(src_crs)
        g1_chunk = g1_chunk.to_crs(equal_area_crs)

        # keep only needed columns
        g1_chunk = g1_chunk[[name1_col, "geometry"]].rename(
            columns={name1_col: "p1_name"}
        )
        g1_chunk["p1_area"] = g1_chunk.geometry.area

        if g1_chunk.empty:
            start = stop
            del g1_chunk
            gc.collect()
            continue

        # bbox for this chunk
        minx, miny, maxx, maxy = g1_chunk.total_bounds
        bbox = box(minx, miny, maxx, maxy)

        # candidate g2 polygons overlapping this bbox
        candidate_idx = list(g2.sindex.intersection(bbox.bounds))
        if not candidate_idx:
            print("  No g2 candidates intersect this chunk bbox.")
            start = stop
            del g1_chunk
            gc.collect()
            continue

        g2_chunk = g2.iloc[candidate_idx]

        # overlay: intersections only for this chunk
        print(f"  Overlay with {len(g1_chunk)} g1 and {len(g2_chunk)} g2 features...")
        inter = gpd.overlay(g1_chunk, g2_chunk, how="intersection")

        if not inter.empty:
            inter["overlap_area"] = inter.geometry.area
            inter["pct_of_p1"] = inter["overlap_area"] / inter["p1_area"]

            # update nested dict
            for p1_name, sub in inter.groupby("p1_name"):
                inner = nested.setdefault(p1_name, {})
                for _, row in sub.iterrows():
                    inner[row["p2_name"]] = float(row["pct_of_p1"])

            # checkpoint
            with open(checkpoint_path, "wb") as f:
                pickle.dump(nested, f, protocol=pickle.HIGHEST_PROTOCOL)
            print(f"  Checkpoint saved -> {checkpoint_path}")
        else:
            print("  No intersections in this chunk.")

        # free memory from this iteration
        del g1_chunk, g2_chunk, inter
        gc.collect()

        start = stop

    print(f"\nNumber of g1 polygons with overlaps: {len(nested)}")

    # final save
    with open(final_path, "wb") as f:
        pickle.dump(nested, f, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"Final nested dict saved to: {final_path}")


def parse_args() -> argparse.Namespace:
    """Config file parser"""
    parser = argparse.ArgumentParser(
        description="Compute polygon overlap percentages into a nested dict."
    )
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        required=True,
        help="Path to YAML config file (e.g., example_divides_mapping_config.yaml)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    """
    Script to find overlapping percentage area of a reference fabric divide covered by unit basins in MERIT.

    How to run in terminal:
    python examples/divides_mapping.py --config configs/example_divides_mapping_config.yaml

    The path to reference fabric and Merit file for North America should be modified based on where
    they are stored locally. You can find them on S3 data account.
    ref_fabric_path:   s3://hydrofabric-data/beta/sc_reference_fabric.gpkg
    Merit_path:        s3://hydrofabric-data/MERIT/cat_pfaf_7_MERIT_Hydro_v07_Basins_v01_bugfix1.shp
    The pkl results have been saved into S3 data account here, for future references:
    out_path: s3://hydrofabric-data/Q_song_et_al_2025/

    Running time on a mac with 16 GB memory: ~10 minutes
    """
    args = parse_args()
    with open(args.config) as f:
        cfg = yaml.safe_load(f)
    main(cfg)
