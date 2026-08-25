from pathlib import Path
from zipfile import ZipFile

import requests
import xarray as xr
from rasterio.enums import Resampling
from tqdm import tqdm

from hydrofabric_builds.helpers.spatial import regrid_percent
from hydrofabric_builds.schemas.irrigation_constants import CDL_URL_BASE, CROPS


def classify_irrigation(
    min_yr: int, max_yr: int, wd: Path, download: bool, unzip: bool, classify: bool, aggregate: bool
) -> None:
    """Classify the USDA NASS Cropland Data Layer to crops likely to be irrigated.

    The final output is a single boolean layer where 1 represents at least one irrigated crop year and 0 represents None.
    Intermediate outputs are a classified layer for each year.

    Includes flags to skip undesired parts of pipeline. If no flags, the entire pipeline will be skipped.
    Flags include:
    - download: if files should be downloaded
    - unzip: if downloaded zips should be unzipped
    - classify: if each year's layer should be classified
    - aggeregate: if the range of requested years should be temporally aggregated to one layer

    NOTE: .ovr files included in CDL zips can be deleted (5 GB each)

    NOTE: RAM requirements - Running CONUS-scale layers may need 20-40 GB RAM

    Args:
        min_yr (int): First CDL year to process
        max_yr (int): Last year to process. If only one year needed, set max_yr to equal min_yr
        wd (Path): Working directory for data
        download (bool): Flag to download files: If True, will download zips. If False, skip downloading
        unzip (bool): Flag to unzip files: If True, will unzip. If False, skip unzipping
        classify (bool): Flag to classify individual layers based on crop list. If True, all layers will be classified
                        If False, skipp classification
        aggregate (bool): Flag to aggregate all layers temporally.
                    This will generate one boolean layer where True means at least one year had irrigated crop
    """
    # set years
    if min_yr == max_yr:
        cdl_years = [min_yr]
    else:
        cdl_years = range(min_yr, max_yr + 1)  # type: ignore[assignment]
    output_path = wd / f"irrigation_{min_yr}_{max_yr}.tif"

    cdl_urls = [f"{CDL_URL_BASE}{year}_30m_cdls.zip" for year in cdl_years]

    # Download from source
    if download:
        for i, year in enumerate(tqdm(cdl_years)):
            print(f"Downloading {cdl_urls[i]}")
            response = requests.get(cdl_urls[i])
            with open(wd / f"{year}_30m_cdls.zip", mode="wb") as f:
                f.write(response.content)

    # unzip downloaded files
    # NOTE: .ovr files can be deleted if need additional space
    if unzip:
        for year in tqdm(cdl_years):
            print(f"Unzipping {year}")
            with ZipFile(wd / f"{year}_30m_cdls.zip") as z:
                z.extractall(wd)

    # classify individual years
    if classify:
        for yr in tqdm(cdl_years):
            print(f"Classifying {yr}")
            cdl = wd / f"{yr}_30m_cdls.tif"
            ds = xr.open_dataset(cdl, engine="rasterio", chunks="auto", masked=True).astype("uint8")
            temp_ds = xr.where(ds.astype("uint8").band_data.isin(CROPS), 1, 0).astype("uint8").compute()

            print(f"Writing {yr} to tif")
            temp_ds.rio.to_raster(wd / f"irrigation_{yr}.tif", compress="deflate", tiled="YES", crs=5070)
            del ds, temp_ds

    # Aggregate all years requested to single boolean layer
    if aggregate:
        if len(cdl_years) < 2:
            print("Temporal aggregation was requested, but only one year was provided. Skipping aggregation.")
            return

        ds = xr.open_dataset(
            wd / f"irrigation_{cdl_years[0]}.tif", engine="rasterio", chunks="auto", masked=True
        ).astype("uint8")
        ds_new = ds.copy(deep=True)
        crs = ds.rio.crs
        del ds

        for yr in tqdm(cdl_years[1:]):
            print(f"Aggregating {yr}")
            ds = xr.open_dataset(
                wd / f"irrigation_{yr}.tif", engine="rasterio", chunks="auto", masked=True
            ).astype("uint8")
            ds_new = xr.where(ds == 1, 1, ds_new).astype("uint8").compute()
            del ds

        print("Saving temporally aggregated raster")
        ds_new.rio.write_crs(crs, inplace=True)
        ds_new.band_data.rio.to_raster(output_path, compress="deflate", tiled="YES")
        del ds_new
        print(f"Saved temporally aggregated raster to {output_path}")


def regrid_irrigation(wd: Path, grid_path: Path, regrid_list: list[str]) -> None:
    """Resample and regrid irrigation layers. Converts fine to coarse resolution where value is percent of irrigated cells.

    Args:
        wd (Path): Working directory for data
        grid_path (Path_): Path to grid to match
        regrid_list (list[str]): List of years to regrid
    """
    print(f"Regridding {regrid_list} - each year may take 10+ minutes")
    for yr in tqdm(regrid_list):
        input_path = wd / f"irrigation_{yr}.tif"
        output_path = wd / f"irrigation_{yr}_regrid.tif"

        regrid_percent(
            grid_path=grid_path, input_path=input_path, output_path=output_path, resampling=Resampling.nearest
        )
