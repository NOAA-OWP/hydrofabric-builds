from __future__ import annotations

import argparse
import os
from pathlib import Path

import yaml

from hydrofabric_builds.reservoirs.data_prep.rfc_da import build_rfc_da_hydraulics


def _resolve_templates(d: dict, env: dict) -> dict:
    """Expand {placeholders} in string leaves using env mapping."""

    def expand(val: dict) -> dict:
        """Expanding the vals in config"""
        if isinstance(val, str):
            try:
                return val.format(**env)
            except KeyError:
                return val
        if isinstance(val, dict):
            return {k: expand(v) for k, v in val.items()}
        if isinstance(val, list):
            return [expand(v) for v in val]
        return val

    return expand(d)


def load_config(path: Path) -> dict:
    """
    To load a YAML config

    :param path: path to config file
    :return: nested dictionary format of config file
    """
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    env = {**cfg.get("roots", {})}
    env.update(os.environ)  # allow env-var overrides
    return _resolve_templates(cfg, env)


def main(cfg_path: Path) -> None:
    """The main file entering reservoir attributes calculation"""
    cfg = load_config(cfg_path)
    dem_path = Path(cfg["dem"]["path"])
    ref_reservoirs_path = Path(cfg["inputs"]["reference_reservoirs"]["path"])
    ref_wb_path = Path(cfg["inputs"]["reference_waterbodies"]["path"])
    osm_ref_wb_path = Path(cfg["inputs"]["osm_build"]["path"])
    nid_path_clean = Path(cfg["inputs"]["nid"]["path"])
    out_dir = Path(cfg["roots"]["output_dir"])
    hydr = build_rfc_da_hydraulics(
        dem_path=dem_path,
        ref_reservoirs_path=ref_reservoirs_path,
        ref_wb_path=ref_wb_path,
        osm_ref_wb_path=osm_ref_wb_path,
        nid_clean_path=nid_path_clean,  # or .parquet
        max_waterbody_nearest_dist_m=cfg["matching"]["max_waterbody_nearest_dist_m"],
        min_area_sqkm=cfg["matching"]["min_area_sqkm"],
        out_dir=out_dir,
        work_crs=cfg["crs"]["work_crs"],
        default_crs=cfg["crs"]["default_src_crs"],
        use_hazard=True,
    )
    print(f"[OK] attributes have been estimated for {len(hydr)} reservoirs")


if __name__ == "__main__":
    """
    How to run:
    Type the following in terminal:
    python3 reservoir_data_prep.py --config /path/to/example_reservoir_attr_config.YAML

    ## the source files and reference files can be found in S3 data account. The paths should be in config file
    ## Please refer to /docs/builds.reservoir_attrs.md for more information and the files paths.
    """

    parser = argparse.ArgumentParser(
        description="Build RFC-DA reservoir hydraulics from DEM, NID, OSM, reference reservoirs."
    )
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        required=True,
        help="Path to YAML config file (e.g., example_reservoir_attrs_config.yaml).",
    )

    args = parser.parse_args()
    main(args.config)
    print("end")
