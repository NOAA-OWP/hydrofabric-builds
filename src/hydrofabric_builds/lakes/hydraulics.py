from __future__ import annotations

from collections.abc import Iterable

import geopandas as gpd
import numpy as np
import pandas as pd


def _zero_to_nan(s: pd.Series | None) -> pd.Series | None:
    """
    Changes zero values in a series tto NaN

    :param s:pandas series
    :return: pandas series
    """
    if s is None:
        return None
    s = pd.to_numeric(s, errors="coerce")
    return s.where(s != 0, np.nan)


def _tolower_chr(s: pd.Series) -> pd.Series:
    """
    R-style tolower_chr: as.character, NA -> '', tolower.

    :param s: pandas series
    :return: modified pandas series
    """
    return s.astype("string").fillna("").str.lower()


def _num(x: pd.Series | Iterable | None) -> pd.Series:
    """R-style num(): numeric with coercion to NaN."""
    if x is None:
        return pd.Series(dtype="float32")
    return pd.to_numeric(x, errors="coerce")


def _coalesce_num(*cols: Iterable | pd.Series) -> np.ndarray:
    """
    R-style coalesce_num: row-wise first non-NA across inputs.

    Returns a 1D numpy array of float.
    """
    if not cols:
        return np.array([], dtype="float32")

    # Normalize to numeric Series with a shared index/length.
    ser_list = []
    max_len = 0
    for c in cols:
        s = _num(pd.Series(c))
        ser_list.append(s)
        max_len = max(max_len, len(s))

    # Align lengths (pad with NaN if needed)
    aligned = []
    for s in ser_list:
        if len(s) < max_len:
            s = pd.concat([s, pd.Series([np.nan] * (max_len - len(s)))], ignore_index=True)
        aligned.append(s.to_numpy(dtype="float32"))

    out = np.full(max_len, np.nan, dtype="float32")
    for arr in aligned:
        mask = np.isnan(out) & ~np.isnan(arr)
        out[mask] = arr[mask]
    return out


def _populate_hydraulics(
    df: pd.DataFrame | gpd.GeoDataFrame,
    # parameterized fallbacks
    default_WeirC: float = 0.4,
    default_WeirL: float = 10.0,  # m
    default_OrificeC: float = 0.1,
    default_OrificeA: float = 1.0,  # m²
    default_ifd: float = 0.899,
    # height-based fractions
    crest_frac: float = 0.90,  # crest ~ base + 0.90*H
    invert_frac: float = 0.15,  # invert ~ base + 0.15*H
    max_frac: float = 1.00,  # max pool ~ base + 1.00*H
    # relative to waterbody elevation
    max_from_wb_frac: float = 0.10,  # LkMxE ~ wb + 0.10*H
    # Orifice area heuristics
    OrificeA_small: float = 0.5,
    OrificeA_med: float = 0.9,
    OrificeA_large: float = 1.5,
    OrificeA_concrete: float = 1.2,
    use_hazard: bool = False,
) -> pd.DataFrame:
    """
    Python port of R `populate_hydraulics()`.

    Expects a DataFrame with (some subset of) the following columns:
      dam_id, nidid, ref_fab_wb,
      dam_type, spillway_type, purposes, hazard,
      structural_height, dam_height, hydraulic_height, nid_height,
      ref_area_sqkm, osm_area_sqkm, surface_area,
      nid_storage, normal_storage, max_storage,
      ref_elev, osm_wb_elev, dam_elev, spillway_width, dam_length.

    Returns a new DataFrame with:
      dam_id, nidid, ref_wb_id,
      H_m, LkArea, LkMxE, WeirC, WeirL, WeirE,
      OrificeC, OrificeA, OrificeE, Dam_Length, ifd.

    :param df: Reference reservoirs dataframe. It should contain columns like dam_id, nidid, dam_type,
        spillway_type, heights, areas, storage, DEM-based elevations, etc. This is the input you’re enriching
        with hydraulic surrogates.
    :param default_WeirC: Fallback weir coefficient (dimensionless) used when it can’t be inferred from
        the spillway type text. This is the coefficient in the standard broad-crested/sharp-crested weir equation
    :param default_WeirL: Fallback weir length in meters. Used when neither spillway_width nor dam_length
        are available or valid. Also becomes the default Dam_Length.
    :param default_OrificeC: Fallback Orifice coefficient (dimensionless) used when the spillway text and
        purposes don’t give enough information to assign a more specific value.
    :param default_OrificeA: Fallback Orifice area in square meters used when no height-based or material-based
        heuristic can assign OrificeA.
    :param default_ifd: A constant scalar returned as the ifd column for each dam. In the v1 workflow this is set
        to 0.899 to match NWM defaults.
    :param crest_frac: Fraction of total dam height H used to place the weir crest elevation above the dam base
        (dam_elev) when DEM water-surface info is missing. Approx: WeirE ≈ base + crest_frac * H (default 0.90).
    :param invert_frac: Fraction of H above the dam base used to estimate the Orifice invert elevation.
        Approx: OrificeE ≈ base + invert_frac * H (default 0.15).
    :param max_frac: Fraction of H above the dam base used to estimate maximum pool elevation when no better info is available.
        Approx: LkMxE ≈ base + max_frac * H (default 1.00).
    :param max_from_wb_frac: Fraction of H added on top of the waterbody elevation (wb = ref_elev or osm_wb_elev) to estimate maximum pool:
        LkMxE ≈ wb + max_from_wb_frac * H (default 0.10).
    :param OrificeA_small: Heuristic Orifice area (m²) assigned when dam height H < 10 m.
    :param OrificeA_med: Heuristic Orifice area (m²) for medium dams (10 ≤ H < 30 m).
    :param OrificeA_large: Heuristic Orifice area (m²) for tall dams (H ≥ 30 m).
    :param OrificeA_concrete: Override Orifice area (m²) used when the dam looks concrete/ogee/gravity/arch. Applied if
        OrificeA is still missing after height-based rules.
    :param use_hazard: Boolean flag. If True, the function uses the hazard rating (e.g., high / significant) to
        slightly increase weir length, Orifice area, and sometimes Orifice coefficient for higher-hazard dams (more conservative hydraulics). If False, no hazard-based adjustment is applied.
    :return: a Dataframe
    """
    n = len(df)
    df = df.reset_index(drop=True)

    # These columns likely already exist from importing previous data, but if they do not exist, create them and fill with nan
    param_columns = [
        "LkArea",
        "LkMxE",
        "WeirC",
        "WeirL",
        "WeirE",
        "OrificeC",
        "OrificeA",
        "OrificeE",
        "Dam_Length",
        "ifd",
    ]
    for c in param_columns:
        if c not in df.columns:
            df[c] = np.nan

    # ---- pull allowed inputs (lowercased strings) ----
    spill = _tolower_chr(df.get("spillway_type", pd.Series(index=df.index, dtype="string")))
    dtype = _tolower_chr(df.get("dam_type", pd.Series(index=df.index, dtype="string")))
    purp = _tolower_chr(df.get("purposes", pd.Series(index=df.index, dtype="string")))

    # ---- Heights (m) ----
    H = _coalesce_num(
        _zero_to_nan(df.get("structural_height")),
        _zero_to_nan(df.get("dam_height")),
        _zero_to_nan(df.get("hydraulic_height")),
        _zero_to_nan(df.get("nid_height")),
    )

    # ---- DEM anchors (m) ----
    wb = _coalesce_num(  # normal pool proxy
        df.get("ref_elev"),
    )
    base = _num(df.get("dam_elev", pd.Series(index=df.index, dtype="float32"))).to_numpy()

    # ---- Area (km²): ref_area_sqkm >  surface_area ----
    LkArea = _coalesce_num(
        _num(df.get("LkArea")),
        _num(df.get("wb_areasqkm")),
        _num(df.get("ref_area_sqkm")),
        _num(df.get("surface_area")) / 1e6,
    )
    LkArea = np.where(LkArea == 0, np.nan, LkArea)

    # ---- Storage → mean depth (m) ----
    # 1233.48184    : acre-feet to m3 conversion factor
    storage_m3 = _coalesce_num(
        _num(df.get("nid_storage")) * 1233.48184,
        _num(df.get("normal_storage")) * 1233.48184,
        _num(df.get("max_storage")) * 1233.48184,
    )
    storage_m3 = storage_m3.astype("float32")
    mean_depth = np.where(
        (~np.isnan(storage_m3)) & (~np.isnan(LkArea)) & (LkArea > 0),
        storage_m3 / LkArea,
        np.nan,
    )

    # ---- Weir length (m): spillway_width > dam_length > default ----
    WeirL = _coalesce_num(
        _zero_to_nan(df.get("WeirL")),
        _zero_to_nan(df.get("spillway_width")),
        _zero_to_nan(df.get("dam_length")),
    )
    if WeirL.size == 0:
        WeirL = np.full(n, np.nan, dtype="float32")
    else:
        # ensure same length as df
        if WeirL.size < n:
            WeirL = np.pad(WeirL, (0, n - WeirL.size), constant_values=np.nan)

    WeirL = WeirL.astype("float32")
    WeirL[np.isnan(WeirL)] = default_WeirL
    WeirL[WeirL == 0] = default_WeirL
    # Dam_Length = WeirL.copy()   ## the original R code uses this logic but I changed it.

    # Dam_Length: prefer original dam_length; else use WeirL
    Dam_Length = _coalesce_num(_num(df.get("Dam_Length")), _num(df.get("dam_length")))
    mask = np.isnan(Dam_Length) | (Dam_Length == 0)
    Dam_Length[mask] = WeirL[mask]

    # ---- Weir coefficient WeirC ----
    spill_str = spill.fillna("")
    dtype_str = dtype.fillna("")

    is_sharp = spill_str.str.contains("sharp", na=False)
    is_broad = spill_str.str.contains("broad", na=False)
    is_ogee = spill_str.str.contains("ogee", na=False)
    is_earth = dtype_str.str.contains("earth", na=False) | dtype_str.str.contains("earthen", na=False)

    # based on spillway type, the following numbers are assumed
    """
    Those numbers are the weir discharge coefficients pulled straight from standard open-channel hydraulics
    practice (the same ones cited in the R comments: Chow, 1959):

    1.84 → sharp-crested weir coefficient
    1.7 → ogee (overflow) spillway coefficient
    1.6 → broad-crested or earthen / overflow-type coefficient
    They’re dimensionless constants in the weir equation:
    𝑄 = 𝐶⋅𝐿⋅𝐻^3/2

    where:
    C is that coefficient (WeirC),
    L is weir length (WeirL),
    H is head over crest.

    In this code code, we’re mapping text cues in spillway_type / dam_type to typical literature values:
    is_broad → 1.6
    is_ogee → 1.7
    is_sharp → 1.84
    is_earth (earthen dam) → 1.6 (if not already classified as broad/ogee/sharp)

    Anything that doesn’t match those patterns falls back to default_WeirC (0.4 in the original workflow,
    matching current NWM default).
    """
    WeirC = df["WeirC"].to_numpy(dtype="float32")
    WeirC[is_broad.to_numpy() & np.isnan(WeirC)] = 1.6
    WeirC[is_ogee.to_numpy() & np.isnan(WeirC)] = 1.7
    WeirC[is_sharp.to_numpy() & np.isnan(WeirC)] = 1.84
    WeirC[(is_earth & ~is_broad & ~is_ogee & ~is_sharp & np.isnan(WeirC)).to_numpy()] = 1.6
    WeirC[np.isnan(WeirC)] = default_WeirC

    # ---- Orifice coefficient OrificeC ----
    looks_Orifice = (
        spill_str.str.contains("Orifice", na=False)
        | spill_str.str.contains("orfice", na=False)
        | spill_str.str.contains("sluice", na=False)
        | spill_str.str.contains("pipe", na=False)
        | spill_str.str.contains("outlet", na=False)
    )
    looks_rounded = (
        spill_str.str.contains("gate", na=False)
        | spill_str.str.contains("gated", na=False)
        | spill_str.str.contains("radial", na=False)
        | spill_str.str.contains("tunnel", na=False)
        | spill_str.str.contains("culvert", na=False)
        | spill_str.str.contains("conduit", na=False)
        | spill_str.str.contains("valve", na=False)
    )
    is_hydro = purp.fillna("").str.contains("hydro|power", regex=True, na=False)

    """
    These are the Orifice discharge coefficients we’d plug into the classic Orifice equation:

    𝑄 = C_d * A * (2 * g * H) ^ 2
    where C_D is what we’re calling OrificeC.

    In words:
    0.62 → typical coefficient for a sharp-edged / sluice / pipe outlet
    Those terms (Orifice, sluice, pipe, outlet) usually imply a sharp-edged Orifice or simple outlet works.
    Textbook Cd for sharp-edged Orifices is usually ~0.6–0.62.

    0.80 → typical coefficient for rounded / gated / tunnel / culvert / conduit-type inlets
    Terms like gate, gated, radial, tunnel, culvert, conduit, valve generally mean smoother, better-formed
    entrances with less contraction loss.

    C_d values in the ~0.8 range are standard for well-rounded or gated Orifices.
    We also set 0.80 when purposes contains "hydro" or "power" (is_hydro), on the assumption that
    hydropower outlets are typically gated/engineered, so their effective Cd is closer to
    the “rounded/gated” value than the conservative default.

    Anything that doesn’t match those cues falls back to default_OrificeC
    (0.1 in the original R code, matching the conservative NWM default).
    All of these numbers are heuristic but grounded in common ranges from standard hydraulics
    references (e.g., Chow’s Open-Channel Hydraulics, USACE manuals, etc.).
    """
    OrificeC = df["OrificeC"].to_numpy(dtype="float32")
    OrificeC[looks_Orifice.to_numpy() & np.isnan(OrificeC)] = 0.62
    mask = np.isnan(OrificeC) & looks_rounded.to_numpy()
    OrificeC[mask] = 0.80
    mask = np.isnan(OrificeC) & is_hydro.to_numpy()
    OrificeC[mask] = 0.80
    OrificeC[np.isnan(OrificeC)] = default_OrificeC

    # ---- Orifice area OrificeA (m²) ----
    OrificeA = df["OrificeA"].to_numpy(dtype="float32")
    H_valid = ~np.isnan(H)
    OrificeA[H_valid & (H < 10) & np.isnan(OrificeA)] = OrificeA_small
    OrificeA[H_valid & (H >= 10) & (H < 30) & np.isnan(OrificeA)] = OrificeA_med
    OrificeA[H_valid & (H >= 30) & np.isnan(OrificeA)] = OrificeA_large

    # ---- Optional hazard-based nudges ----
    if use_hazard and "hazard" in df.columns:
        haz = _tolower_chr(df["hazard"])
        is_high = haz.str.startswith("h", na=False)
        is_sig = haz.str.startswith("s", na=False)

        # modest adjustments to WeirL and OrificeA
        WeirL = np.where(is_high.to_numpy(), WeirL * 1.10, WeirL)
        WeirL = np.where(is_sig.to_numpy(), WeirL * 1.05, WeirL)

        OrificeA = np.where(is_high.to_numpy(), OrificeA * 1.20, OrificeA)
        OrificeA = np.where(is_sig.to_numpy(), OrificeA * 1.10, OrificeA)

        # if OrificeC fell back to default, bump slightly for higher hazard
        spill_raw = df.get("spillway_type", pd.Series(index=df.index, dtype="string"))
        used_default_OrificeC = spill_raw.isna() | (spill_raw.astype("string") == "")
        OrificeC = np.where(
            is_high.to_numpy() & used_default_OrificeC.to_numpy(), np.maximum(OrificeC, 0.80), OrificeC
        )
        OrificeC = np.where(
            is_sig.to_numpy() & used_default_OrificeC.to_numpy(), np.maximum(OrificeC, 0.70), OrificeC
        )

    # ---- Concrete / ogee cue for OrificeA ----
    looks_concrete = (
        dtype_str.str.contains("concrete", na=False)
        | dtype_str.str.contains("gravity", na=False)
        | dtype_str.str.contains("arch", na=False)
        | spill_str.str.contains("ogee", na=False)
    )
    mask = np.isnan(OrificeA) & looks_concrete.to_numpy()
    OrificeA[mask] = OrificeA_concrete
    OrificeA[np.isnan(OrificeA)] = default_OrificeA

    # ---- absolute elevations (m) using DEM anchors ----

    # Crest (WeirE)
    WeirE = _coalesce_num(
        df["WeirE"].to_numpy(),
        wb,
        np.where((~np.isnan(base)) & (~np.isnan(H)), base + crest_frac * H, np.nan),
        np.where((~np.isnan(base)) & (~np.isnan(mean_depth)), base + mean_depth, np.nan),
    )

    # Max pool (LkMxE)
    LkMxE = _coalesce_num(
        df["LkMxE"].to_numpy(),
        np.where((~np.isnan(wb)) & (~np.isnan(H)), wb + max_from_wb_frac * H, np.nan),
        wb,
        np.where((~np.isnan(base)) & (~np.isnan(H)), base + max_frac * H, np.nan),
        np.where((~np.isnan(base)) & (~np.isnan(mean_depth)), base + mean_depth, np.nan),
    )

    # Orifice invert (OrificeE)
    OrificeE = _coalesce_num(
        df["OrificeE"].to_numpy(),
        np.where((~np.isnan(base)) & (~np.isnan(H)), base + invert_frac * H, np.nan),
        np.where((~np.isnan(wb)) & (~np.isnan(H)), wb - (crest_frac - invert_frac) * H, np.nan),
    )

    # ---- constant ifd ----
    ifd = df["ifd"].to_numpy(dtype=np.float32)
    ifd = np.where(np.isnan(ifd), default_ifd, ifd)

    out = pd.DataFrame(
        {
            "H_m": H,
            "LkArea": LkArea,
            "LkMxE": LkMxE,
            "WeirC": WeirC,
            "WeirL": WeirL,
            "WeirE": WeirE,
            "OrificeC": OrificeC,
            "OrificeA": OrificeA,
            "OrificeE": OrificeE,
            "Dam_Length": Dam_Length,
            "ifd": ifd,
        },
        index=df.index,
    )

    # correct any issues caused by mixing of data
    # t-route will error if WeirE < OrificeE or LkMxE < WeirE. == is acceptable
    # OrificeE can be null if there is no dam information - set it to WeirE
    out["WeirE"] = np.where(out["WeirE"] < out["OrificeE"], out["OrificeE"], out["WeirE"])
    out["LkMxE"] = np.where(out["LkMxE"] < out["WeirE"], out["WeirE"], out["LkMxE"])
    out["OrificeE"] = np.where(out["OrificeE"].isnull(), out["WeirE"], out["OrificeE"])

    df = df.drop(columns=param_columns)
    out = df.merge(out, left_index=True, right_index=True, how="left")

    return out
