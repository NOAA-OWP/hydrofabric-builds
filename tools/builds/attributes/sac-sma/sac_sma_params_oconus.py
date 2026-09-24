"""Create Sac-SMA parameter rasters for OCONUS using a global dataset.

Data source:
s3://edfs-data/attributes/sac-sma-inputs/
The global inputs are 250m global Sac-SMA datasets from https://data.csiro.au/collection/csiro:62260?q=sac-sma&_st=keyword&_str=1&_si=1
"""

import argparse
import os
from pathlib import Path

import geopandas as gpd
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import calculate_default_transform, reproject

# The proj4 string representing ESRI:54009, World_Mollweide, the native CRS of the global sac-sma dataset
proj4_src = "+proj=moll +lon_0=0 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs +type=crs"
default_layer = "divides"
default_buffer = 1.0


def get_extent(
    data_dir: Path,
    filename: Path,
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
    gdf = gpd.read_file(os.path.join(data_dir, filename), layer=layer)
    gdf = gdf.to_crs(source_crs)
    bounds = gdf.total_bounds
    bounds[0], bounds[1], bounds[2], bounds[3] = (
        bounds[0] - buffer,
        bounds[1] - buffer,
        bounds[2] + buffer,
        bounds[3] + buffer,
    )
    return bounds


def clip_reproject(
    data_dir: Path, filename: Path, bounds: list[float], dst_res: list[float], dst_crs: str
) -> None:
    """Clips global sac-sma parameter raster to the domain and reprojects to the NHF oconus domain and saves raster"

    Parameters
    ----------
    data_dir : Path
        Path to directory containing the global sac-sma parameter raster
    filename: Path
        Filename of the global sac-sma parameter raster
    bounds: list[float]
        list of boundary coordinates defining the domain's extent
    dst_res: list[float]
        resolution of the output raster in meters
    dst_crs: str
        Output raster CRS in EPSG, e.g., EPSG:3338

    """
    dst_crs = f"EPSG:{dst_crs}"

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
        new_filename = Path(f"{parameter_name}.tif")

        with rasterio.open(os.path.join(data_dir, new_filename), "w", **kwargs) as dst:
            reproject(
                source=rasterio.band(src, 1),
                destination=rasterio.band(dst, 1),
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=dst_transform,
                dst_crs=dst_crs,
                resampling=Resampling.bilinear,
                tiled=True,
                compress="deflate",
            )


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
    parser.add_argument(
        "--crs",
        type=str,
        help="CRS in EPSG for the OCONUS NHF domain, for example 3338",
    )
    parser.add_argument(
        "--resolution",
        type=str,
        help="optional output raster resolution in meters, e.g., 1000.  Defaults to 250",
    )

    args = parser.parse_args()

    extent = get_extent(
        data_dir=Path(args.data_dir),
        filename=Path(args.extent_file),
        layer="divides",
        buffer=1,
    )

    # use 250m resolution if not specified in command line
    if args.resolution:
        dst_res = [float(args.resolution), float(args.resolution)]
    else:
        dst_res = [250.0, 250.0]

    clip_reproject(
        data_dir=Path(args.data_dir),
        filename=Path(args.global_raster_file),
        bounds=extent,
        dst_res=dst_res,
        dst_crs=args.crs,
    )
