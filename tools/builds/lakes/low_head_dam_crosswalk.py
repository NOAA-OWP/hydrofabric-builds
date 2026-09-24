"""Crosswalk low head dams.

Script to crosswalk low head dams from USACE LHDI to 1 or 2 input lake polygon layers.

Polygons default to the NHF path for NWM lakes and reference waterbodies
    NWM lakes: ./data/sconus/lakes/input/nwm_lakes_sconus_input.gpkg
    reference-waterbodies: "./data/sconus/lakes/input/reference_waterbodies.gpkg"

Outputs to a GPKG with standard dam information
"""

import argparse
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd


def lhdi_to_gdf(lhdi_path: Path) -> gpd.GeoDataFrame:
    """Read LHD data and convert to GeoDataFrame.

    Reads the raw LHD data and filters on the review status being Confirmed.  Gathers
    necessary column to populate the standard dam information fields and adds default
    values for missing attributes.

    Parameters
    ----------
    lhdi_path : Path
        Path to the LHD geopackage file.

    Returns
    -------
    gpd.GeoDataFrame
        GeoDataFrame containing the verified low head dam data with standard dam columns.
    """
    lhdi_gdf = gpd.read_file(lhdi_path, layer="low_head_dams_raw")
    lhdi_verified = lhdi_gdf[lhdi_gdf.reviewStatusId == "Confirmed"]
    lhdi_verified = lhdi_verified.reset_index(drop=True)
    lhdi_columns_to_keep = [
        "name",
        "lhdId",
        "length",
        "damHeight",
        "hydraulicHeight",
        "nidHeight",
        "damVolume",
        "purposeIds",
        "geometry",
    ]
    lhdi_columns_rename = {
        "name": "dam_name",
        "lhdId": "nidid",
        "length": "dam_length",
        "damHeight": "dam_height",
        "hydraulicHeight": "hydraulic_height",
        "nidHeight": "nid_height",
        "damVolume": "nid_storage",
        "purposeIds": "purposes",
    }
    lhdi_columns_retype = {
        "dam_length": "float64",
        "dam_height": "float64",
        "hydraulic_height": "float64",
        "nid_height": "float64",
        "nid_storage": "float64",
    }
    lhdi_verified = lhdi_verified[lhdi_columns_to_keep]
    lhdi_verified = lhdi_verified.rename(columns=lhdi_columns_rename)
    lhdi_verified = lhdi_verified.astype(lhdi_columns_retype)
    lhdi_verified = lhdi_verified.assign(
        dam_type="low head dam",
        spillway_type="",
        spillway_width="",
        structural_height=np.nan,
        surface_area=np.nan,
        wb_areasqkm=np.nan,
        normal_storage=np.nan,
        max_storage=np.nan,
        hazard="",
    )
    return lhdi_verified


def create_lhd_polygons(crosswalk_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Create buffered low head dam polygons.

    Generates square buffer polygons around low head dam points based on the dam length.

    Parameters
    ----------
    crosswalk_gdf : gpd.GeoDataFrame
        GDF containing crosswalked low head dam points.

    Returns
    -------
    gpd.GeoDataFrame
        GDF containing buffered low head dam polygons.
    """
    lhd_gdf = crosswalk_gdf.to_crs("EPSG:5070")
    lhd_gdf["buffer_dist"] = (lhd_gdf["dam_length"].fillna(20)) / 2
    lhd_gdf["geometry"] = lhd_gdf.buffer(distance=(lhd_gdf.buffer_dist), cap_style="square")
    lhd_gdf = lhd_gdf.drop(columns=["dist_to_lake_1", "dist_to_lake_2", "buffer_dist"])
    return lhd_gdf


def crosswalk_low_head_dams(
    lhd: gpd.GeoDataFrame,
    lakes_1: gpd.GeoDataFrame,
    lakes_2: gpd.GeoDataFrame | None,
    lakes_1_key: str,
    lakes_2_key: str | None,
    buffer: int = 300,
) -> gpd.GeoDataFrame:
    """Crosswalk low head dams to lake layers.

    Creates a crosswalk between low head dams and lake layers, identifying dams that are
    within a specified buffer distance from lakes.  Dams that are less than the buffer
    distance from a lake have the geometry from the lake transfered to the low head dam
    dataset.  Points that do not lie within the buffer distance have a new polygon
    created.

    Parameters
    ----------
    lhd : gpd.GeoDataFrame
        GeoDataFrame containing low head dam points.
    lakes_1 : gpd.GeoDataFrame
        The primary lake polygons to crosswalk to.
    lakes_2 : gpd.GeoDataFrame | None
        A secondary set of lake polygons to crosswalk to.
    lakes_1_key : str
        ID in first lake layer
    lakes_2_key : str | None
        ID in second lake layer
    buffer : int, optional
        Buffer distance to exclude dams near lakes, by default 300.

    Returns
    -------
    gpd.GeoDataFrame
        Crosswalked low head dam points with distance to lakes and filtered by buffer distance.
    """
    # Columns to retain for final dataset
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
        "geometry",
    ]

    lhd = lhd.to_crs(lakes_1.crs)
    lakes_1["lakes_1_geom"] = lakes_1.geometry
    lhd_join_lakes_1 = gpd.sjoin_nearest(
        lhd, lakes_1, how="left", distance_col="dist_to_lake_1", max_distance=buffer
    )
    lhd_join_lakes_1_valid = (
        lhd_join_lakes_1.loc[lhd_join_lakes_1[lakes_1_key].notnull()].copy().reset_index(drop=True)
    )
    lhd_join_lakes_1_valid.rename(columns={lakes_1_key: "lake_id"}, inplace=True)
    lhd_join_lakes_1_valid.set_geometry("lakes_1_geom", inplace=True)
    lhd_join_lakes_1_valid.drop(columns=["geometry"], inplace=True)
    lhd_join_lakes_1_valid.rename(columns={"lakes_1_geom": "geometry"}, inplace=True)
    lhd_missing_lakes_1 = (
        lhd_join_lakes_1.loc[lhd_join_lakes_1[lakes_1_key].isnull()].copy().reset_index(drop=True)
    )
    lhd_missing_lakes_1 = lhd_missing_lakes_1[keep_cols]
    if isinstance(lakes_2, gpd.GeoDataFrame):
        lakes_2 = lakes_2.to_crs(lakes_1.crs)
        lakes_2["lakes_2_geom"] = lakes_2.geometry
        lhd_join_lakes_2 = gpd.sjoin_nearest(
            lhd_missing_lakes_1,
            lakes_2,
            how="left",
            distance_col="dist_to_lake_2",
            max_distance=buffer,
        )
        lhd_join_lakes_2_valid = (
            lhd_join_lakes_2.loc[lhd_join_lakes_2[lakes_2_key].notnull()].copy().reset_index(drop=True)
        )
        lhd_join_lakes_2_valid.rename(columns={lakes_2_key: "lake_id"}, inplace=True)
        lhd_join_lakes_2_valid.set_geometry("lakes_2_geom", inplace=True)
        lhd_join_lakes_2_valid.drop(columns=["geometry"], inplace=True)
        lhd_join_lakes_2_valid.rename(columns={"lakes_2_geom": "geometry"}, inplace=True)
        lhd_missing_lakes_2 = (
            lhd_join_lakes_2.loc[lhd_join_lakes_2[lakes_2_key].isnull()].copy().reset_index(drop=True)
        )
        lhd_missing_lakes_2 = lhd_missing_lakes_2[keep_cols]
        lhd_missing_polys = create_lhd_polygons(lhd_missing_lakes_2)
        keep_cols.append("lake_id")
        output = pd.concat([lhd_join_lakes_1_valid, lhd_join_lakes_2_valid, lhd_missing_polys])
    else:
        lhd_missing_polys = create_lhd_polygons(lhd_missing_lakes_1)
        output = pd.concat([lhd_join_lakes_1_valid, lhd_missing_polys])

    # Add lake_id to keep_cols
    output = output[keep_cols]
    output["lake_id"] = output["lake_id"].astype(pd.Int64Dtype()).astype(str)
    output["lake_id"] = output["lake_id"].replace("<NA>", None)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--crosswalk",
        action="store true",
        help="Option to crosswalk low head dam data to lakes polygon layer",
    )
    parser.add_argument(
        "--output-lhd-poly",
        type=Path,
        default="./data/sconus/lakes/input/lhd_crosswalk.gpkg",
        help="Output path for low head dam polygons",
    )
    parser.add_argument(
        "--lhd-inventory",
        type=Path,
        default="./data/sconus/lakes/input/low_head_dams.gpkg",
        help="Input path for the low head dams inventory.",
    )
    parser.add_argument(
        "--lakes-1",
        type=Path,
        default="./data/sconus/lakes/input/nwm_lakes_sconus_input.gpkg",
        help="Input path for nwm lakes data to crosswalk. This will be the first crosswalk.",
    )
    parser.add_argument(
        "--lakes-1-key",
        type=str,
        default="newID",
        help="The ID key for lakes 1 layer.",
    )
    parser.add_argument(
        "--lakes-2",
        type=Path,
        default="./data/sconus/lakes/input/reference_waterbodies.gpkg",
        help="Input path for lakes reference waterbodies to crosswalk. This will be the second crosswalk for any lakes missing in crossswalk 1.",
    )
    parser.add_argument(
        "--lakes-2-key",
        type=str,
        default="comid",
        help="The ID key for lakes 2 layer.",
    )
    args = parser.parse_args()

    if args.crosswalk:
        lakes_1 = gpd.read_file(args.lakes_1)
        lakes_2 = gpd.read_file(args.lakes_2) if Path(args.lakes_2).exists() else None
        print("Reading low head dams inventory")
        lhd = lhdi_to_gdf(args.lhd_inventory)
        print("Crosswalking low head dams to lakes")
        gdf_cross = crosswalk_low_head_dams(
            lhd=lhd,
            lakes_1=lakes_1,
            lakes_2=lakes_2,
            lakes_1_key=args.lakes_1_key,
            lakes_2_key=args.lakes_2_key,
        )
        print("Creating low head dam polygons from crosswalked data")
        gdf_cross.to_file(args.output_lhd_poly)
        print(f"Saved crosswalked low head dam data to {args.output_lhd_poly}")
