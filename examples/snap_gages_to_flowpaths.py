from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd

from hydrofabric_builds.streamflow_gauges.assign_fp_to_gage import run_assignment

if __name__ == "__main__":
    """
    Running with argeparse

    ### Serial Run Example: ###
    python3 examples/snap_gages_to_flowpaths.py \
  --flowpaths /home/farshid.rahmani/Documents/Dataset/HF/HF_beta_v2.3/sc_reference_fabric.gpkg \
  --gages /home/farshid.rahmani/Documents/Dataset/HF/usgs_gages_all_conus_AK_Pr.gpkg \
  --buffer-m 500

    ### Parallel Run Example: ###
    python3 examples/snap_gages_to_flowpaths.py \
  --flowpaths /home/farshid.rahmani/Documents/Dataset/HF/HF_beta_v2.3/sc_reference_fabric.gpkg \
  --gages /home/farshid.rahmani/Documents/Dataset/HF/usgs_gages_all_conus_AK_Pr.gpkg \
  --parallel --max-workers 2

    """
    parser = argparse.ArgumentParser(
        description="Assign USGS gages (points) to flowpaths using basin area matching."
    )
    parser.add_argument(
        "--flowpaths",
        required=True,
        help="Path to the flowpaths GeoPackage (e.g., sc_reference_fabric.gpkg).",
    )
    parser.add_argument(
        "--flowpaths-layer",
        default="reference_flowpaths",
        help="Layer name in the flowpaths GeoPackage (default: reference_flowpaths).",
    )
    parser.add_argument(
        "--gages",
        required=True,
        help="Path to the gages GeoPackage (e.g., usgs_gages_all_conus_AK_Pr.gpkg).",
    )
    parser.add_argument(
        "--gages-layer",
        default="usgs_gages",
        help="Layer name in the gages GeoPackage (default: usgs_gages).",
    )
    parser.add_argument(
        "--buffer-m",
        type=float,
        default=500.0,
        help="Search buffer radius in meters around each gage (default: 500).",
    )
    parser.add_argument(
        "--parallel",
        action="store_true",
        help="Enable parallel processing (ProcessPoolExecutor).",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help="Max worker processes when --parallel is set (default: all CPUs).",
    )
    parser.add_argument(
        "--output",
        help=(
            "Output GeoPackage path. "
            "Default: <directory of --gages>/usgs_gages_all_conus_AK_Pr_flowpath_id.gpkg"
        ),
    )

    args = parser.parse_args()

    flowpaths_path = Path(args.flowpaths)
    gages_path = Path(args.gages)

    # Default output next to the gages file if not provided
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = gages_path.parent / "usgs_gages_all_conus_AK_Pr_flowpath_id.gpkg"

    # Load gages (filter out rows without geometry)
    gages = gpd.read_file(gages_path, layer=args.gages_layer)
    gages = gages[gages.geometry.notna()].copy()

    # Run assignment (serial or parallel based on flag)
    assigned = run_assignment(
        gages=gages,
        flowpaths_path=flowpaths_path,
        flowpaths_layer=args.flowpaths_layer,
        buffer_m=float(args.buffer_m),
        parallel=bool(args.parallel),  # decides to run in serial or parallel
        max_workers=(args.max_workers if args.parallel else None),
    )

    # Save result
    assigned.to_file(output_path, layer=args.gages_layer, driver="GPKG")
    print(f"[ok] wrote {output_path}")

    """Running with no argeparse. Good for debugging"""
    # flowpaths_path = Path(r"/home/farshid.rahmani/Documents/Dataset/HF/HF_beta_v2.3/sc_reference_fabric.gpkg")
    # gages_path = Path(r"/home/farshid.rahmani/Documents/Dataset/HF/usgs_gages_all_conus_AK_Pr.gpkg")
    #
    # gages = gpd.read_file(gages_path, layer="usgs_gages")
    # gages = gages[gages.geometry.notna()].copy()
    #
    # # Serial
    # assigned = run_assignment(
    #     gages=gages,
    #     flowpaths_path=flowpaths_path,
    #     flowpaths_layer="reference_flowpaths",
    #     buffer_m=500.0,
    #     parallel=False,  ### serial or parallel
    #     max_workers=None,  ### None: if serial
    # )

    # assigned.to_file(
    #     gages_path.parent / "usgs_gages_all_conus_AK_Pr_flowpath_id.gpkg",
    #     layer="usgs_gages",
    #     driver="GPKG",
    # )
    print("done.")
