"""Create Sac-SMA parameters using the NWS rasters for CONUS and a global dataset for the rest of superconus.

Data source:
s3://edfs-data/attributes/sac-sma-inputs/
The global inputs are 250m global Sac-SMA datasets from https://data.csiro.au/collection/csiro:62260?q=sac-sma&_st=keyword&_str=1&_si=1
The conus inputs are 4km rasters created from the NWS XMRG data: https://hydrology.nws.noaa.gov/pub/Sacramento_Snow_Parameters/sac_par/ssurgo/
"""

import argparse
import os
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
import rioxarray
import xarray as xr
from rasterio.enums import Resampling
from rasterio.warp import calculate_default_transform, reproject

dst_crs = "EPSG:5070"
# The proj4 string representing ESRI:54009, World_Mollweide, the native CRS of the global sac-sma dataset
proj4_src = "+proj=moll +lon_0=0 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs +type=crs"
default_layer = "divides"
default_buffer = 1.0


def get_extent(
    data_dir: Path,
    filename: Path | None = None,
    layer: str = default_layer,
    buffer: float = default_buffer,
    source_crs: str = proj4_src,
) -> list[float]:
    """Get domain extent from a specified layer in the NHF gpkg, if provided, or use superconus default.

    Parameters
    ----------
    data_dir : Path
        Path to directory containing the gpkg
    filename: Path
        Filename of gpkg if provided.
    layer: str
        gpkg layer to use for the extent
    buffer: float
        buffer in meters (in sac-sma dataset native crs, ESRI:54009 - World_Mollweide)
    source_crs: str
        global sac-sma dataset crs.  This should always be the default (ESRI:54009 - World_Mollweide)

    Returns
    -------
    list[float]
        list of extent coordinates
    """
    if filename:
        gdf = gpd.read_file(os.path.join(data_dir, filename), layer=layer)
        gdf = gdf.to_crs(source_crs)
        bounds = gdf.total_bounds
        bounds[0], bounds[1], bounds[2], bounds[3] = (
            bounds[0] - buffer,
            bounds[1] - buffer,
            bounds[2] + buffer,
            bounds[3] + buffer,
        )
    else:
        bounds = np.array([-10738945.66206765, 3034274.86883132, -5310420.57022129, 6171717.53817575])

    return bounds


def clip_reproject(
    data_dir: Path, filename: Path, bounds: list[float], dst_res: list[float], dst_crs: str = dst_crs
) -> Path:
    """Clips global sac-sma parameter raster to the domain and reprojects to EPSG:5070 and saves a temporary raster"

    Parameters
    ----------
    data_dir : Path
        Path to directory containing the global sac-sma parameter raster
    filename: Path
        Filename of the global sac-sma parameter raster
    bounds: list[float]
        list of boundary coordinates defining the domain's extent
    dst_res: list[float]
        resolution of NWS raster which will be used for the output raster
    dst_crs: str
        crs of NWS raster which will be used for the output raster

    Returns
    -------
    Path
        path and filename of temporary raster
    """
    with rasterio.open(os.path.join(data_dir, filename)) as src:
        # Create transform for new crs.
        dst_transform, dst_width, dst_height = calculate_default_transform(
            src.crs,
            dst_crs,
            src.width,
            src.height,
            bounds[0],
            bounds[1],
            bounds[2],
            bounds[3],
            resolution=dst_res,
        )

        # Update metadata for the destination file
        kwargs = src.meta.copy()
        kwargs.update(
            {
                "crs": dst_crs,
                "transform": dst_transform,
                "width": dst_width,
                "height": dst_height,
                "tiled": True,
                "compress": "deflate",
            }
        )

        # 3. Write the reprojected and resampled data
        parameter_name = str(filename).split("_")[0]
        new_filename = Path(f"{parameter_name}_temp.tif")

        with rasterio.open(os.path.join(data_dir, new_filename), "w", **kwargs) as dst:
            reproject(
                source=rasterio.band(src, 1),
                destination=rasterio.band(dst, 1),
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=dst_transform,
                dst_crs=dst_crs,
                resampling=Resampling.bilinear,
            )
    return new_filename


def combine_rasters(data_dir: Path, superconus_raster: Path, conus_raster: Path) -> None:
    """Combines the NWS CONUS raster and the superconus temporary raster

    Parameters
    ----------
    data_dir : Path
        Path to directory containing the superconus parameter raster and conus NWS raster
    superconus_raster: Path
        Filename of the superconus parameter raster
    conus_raster: list[float]
        Filename of the conus NWS parameter raster

    Returns
    -------
    None
    """
    # Read temporary superconus raster and NWM conus raster
    superconus = rioxarray.open_rasterio(os.path.join(data_dir, superconus_raster)).squeeze()
    conus = rioxarray.open_rasterio(os.path.join(data_dir, conus_raster)).squeeze()

    # resample superconus raster to the resolution of the NWS raster
    superconus = superconus.rio.reproject(
        superconus.rio.crs, resolution=conus.rio.resolution(), resampling=Resampling.bilinear
    )

    # Expand the extent of the conus raster to match that of the superconus raster
    conus_matched = conus.rio.reproject_match(superconus)
    conus_matched_data = conus_matched.to_numpy()
    superconus_data = superconus.to_numpy()
    no_data = conus.rio.nodata

    # Create a numpy array where pixels with in CONUS contain the NWS value and those outside contain
    # the superconus value.
    new_array = np.where(conus_matched_data != no_data, conus_matched_data, superconus_data)

    # create new raster
    new_raster = xr.DataArray(
        new_array, coords=superconus.coords, dims=superconus.dims, attrs=superconus.attrs
    )

    # create results directory and save raster
    Path(f"{data_dir}/combined").mkdir(parents=True, exist_ok=True)
    new_filename = Path(f"{data_dir}/combined/{conus_raster}")
    new_raster.rio.to_raster(new_filename, tiled=True, compress="deflate")

    # remove temporary file
    Path(data_dir, superconus_raster).unlink(missing_ok=True)


def get_resolution(data_dir: Path, filename: Path) -> list[float]:
    """Returns the resolution of a raster file

    Parameters
    ----------
    data_dir : Path
        Path to directory containing the raster
    filename: Path
        Filename of the raster

    Returns
    -------
    list[float]
        resolution of the raster
    """
    data = rioxarray.open_rasterio(Path.joinpath(data_dir, filename))
    # make sure there are no negative numbers in the resolution
    return [abs(x) for x in data.rio.resolution()]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A script to clip and reproject sac-sma parameters")
    parser.add_argument(
        "--data_dir",
        type=str,
        help="Path to directory containing input rasters and where the output rasters will be written.",
    )
    parser.add_argument("--global_raster_file", type=str, help="filename for global sac-sma raster")
    parser.add_argument(
        "--extent_file",
        type=str,
        help="optional NHF domain gpkg for the extent, if not provided, a default superconus"
        "extent will be used.",
    )
    parser.add_argument("--conus_raster", type=str, help="filename for NWS CONUS sac-sma raster")

    args = parser.parse_args()

    extent = get_extent(
        data_dir=Path(args.data_dir),
        filename=Path(args.extent_file) if args.extent_file else None,
        layer="divides",
        buffer=1,
    )

    resolution = get_resolution(data_dir=Path(args.data_dir), filename=Path(args.conus_raster))
    filename = clip_reproject(
        data_dir=Path(args.data_dir),
        filename=Path(args.global_raster_file),
        bounds=extent,
        dst_res=resolution,
    )
    combine_rasters(
        data_dir=Path(args.data_dir), superconus_raster=filename, conus_raster=Path(args.conus_raster)
    )
