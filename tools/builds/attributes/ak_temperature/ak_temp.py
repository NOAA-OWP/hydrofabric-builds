"""Script to generate mean temperature delta per month from TerraClimate data

Sample call for AK:
> python tools/builds/attributes/ak_temperature/ak_temp.py --dir data/terraclimate --extent data/ak_reference.gpkg --extent-lyr divides --crs 3338

Retrieve Terraclimate from source [https://thredds.northwestknowledge.net/thredds/catalog/TERRACLIMATE_ALL/data/catalog.html]
or [test] s3://edfs-data/attributes/ueb/terraclimate/

For Alaska, use NWM 4 domain. For example [data] s3://hydrofabric-data/reference/ak/ak_reference.gpkg
"""

import argparse
import warnings
from pathlib import Path

import geopandas as gpd
import xarray as xr
from zarr.errors import ZarrUserWarning

warnings.filterwarnings("ignore", category=ZarrUserWarning)


def get_extent(extent_file: Path, layer: str | None = None, buffer: float | int = 1) -> list[float]:
    """Get the clipping extent for the domain

    Parameters
    ----------
    extent_file : Path
        Path to domain file
    layer : str | None, optional
        Layer if the domain file is a GPKG, by default None
    buffer : float | int, optional
        Buffer in WGS84/4326 degrees for extent, by default 1

    Returns
    -------
    list[float]
        list of bounds (minx, miny, maxx, maxy)
    """
    gdf = gpd.read_file(extent_file, layer=layer) if layer else gpd.read_file(extent_file)
    gdf = gdf.to_crs(4326)
    bounds = gdf.total_bounds
    bounds[0], bounds[1], bounds[2], bounds[3] = (
        bounds[0] - buffer,
        bounds[1] - buffer,
        bounds[2] + buffer,
        bounds[3] + buffer,
    )
    return bounds


def clip_extent(file_list: list[Path], out_folder: Path, bounds: list[float]) -> list[Path]:
    """Clip the Terraclimate files to the extent

    Parameters
    ----------
    file_list : list[Path]
        list of terraclimate file paths
    out_folder : Path
        output folder to store clipped layers
    bounds : list[float]
        bounds to clip to (minx, miny, maxx, maxy)

    Returns
    -------
    list[Path]
        list of clipped files
    """
    out_files = [out_folder / (f.name.split(".")[0] + ".zarr") for f in file_list]
    for f, out in zip(file_list, out_files, strict=False):
        ds = xr.open_dataarray(f, engine="netcdf4").rio.write_crs(4326)
        ds = ds.rio.clip_box(minx=bounds[0], miny=bounds[1], maxx=bounds[2], maxy=bounds[3])
        ds.to_zarr(out, mode="w")
    return out_files


def process_mean(file_list: list[Path], var_name: str) -> xr.DataArray:
    """For a single variable, proces the mean for each month over the amount of years present

    Parameters
    ----------
    file_list : list[Path]
        list of paths to datasets
    var_name : str
        variable name (tmin or tmax)

    Returns
    -------
    xr.DataArray
        A single dataarray with mean per month
    """
    means = []
    datasets = [xr.open_dataset(f, engine="zarr") for f in file_list]

    # for each month concatenate the dataset years, then take mean on the time dimension (months)
    for i in range(0, 12):
        ds_new = xr.concat([ds[var_name].isel(time=i) for ds in datasets], dim="time")
        mean = ds_new.mean(dim="time")
        means.append(mean)
        del ds_new, mean

    ds_means = xr.concat(means, dim="time")
    ds_means = ds_means.assign_coords({"time": range(1, 13)})

    return ds_means


def delta(tmin: xr.DataArray, tmax: xr.DataArray, crs: int, out_dir: Path) -> None:
    """Get the temperature delta for each month and write to tif

    Parameters
    ----------
    tmin : xr.DataArray
        tmin array
    tmax : xr.DataArray
        tmax array
    crs : int
        EPSG CRS as int
    out_path : Path
        output path for tif
    """
    months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]

    ds_delta = abs(tmax - tmin)
    ds_delta = ds_delta.rio.write_crs(4326)
    ds_delta = ds_delta.rio.reproject(crs)
    for i, month in zip(range(12), months, strict=False):
        ds_delta.isel(time=i).rio.to_raster(
            out_dir / f"{month}.tif", driver="GTiff", compress="deflate", tiled=True
        )
    print(f"Saved temperature delta to {out_dir}")


def calculate_temp_delta_from_terraclimate(
    extent: Path,
    dir: Path,
    crs: int,
    yrmin: int,
    yrmax: int,
    extent_lyr: str | None = None,
) -> None:
    """Pipeline to process Terraclimate tmin and tmax layers over year range to calculate temperature delta per month based on monthy means

    Parameters
    ----------
    extent : Path
        domain extent to clip to
    data_dir : Path
        directory with terraclimate tmin and tmax netcdfs
    crs : int
        EPSG CRS as an integer. For Alaska: 3338
    out_path : Path
        output path for temperature delta tif
    yrmin : int
        minimum year for range of data to take mean
    yrmax : int
        maximum year for range of data to take mean
    extent_lyr : str | None, optional
        If the domain is a GPKG, the layer to use. Note that if using a hydrofabric, specify divides, by default None
    """
    tmax = [dir / f"TerraClimate_tmax_{yr}.nc" for yr in range(yrmin, yrmax + 1)]
    tmin = [dir / f"TerraClimate_tmin_{yr}.nc" for yr in range(yrmin, yrmax + 1)]

    print(f"Getting bounds from {extent}")
    bounds = get_extent(extent_file=extent, layer=extent_lyr)

    print("Clipping data files to extent")
    clip_dir = dir / "clipped"
    clip_dir.mkdir(exist_ok=True)
    clipped_files = clip_extent(tmax + tmin, out_folder=clip_dir, bounds=bounds)

    print(f"Getting mean from year range {yrmin} to {yrmax}")
    ds_tmax = process_mean([f for f in clipped_files if "tmax" in f.name], var_name="tmax")
    ds_tmin = process_mean([f for f in clipped_files if "tmin" in f.name], var_name="tmin")

    print("Getting temperature delta")
    out_dir = dir / "t_delta"
    out_dir.mkdir(exist_ok=True)
    delta(tmin=ds_tmin, tmax=ds_tmax, crs=crs, out_dir=out_dir)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="A script to calculate temperature delta from 30 years of TerraClimate temperature max and mins for a given extent"
    )
    parser.add_argument(
        "--extent",
        type=str,
        help="Path to GPKG extent to clip output to (e.g. Alaska southern  domain)",
    )
    parser.add_argument(
        "--extent-lyr",
        type=str,
        help="If extent is a geopackage with multiple layers, specify the layer (e.g. 'divides' in hydrofabric)",
    )
    parser.add_argument(
        "--dir",
        type=str,
        default="data/temperature",
        help="The directory containing the input TerraClimate netcdfs",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="data/temperature/temperature_delta.tif",
        help="The file path to the output file",
    )
    parser.add_argument("--crs", type=int, help="The CRS of the output file")
    parser.add_argument(
        "--yr-min", type=int, default=1991, help="Minimum year to calculate delta for. Defaults to 1991"
    )
    parser.add_argument(
        "--yr-max", type=int, default=2020, help="Maximum year to calculate delta for. Defaults to 2020"
    )

    args = parser.parse_args()

    calculate_temp_delta_from_terraclimate(
        extent=Path(args.extent),
        dir=Path(args.dir),
        crs=args.crs,
        yrmin=args.yr_min,
        yrmax=args.yr_max,
        extent_lyr=args.extent_lyr,
    )
