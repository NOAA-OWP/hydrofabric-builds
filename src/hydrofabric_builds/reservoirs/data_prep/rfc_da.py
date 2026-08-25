from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from hydrofabric_builds.reservoirs.data_prep.DEM_helper import extract_elev_at_points
from hydrofabric_builds.reservoirs.data_prep.hydraulics import populate_hydraulics
from hydrofabric_builds.reservoirs.data_prep.nwm_lakes import find_lake_duplicates
from hydrofabric_builds.reservoirs.data_prep.osm_dams import build_osm_wb_elevs
from hydrofabric_builds.reservoirs.data_prep.ref_wb_link import build_ref_wb_elevs

logger = logging.getLogger(__name__)


def build_rfc_da_locs(
    dem_path: str | Path,
    ref_reservoirs_path: str | Path,
    nid_clean_path: str | Path,
    ref_wb_elevs_path: str | Path,
    osm_wb_elevs_path: str | Path,
    out_gpkg: str | Path,
    work_crs: str,
    default_crs: str,
    max_waterbody_nearest_dist_m: float,
    min_area_sqkm: float,
    res_keep: list[str],
) -> gpd.GeoDataFrame:
    """
    Python equivalent of the middle part:

      - read cleaned NID, prep attributes,
      - restrict to candidate dams,
      - join NID attrs + WB elevations,
      - sample DEM at dam points -> dam_elev,
      - write rfc-da-locs.gpkg.

    :param dem_path: path to dem file
    :param ref_reservoirs_path: path to reference_reservoirs
    :param nid_clean_path: path to nid dams file
    :param ref_wb_elevs_path: reference waterbodies path
    :param osm_wb_elevs_path: path to osm_dams
    :param out_gpkg: output path
    :param max_waterbody_nearest_dist_m: maximum distance between points and waterbodies
    :param min_area_sqkm: minimum waterbody area to be considered
    :param res_keep: list of reference reservoir `dam_id`s to keep
    :return: geodataframe file
    """
    # res + candidates (same as before)
    res = gpd.read_file(ref_reservoirs_path)
    da = res[
        ((res["distance_to_fp_m"] < max_waterbody_nearest_dist_m) & (res["wb_areasqkm"] >= min_area_sqkm))
        | (res["lk"] == True)  # noqa: E712
        | (res["dam_id"].isin(res_keep))
    ].copy()

    # read cleaned NID (GPKG or Parquet or CSV)
    nid_path = Path(nid_clean_path)
    if nid_path.suffix.lower() == ".gpkg":
        nid_gdf = gpd.read_file(nid_path)
        nid_df = pd.DataFrame(nid_gdf.drop(columns=nid_gdf.geometry.name, errors="ignore"))
    elif nid_path.suffix.lower() in {".parquet", ".pq"}:
        nid_df = pd.read_parquet(nid_path)
    else:
        nid_df = pd.read_csv(nid_path)

    cols_lower = [col.lower() for col in nid_df.columns]
    nid_df.columns = cols_lower
    # cast types like R
    for col in ("spillway_type", "dam_type"):
        if col in nid_df.columns:
            nid_df[col] = nid_df[col].astype("string")

    for col in ("structural_height", "dam_height", "nid_height", "surface_area", "hydraulic_height"):
        if col in nid_df.columns:
            nid_df[col] = pd.to_numeric(nid_df[col], errors="coerce")

    if "surface_area" not in nid_df.columns:
        nid_df["surface_area"] = np.nan

    # keep only needed columns (loosely matching R)
    keep_cols = [
        "nidid",
        "dam_name",
        "dam_type",
        "spillway_type",
        "spillway_width",
        "dam_length",
        "dam_height",
        "structural_height",
        "hydraulic_height",
        "nid_height",
        "surface_area",
        "wb_areasqkm",
        "nid_storage",
        "normal_storage",
        "max_storage",
        "hazard",
        "purposes",
    ]

    keep_cols = [c for c in keep_cols if c in nid_df.columns]
    nid_df = nid_df[keep_cols].copy()

    # restrict NID to NID IDs in da$nid
    if "nid" not in da.columns:
        raise ValueError("Expected 'nid' column in reference reservoirs (da).")
    nid_ids = da["nid"].dropna().unique()
    nid_df = nid_df[nid_df["nidid"].isin(nid_ids)].copy()

    # build tmp = da + NID attrs + WB elevs
    tmp = da[["dam_id", "nid", "osm_ww_poly", "ref_fab_wb", "x", "y"]].rename(columns={"nid": "nidid"}).copy()

    tmp = tmp.merge(nid_df, on="nidid", how="left")

    osm_wb_elevs = gpd.read_file(osm_wb_elevs_path).drop(columns="geometry")
    ref_wb_elevs = gpd.read_file(ref_wb_elevs_path).drop(columns="geometry")

    tmp = tmp.merge(osm_wb_elevs, left_on="osm_ww_poly", right_on="osm_id", how="left")
    tmp = tmp.merge(ref_wb_elevs, left_on="ref_fab_wb", right_on="comid", how="left")

    # GeoDataFrame in 5070, then to 4326 (like xx_sf in R)
    gdf = gpd.GeoDataFrame(
        tmp,
        geometry=gpd.points_from_xy(tmp["x"], tmp["y"]),
        crs=work_crs,
    ).to_crs(default_crs)

    # Sample DEM at dam points for dam_elev
    logger.info("Sampling DEM for dam elevation")
    dam_elev = extract_elev_at_points(dem_path, gdf)
    gdf["dam_elev"] = dam_elev

    out_path = Path(out_gpkg)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(out_path, driver="GPKG")
    return gdf


def build_rfc_da_hydraulics(
    dem_path: str | Path,
    ref_reservoirs_path: str | Path,
    ref_wb_path: str | Path,
    osm_ref_wb_path: str | Path,
    nid_clean_path: str | Path,
    hf_lakes_path: str | Path,
    ref_fp_path: str | Path,
    max_waterbody_nearest_dist_m: float,
    min_area_sqkm: float,
    out_dir: str | Path,
    out_rfcda: Path,
    work_crs: str,
    default_crs: str,
    use_hazard: bool = True,
    wb_buffer: int | float = 200,  # lakes 2.2
    res_keep: list[str] | None = None,
    lakes_keep: list[int] | None = None,
) -> gpd.GeoDataFrame:
    """
    End-to-end Python equivalent of 03_hydraulics.R.

    Produces:
      - ref_wb_elevs.gpkg
      - osm_wb_elevs.gpkg
      - rfc-da-locs.gpkg
      - rfc-da-hydraulics-v1.gpkg

    :param dem_path: path to .vrt format of dem file
    :param ref_reservoirs_path: path to reference_reservoirs file
    :param ref_wb_path: path to the file reference_waterbodies
    :param osm_ref_wb_path: path to osm_dams_all
    :param nid_clean_path: path to nid dams file
    :param hf_lakes_path: path to HF 2.2/NWM lakes to be appended
    :param max_waterbody_nearest_dist_m: maximum distance between points and waterbodies
    :param min_area_sqkm: minimum waterbody area to be considered
    :param out_dir: output directory
    :param use_hazard:
    :param wb_buffer: meters to buffer waterbodies when matching Hf 2.2/NWM lakes
    :param res_keep: list of reservoirs dam_id to keep regardless of criteria
    :param lakes_keep: list of lake_ids to keep regardless of criter
    :return: rfc_da geodataframe file
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ref_wb_elevs_path = out_dir / "ref_wb_elevs.gpkg"
    osm_wb_elevs_path = out_dir / "osm_wb_elevs.gpkg"
    rfc_da_locs_path = out_dir / "rfc-da-locs.gpkg"
    ref_res_tmp_path = out_dir / "ref_res_tmp.gpkg"
    lakes_tmp_path = out_dir / "nwm_lakes_tmp.gpkg"
    rfc_da_hydr_path = out_rfcda
    res_keep = res_keep if res_keep is not None else []
    lakes_keep = lakes_keep if lakes_keep is not None else []

    # Prep HF 2.2 lakes
    logger.info("Prepping HF 2.2 lakes")
    find_lake_duplicates(
        lakes_path=hf_lakes_path,
        lakes_layer="lakes",
        ref_wb_path=ref_wb_path,
        ref_res_path=ref_reservoirs_path,
        ref_fp_path=ref_fp_path,
        wb_buffer=wb_buffer,
        lakes_tmp_path=lakes_tmp_path,
        ref_res_tmp_path=ref_res_tmp_path,
        lakes_keep=lakes_keep,
    )

    # 1) WB elevations (ref + OSM)
    logger.info("Building reference reservoirs elevation")
    build_ref_wb_elevs(
        dem_path,
        ref_res_tmp_path,
        ref_wb_path,
        ref_wb_elevs_path,
        max_waterbody_nearest_dist_m=max_waterbody_nearest_dist_m,
        min_area_sqkm=min_area_sqkm,
        work_crs=work_crs,
        res_keep=res_keep,
    )

    logger.info("Building OSM waterbody elevation")
    build_osm_wb_elevs(
        dem_path,
        ref_res_tmp_path,
        osm_ref_wb_path,
        osm_wb_elevs_path,
        max_waterbody_nearest_dist_m=max_waterbody_nearest_dist_m,
        min_area_sqkm=min_area_sqkm,
        work_crs=work_crs,
        res_keep=res_keep,
    )

    # 2) Dam locations + NID attrs + WB elevs + dam_elev
    logger.info("Building RFC DA locations")
    df_locs = build_rfc_da_locs(
        dem_path=dem_path,
        ref_reservoirs_path=ref_res_tmp_path,
        nid_clean_path=nid_clean_path,
        ref_wb_elevs_path=ref_wb_elevs_path,
        osm_wb_elevs_path=osm_wb_elevs_path,
        out_gpkg=rfc_da_locs_path,
        work_crs=work_crs,
        default_crs=default_crs,
        max_waterbody_nearest_dist_m=max_waterbody_nearest_dist_m,
        min_area_sqkm=min_area_sqkm,
        res_keep=res_keep,
    )

    # 3) Join in minimal res (dam_id, ref_fab_fp, x, y)
    logger.info("Joining reservoirs")
    res = gpd.read_file(ref_res_tmp_path)

    res_min = res[["dam_id", "ref_fab_fp", "x", "y"]].drop_duplicates().copy()

    # df_locs is GeoDataFrame (geometry 4326); we only need attributes:
    df_attr = pd.DataFrame(df_locs.drop(columns=df_locs.geometry.name, errors="ignore"))
    df_joined = df_attr.merge(res_min, on="dam_id", how="left")

    # 4) Hydraulics
    logger.info("Calculating hydraulics")
    hydr_attrs = populate_hydraulics(df_joined, use_hazard=use_hazard)

    # 5) Combine hydraulics with res_min to build final GeoDataFrame in 5070
    #    (geometry from x,y as in R)
    # Keep first record per dam_id
    logger.info("Building final RFC-DA dataframe")
    df_joined = df_joined.sort_values("dam_id").drop_duplicates(  # or by something meaningful
        subset="dam_id", keep="first"
    )
    df_joined = df_joined.drop(columns=["Dam_Length", "dam_length"], errors="ignore")

    hydr_attrs = hydr_attrs.sort_values("dam_id").drop_duplicates(subset="dam_id", keep="first")

    hydr_attrs_list = [
        "LkArea",
        "LkMxE",
        "WeirC",
        "WeirL",
        "WeirE",
        "OrficeC",
        "OrficeA",
        "OrficeE",
        "Dam_Length",
        "ifd",
    ]

    hydr_df = df_joined.merge(
        hydr_attrs[["dam_id", "H_m"] + hydr_attrs_list],
        on="dam_id",
        how="left",
        suffixes=("", "_hydr"),
    )

    hydr_gdf = gpd.GeoDataFrame(
        hydr_df,
        geometry=gpd.points_from_xy(hydr_df["x_y"], hydr_df["y_y"]),
        crs="EPSG:5070",
    )

    hydr_gdf = hydr_gdf.drop(columns=["x_x", "y_x"]).rename(columns={"x_y": "x", "y_y": "y"})

    # Add filtered HF 2.2 Lakes
    df_lakes = gpd.read_file(lakes_tmp_path)

    df_lakes = df_lakes.rename(
        columns={
            "lake_x": "x",
            "lake_y": "y",
            "lake_id": "nwm_lake_id",
            "OrificeC": "OrficeC",
            "OrificeA": "OrficeA",
            "OrificeE": "OrficeE",
        }
    )
    df_lakes = df_lakes[["geometry", "x", "y", "nwm_lake_id", "ref_fab_fp"] + hydr_attrs_list]
    df_lakes = df_lakes.loc[~df_lakes["ref_fab_fp"].isnull(), :].copy()

    hydr_gdf = pd.concat([hydr_gdf, df_lakes], ignore_index=True)

    hydr_gdf.to_file(rfc_da_hydr_path, driver="GPKG")
    logger.info(f"Saved RFC-DA reservoirs to {rfc_da_hydr_path}")
    return hydr_gdf
