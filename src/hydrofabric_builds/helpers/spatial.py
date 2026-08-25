from pathlib import Path

import numpy as np
import rasterio
import rioxarray
import xarray as xr
from rasterio import shutil as rio_shutil  # ty: ignore[unresolved-import]
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT


def resample_percent(
    raster: xr.DataArray, target_resolution: int | float = 250, bands: list[int] | None = None
) -> xr.DataArray:
    """Resamples a raster from a finer resolution to coarser resolution where each new cell is the percent of positive cells in the finer resolution.

    Parameters
    ----------
    raster : xr.DataArray
        Binary raster as xarray DataArray
    target_resolution : int | float, optional
        Output raster cell resolution to resample to by default 250
    bands : list[int] | None, optional
        The bands in the raster to use. If nothing is passed, all bands will be calculated, by default None

    Returns
    -------
    xr.DataArray
        Resampled raster where each cell is percent of aggregated True cells

    Raises
    ------
    ValueError
        Raises if CRS and target resolution do not appear to match (meters vs degrees)
    """
    current_res_x, current_res_y = raster.rio.resolution()
    current_width = raster.rio.width
    current_height = raster.rio.height

    # Calculate the scale factor
    scale_x = abs(target_resolution / current_res_x)
    scale_y = abs(target_resolution / current_res_y)

    # Calculate new dimensions
    new_width = int(current_width / scale_x)
    new_height = int(current_height / scale_y)

    # Create an empty array for our percentages
    percentages = np.zeros((raster.rio.count, new_height, new_width))

    # Get the data as a numpy array
    data = raster.values

    # Do all bands if none provided
    if not bands:
        bands = list(range(raster.rio.count))

    for b in bands:
        # Get the band data
        band_data = data[b]

        # Identify pixels with value 1
        binary_mask = (band_data == 1).astype(np.float32)

        # Calculate percentage for each block
        for i in range(new_height):
            for j in range(new_width):
                # Calculate corresponding indices in the original raster
                start_y = int(i * scale_y)
                end_y = int(min((i + 1) * scale_y, current_height))
                start_x = int(j * scale_x)
                end_x = int(min((j + 1) * scale_x, current_width))

                # Extract the block from the original data
                block = binary_mask[start_y:end_y, start_x:end_x]

                # Calculate total number of valid pixels in the block
                total_pixels = block.size

                # Skip if no valid pixels (avoid division by zero)
                if total_pixels == 0:
                    percentages[b, i, j] = np.nan
                    continue

                # Calculate percentage of positive pixels
                flood_count = np.sum(block)
                percentages[b, i, j] = (flood_count / total_pixels) * 100

    # Create a new raster with the percentages
    try:
        percentage_raster = raster.rio.reproject(
            raster.rio.crs, shape=(new_height, new_width), resampling=Resampling.nearest
        )
    except ZeroDivisionError as e:
        raise ValueError(
            "There is a problem with the target resolution. Are you using a geographic CRS with resolutions < 1 degree? Try a different resolution."
        ) from e

    # Replace the values with our percentages
    percentage_raster.values = percentages

    del data, raster

    return percentage_raster


def regrid_percent(
    grid_path: Path, input_path: Path, output_path: Path, resampling: Resampling = Resampling.nearest
) -> None:
    """Resample using percentage method and regrid output to a target raster.

    Parameters
    ----------
    grid_path : Path
        Path to grid to match
    input_path : Path
        Path to input dataset
    output_path : Path
        Path to output dataset
    resampling : Resampling, optional
        Specify the rasterio resampling style to use. Defaults to nearest to preserve percent values, by default Resampling.nearest

    Raises
    ------
    RuntimeError
        For resampling and regrid failure
    """
    # get grid properties
    with rasterio.open(grid_path) as src:
        grid_width, grid_height = src.width, src.height
        grid_transform = src.transform
        grid_resolution = grid_transform[0]
        grid_crs = src.crs

    vrt_options = {
        "resampling": resampling,
        "crs": grid_crs,
        "transform": grid_transform,
        "height": grid_height,
        "width": grid_width,
    }

    temp_path = input_path.parent / "temp.tif"

    try:
        print(f"Regridding {input_path}")
        ras = rioxarray.open_rasterio(input_path, chunks="auto", masked=True).astype("uint8")  # ty: ignore[invalid-argument-type]
        percent_raster = resample_percent(ras, target_resolution=grid_resolution)
        percent_raster.rio.to_raster(temp_path)

        with rasterio.open(temp_path) as src:
            with WarpedVRT(src, dtype="float32", **vrt_options) as vrt:
                rio_shutil.copy(vrt, output_path, driver="GTiff", tiled="YES", compress="deflate")
        del ras
        print(f"Finished regridding {input_path}")
    except Exception as e:
        raise RuntimeError(f"Could not resample and write {input_path} to {output_path}") from e

    finally:
        if temp_path.exists():
            temp_path.unlink()
