import argparse
import pickle
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import yaml


def build_ref_streamflow_hourly_multi(
    mapping_divides_path: str | Path,
    flow_dir: str | Path,
    out_dir: str | Path,
    merit_start_date: str,  # e.g. "2000-01-01"
    merit_end_date: str,  # e.g. "2000-12-31"
    ref_start_date: str,  # e.g. "2000-01-01 00:00"
    ref_end_date: str,  # e.g. "2000-01-10 23:00"
    flow_pattern: str = "Runoff_sim_*.npy",  # pattern for MERIT flow files
    comid_prefix: str = "comids_",  # how to find COMID files from flow stems
    normalize_weights: bool = True,
    divide_id_name: str = "divide_id",
    ref_fabric_gpkg: str | Path = "",
    ref_layer: str = "reference_divides",
) -> None:
    """
    Build hourly reference-basin streamflow CSVs from multiple MERIT daily flow chunks.

    - mapping_divides_path: {ref_id: {merit_comid: overlap_fraction}}
    - flow_dir:      dir with multiple MERIT daily flow npy files and their COMID_*.npy
    - out_dir:       where hourly CSVs (CHRTOUT_DOMAIN1) go
    - merit_*_date:  daily MERIT coverage (inclusive)
    - ref_*_date:    hourly reference time window (inclusive)
    """
    mapping_divides_path = Path(mapping_divides_path)
    flow_dir = Path(flow_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---------- load nested dict ----------
    with mapping_divides_path.open("rb") as f:
        nested: dict[str, dict[str, float]] = pickle.load(f)

    # --- get full list of reference basins from the GPKG ---
    if not ref_fabric_gpkg:
        raise ValueError("ref_fabric_gpkg must be provided to get all reference IDs")

    ref_gdf = gpd.read_file(ref_fabric_gpkg, layer=ref_layer)
    # ensure IDs are strings for consistency
    all_ref_ids = sorted(ref_gdf[divide_id_name].unique())

    # ---------- discover & load all MERIT flow + COMID files ----------
    flow_files = sorted(flow_dir.glob(flow_pattern))
    if not flow_files:
        raise FileNotFoundError(f"No flow files matching {flow_pattern} in {flow_dir}")

    flows_list: list[np.ndarray] = []
    comids_list: list[np.ndarray] = []
    comids_str_list: list[np.ndarray] = []

    for flow_path in flow_files:
        flow_path = Path(flow_path)
        stem = flow_path.stem  # e.g. "Runoff_sim_01"
        # this assumes the last 2 chars of stem identify the chunk, adjust if needed
        comid_path = flow_path.with_name(comid_prefix + stem[-2:] + ".npy")

        if not comid_path.exists():
            raise FileNotFoundError(f"COMID file not found for {flow_path.name}: {comid_path}")

        flow = np.load(flow_path)  # [n_merit_chunk, n_days]
        comids = np.load(comid_path)  # [n_merit_chunk]
        comids_str = comids.astype(str)

        if flow.shape[0] != comids_str.shape[0]:
            raise ValueError(
                f"Row mismatch between {flow_path.name} ({flow.shape[0]}) "
                f"and {comid_path.name} ({comids_str.shape[0]})"
            )

        flows_list.append(flow)
        comids_list.append(comids)
        comids_str_list.append(comids_str)

    # sanity: all chunks must have same number of days
    n_days_set = {f.shape[1] for f in flows_list}
    if len(n_days_set) != 1:
        raise ValueError(f"MERIT flow chunks have differing n_days: {n_days_set}")
    n_days = n_days_set.pop()

    # ---------- time axes ----------
    merit_days = pd.date_range(merit_start_date, merit_end_date, freq="D")
    if len(merit_days) != n_days:
        raise ValueError(f"MERIT date range has {len(merit_days)} days but flow has {n_days} columns")

    merit_date_to_idx = {d.date(): i for i, d in enumerate(merit_days)}
    ref_hours = pd.date_range(ref_start_date, ref_end_date, freq="h")

    # ---------- global COMID index: COMID -> (file_idx, row_idx) ----------
    comid_global_map: dict[str, tuple[int, int]] = {}
    for file_idx, comids_str in enumerate(comids_str_list):
        for row_idx, c in enumerate(comids_str):
            # assume each COMID appears in only one chunk; if not, last one wins
            comid_global_map[c] = (file_idx, row_idx)

    # ---------- precompute indices/weights per ref basin ----------
    # For each ref_id: dict[file_idx] -> (idx_array, weight_array)
    RefFileWeights = dict[int, tuple[np.ndarray, np.ndarray]]
    RefWeightsByFile = dict[str, RefFileWeights | None]

    ref_weights_by_file: RefWeightsByFile = {}

    for ref_id in all_ref_ids:
        overlaps = nested.get(ref_id, {})  # {} if not present in nested
        if not overlaps:
            ref_weights_by_file[ref_id] = None
            continue

        # (file_idx, row_idx, raw_weight)
        triples: list[tuple[int, int, float]] = []
        for merit_comid, w in overlaps.items():
            loc = comid_global_map.get(str(merit_comid))
            if loc is None:
                continue
            file_idx, row_idx = loc
            w_float = float(w)
            if w_float <= 0:
                continue
            triples.append((file_idx, row_idx, w_float))

        if not triples:
            ref_weights_by_file[ref_id] = None
            continue

        # normalize weights across all triples so sum = 1 (if desired)
        weights_arr = np.array([t[2] for t in triples], dtype=float)
        sum_w = float(weights_arr.sum())
        if normalize_weights and sum_w > 0:
            weights_arr = weights_arr / sum_w

        # regroup by file_idx using temporary Python lists
        tmp: dict[int, tuple[list[int], list[float]]] = {}
        for (file_idx, row_idx, _), w_norm in zip(triples, weights_arr, strict=False):
            if file_idx not in tmp:
                tmp[file_idx] = ([], [])
            idx_list, w_list = tmp[file_idx]
            idx_list.append(row_idx)
            w_list.append(float(w_norm))

        # convert lists to numpy arrays
        out_map: RefFileWeights = {}
        for file_idx, (idx_list, w_list) in tmp.items():
            idxs_arr = np.array(idx_list, dtype=int)
            w_arr = np.array(w_list, dtype=float)
            out_map[file_idx] = (idxs_arr, w_arr)

        ref_weights_by_file[ref_id] = out_map

    # ---------- group ref_hours by day ----------
    hours_by_date: dict[pd.Timestamp, list[pd.Timestamp]] = {}
    for t in ref_hours:
        d = t.normalize()
        hours_by_date.setdefault(d, []).append(t)

    # ---------- main loop: per day, then per hour ----------
    for day_ts, hour_list in sorted(hours_by_date.items()):
        day_date = day_ts.date()
        if day_date in merit_date_to_idx:
            day_idx = merit_date_to_idx[day_date]
            # daily MERIT flows -> hourly by /24, done per file
            daily_q_list = [flow[:, day_idx] / 24.0 for flow in flows_list]
        else:
            # no MERIT data: all zeros
            daily_q_list = [np.zeros(flow.shape[0], dtype=float) for flow in flows_list]

        # flows for all ref basins for this day
        flows_per_ref = np.zeros(len(all_ref_ids), dtype=float)

        for k, ref_id in enumerate(all_ref_ids):
            ref_map = ref_weights_by_file.get(ref_id)
            if not ref_map:
                flows_per_ref[k] = 0.0
                continue

            total_flow = 0.0
            for file_idx, (idxs, weights) in ref_map.items():
                q_chunk = daily_q_list[file_idx][idxs]
                total_flow += float((q_chunk * weights).sum())

            flows_per_ref[k] = total_flow

        # write one CSV per hour in this day
        for t in hour_list:
            ts_str = t.strftime("%Y%m%d%H%M")
            out_csv = out_dir / f"{ts_str}.CHRTOUT_DOMAIN1.csv"

            df = pd.DataFrame(
                {
                    divide_id_name: all_ref_ids,
                    "streamflow": flows_per_ref,
                }
            )
            df.to_csv(out_csv, index=False)

    print(f"Hourly streamflow has been made for {len(all_ref_ids)} divides")


def load_config(cfg_path: str | Path) -> dict:
    """
    Loads config yaml file

    :param cfg_path: path to config file
    :return: return dictionary of config
    """
    cfg_path = Path(cfg_path)
    with cfg_path.open("r") as f:
        return yaml.safe_load(f)


def main() -> None:
    """
    Main entrance to make streamflow data for reference fabric divides into NGEN format

    using MERIT streamflow from Song et al 2025
    :return: None. It saves the csv files into local directory defined in config file
    """
    parser = argparse.ArgumentParser(
        description="Build hourly reference-basin streamflow from MERIT daily flows."
    )
    parser.add_argument(
        "-c",
        "--config",
        required=True,
        help="Path to YAML config file. EX: configs/example_match_Q_merit_to_ref_config.yaml",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)

    paths = cfg["paths"]
    dates = cfg["dates"]
    flow_cfg = cfg.get("flow", {})
    ref_cfg = cfg.get("reference", {})
    w_cfg = cfg.get("weights", {})

    build_ref_streamflow_hourly_multi(
        mapping_divides_path=paths["mapping_divides_path"],
        flow_dir=paths["flow_dir"],
        out_dir=paths["out_dir"],
        merit_start_date=dates["merit_start_date"],
        merit_end_date=dates["merit_end_date"],
        ref_start_date=dates["ref_start_date"],
        ref_end_date=dates["ref_end_date"],
        flow_pattern=flow_cfg.get("flow_pattern", "Runoff_sim_*.npy"),
        comid_prefix=flow_cfg.get("comid_prefix", "comids_"),
        normalize_weights=w_cfg.get("normalize_weights", True),
        divide_id_name=ref_cfg.get("divide_id_name", "divide_id"),
        ref_fabric_gpkg=paths["ref_fabric_gpkg"],
        ref_layer=ref_cfg.get("ref_layer", "reference_divides"),
    )


if __name__ == "__main__":
    main()
