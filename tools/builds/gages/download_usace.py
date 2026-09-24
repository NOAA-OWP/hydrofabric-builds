"""
Script to crosswalk USACE gages with flow data to 1 or 2 input lake polygon layers.

The second polygon layer will be used to fill any missing values in first polygon layer.
Sample calls:
Download USACE only:  uv run tools/builds/gages/download_usace.py --download
Crosswalk only: uv run tools/builds/gages/download_usace.py --crosswalk
Download and crosswalk: uv run tools/builds/gages/download_usace.py --download --crosswalk
Specify crosswalk polygons and output: uv run tools/builds/gages/download_usace.py --download --crosswalk
    --output data/usace.gpkg --lakes-1 data/lakes_1.gpkg --lakes-1-key comid --lakes-2 data/lakes_2.gpkg
    --lakes-2-key id
Polygons default to the NHF path for NWM lakes and reference waterbodies
    NWM lakes: ./data/sconus/lakes/input/nwm_lakes_sconus_input.gpkg
    reference-waterbodies: "./data/sconus/lakes/input/reference_waterbodies.gpkg"
Lakes-2 is used for any missing values in lakes-1.
Outputs a GPKG with location, office, full-name, dist_to_lake, lake_id
"""

import argparse
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
from requests.exceptions import RequestException

BASE_URL = "https://cwms-data.usace.army.mil/cwms-data/"
GROUPS = "location/group?location-category-like=flow"
LOCATION_URL = "locations/{site}?office={office}"


def download_usace(max_retries: int = 3, output_file: Path = Path("./usace_gages.csv")) -> gpd.GeoDataFrame:
    """Download USACE locations with flow type data.

    Parameters
    ----------
    max_retries : int, optional
        Max retries for HTTP requests, by default 3
    output_file : Path, optional
        Output CSV, by default Path("./usace_gages.csv")
    """
    # get list of location groups with flow category
    for attempt in range(max_retries):
        try:
            groups = requests.get(f"{BASE_URL}{GROUPS}", timeout=60)
            groups.raise_for_status()
            data = groups.json()
            break
        except Exception as e:
            if attempt < max_retries - 1:
                continue
            else:
                raise e

    # pairs of office ID and location ID
    vals = [
        (i["office-id"], i["shared-ref-location-id"])
        for i in data
        if i.get("shared-ref-location-id", None) is not None
    ]

    # de-duplicate office-id, location ID pairings - there are often duplicates due to multiple types of flow datasets available
    unique = []
    for val in vals:
        if val not in unique:
            unique.append(val)

    # query the location URL for each location/office pairing and save relevant data (lat, lon, long-name when available)
    output = []
    failed = []
    for val in unique:
        for attempt in range(max_retries):
            try:
                loc_url = LOCATION_URL.format(site=val[1], office=val[0], timeout=60)
                r = requests.get(f"{BASE_URL}{loc_url}")
                r.raise_for_status()
                data = r.json()
                try:
                    output.append((val[1], val[0], data["latitude"], data["longitude"], data["long-name"]))
                # sometimes long-name is missing
                except KeyError:
                    output.append((val[1], val[0], data["latitude"], data["longitude"], None))
                break
            except RequestException as e:
                if attempt < max_retries - 1:
                    print(f"Retrying {val[1]}")
                    continue
                else:
                    failed.append((val[1], str(e)))
                    print(f"Failed {val[1]} for {str(e)}")
                    continue
            except Exception as e:  # noqa: BLE001
                failed.append((val[1], str(e)))
                print(f"Failed {val[1]} for {str(e)}")
                continue

    df = pd.DataFrame.from_records(
        data=output, columns=["location", "office", "latitude", "longitude", "full-name"]
    )
    gdf = gpd.GeoDataFrame(
        crs=4326,
        data={"location": df["location"], "office": df["office"], "full-name": df["full-name"]},
        geometry=gpd.points_from_xy(x=df["longitude"], y=df["latitude"]),
    )

    print(f"Failed gages: {len(failed)}")
    return gdf


def crosswalk_usace_lakes(
    gages: gpd.GeoDataFrame,
    lakes_1: gpd.GeoDataFrame,
    lakes_2: gpd.GeoDataFrame | None,
    lakes_1_key: str,
    lakes_2_key: str | None,
    buffer: int = 300,
) -> gpd.GeoDataFrame:
    """Crosswalk gages to 1 or 2 lake polygon layers.

    The first polygon layer will be joined. Any gages missing in the first layer will be joined in
    the second layer.

    A spatial nearest join is used with a max buffer. The distance column is retained in output
    as dist_to_lake

    Parameters
    ----------
    gages : gpd.GeoDataFrame
        Gage gdf
    lakes_1 : gpd.GeoDataFrame
        The primary lake polygons to crosswalk to
    lakes_2 : gpd.GeoDataFrame | None
        A secondary set of lake polygons to crosswalk to
    lakes_1_key : str
        ID in first lake layer
    lakes_2_key : str | None
        ID in second lake layer
    buffer : int, optional
        Buffer for spatial join, by default 300

    Returns
    -------
    gpd.GeoDataFrame
        GDF of gages with new columns `lake_id` and `dist_to_lake`
    """
    # join gages to first lakes polygon layer
    gages = gages.to_crs(lakes_1.crs)
    gages_joined = gpd.sjoin_nearest(
        gages, lakes_1, how="left", max_distance=buffer, distance_col="dist_to_lake"
    )
    gages_joined.rename(columns={lakes_1_key: "lake_id"}, inplace=True)
    gages_joined = (
        gages_joined[["location", "office", "full-name", "dist_to_lake", "lake_id", "geometry"]]
        .copy()
        .reset_index(drop=True)
    )
    if pd.api.types.is_numeric_dtype(gages_joined["lake_id"].dtype):
        gages_joined["lake_id"] = gages_joined["lake_id"].astype(pd.Int64Dtype()).astype(str)

    # join missing lakes to optional second lakes polygons layer
    if isinstance(lakes_2, gpd.GeoDataFrame):
        lakes_2 = lakes_2.to_crs(lakes_1.crs)
        # separate missing gages
        gages_missing = gages_joined.loc[
            gages_joined["lake_id"].isnull(), ["location", "office", "full-name", "geometry"]
        ].copy()
        gages_joined = gages_joined.loc[~gages_joined["lake_id"].isnull()].copy()

        # spatial join nearest
        gages_joined_2 = gpd.sjoin_nearest(
            gages_missing, lakes_2, how="left", max_distance=buffer, distance_col="dist_to_lake"
        )
        gages_joined_2.rename(columns={lakes_2_key: "lake_id"}, inplace=True)
        gages_joined_2 = (
            gages_joined_2[["location", "office", "full-name", "dist_to_lake", "lake_id", "geometry"]]
            .copy()
            .reset_index(drop=True)
        )
        if pd.api.types.is_numeric_dtype(gages_joined_2["lake_id"].dtype):
            gages_joined_2["lake_id"] = gages_joined_2["lake_id"].astype(pd.Int64Dtype()).astype(str)
        output = pd.concat([gages_joined, gages_joined_2])

    else:
        output = gages_joined

    output["lake_id"] = output["lake_id"].replace("<NA>", None)

    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--download",
        action="store_true",
        help="Option to download USACE data",
    )
    parser.add_argument(
        "--crosswalk",
        action="store_true",
        help="Option to crosswalk USACE data to lakes polygon layer",
    )
    parser.add_argument(
        "--output-download",
        type=Path,
        default="./usace_download.gpkg",
        help="Output path for USBR data",
    )
    parser.add_argument(
        "--output-crosswalk",
        type=Path,
        default="./usace_crosswalk.gpkg",
        help="Output path for USBR data",
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

    usace = None
    if args.download:
        print("Downloading USACE data from API")
        usace = download_usace()
        usace.to_file(args.output_download)
        print(f"Saved downloaded USACE data to {args.output_download}")

    if args.crosswalk:
        if not isinstance(usace, gpd.GeoDataFrame):
            print(f"Loading USACE from {args.output_download}")
            usace = gpd.read_file(args.output_download)
        lakes_1 = gpd.read_file(args.lakes_1)
        lakes_2 = gpd.read_file(args.lakes_2) if Path(args.lakes_2).exists() else None
        print("Crosswalking USACE gages")
        gdf = crosswalk_usace_lakes(
            gages=usace,
            lakes_1=lakes_1,
            lakes_2=lakes_2,
            lakes_1_key=args.lakes_1_key,
            lakes_2_key=args.lakes_2_key,
        )
        gdf.to_file(args.output_crosswalk)
        print(f"Saved crosswalked USACE data to {args.output_crosswalk}")
