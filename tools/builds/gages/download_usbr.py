"""USBR RISE feature service as of 7/13/26

https://services1.arcgis.com/ixD30sld6F8MQ7V5/ArcGIS/rest/services/RISE_point_locations_(view)/FeatureServer/0
Calling with default args will save to ./usbr.gpkg
Call `download_usbr.py --lakes-only` to filter by lakes
"""

import argparse
from pathlib import Path

import geopandas as gpd
import requests
from requests.exceptions import RequestException


def download_usbr(output: Path, url: str, lakes_only: bool = True) -> None:
    """Download USBR gpkg from RISE ArcGIS Feature Service

    Use lakes_only to filter to location type 'Lake/Reservoir'
    """
    all_features = []
    offset = 0
    chunk_size = 1000
    max_retries = 3

    while True:
        params = {
            "where": "1=1",
            "outFields": "*",
            "f": "geojson",
            "resultOffset": offset,
            "resultRecordCount": chunk_size,
            "returnGeometry": "true",
        }

        for attempt in range(max_retries):
            try:
                response = requests.post(url, data=params, timeout=60)
                response.raise_for_status()
            except RequestException as e:
                if attempt < max_retries - 1:
                    continue
                else:
                    raise e

        response = response.json()
        features = response.get("features", [])

        if not features:
            break

        all_features.extend(features)
        offset += len(features)

    print(f"Downloaded {len(all_features)} features.")

    gdf = gpd.GeoDataFrame.from_features(all_features, crs=4326)

    if lakes_only:
        gdf = gdf.loc[gdf["type"] == "Lake/Reservoir", :].copy().reset_index(drop=True)

    gdf.to_file(output)
    print(f"Saved USBR to {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default="./usbr.gpkg",
        help="Output path for USBR data",
    )
    parser.add_argument(
        "--url",
        type=str,
        default="https://services1.arcgis.com/ixD30sld6F8MQ7V5/ArcGIS/rest/services/RISE_point_locations_(view)/FeatureServer/0/query",
        help="Input feature service URL",
    )
    parser.add_argument("--lakes-only", action="store_true", help="Filter to only Lakes/Reservoir type")

    args = parser.parse_args()

    download_usbr(output=Path(args.output), url=args.url, lakes_only=args.lakes_only)
