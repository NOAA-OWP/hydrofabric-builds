import argparse
import struct
import sys

import numpy as np
import rioxarray  # noqa: F401
import xarray as xr
from rasterio.warp import Resampling


def xmrgtoascii(path: str, filename: str) -> dict:
    """Convert binary xmrg file to ascii

    Parameters
    ----------
    path : str
            The path to the xmrg binary file
    filename : str
            The filename of the xmrg binary file

    Returns
    -------
    dict
        a dictionary containing the size, origin, fill value, and data
    """
    ascii_file = filename.split(".")[0]
    ascii_file = f"{path}/{ascii_file}"
    xmrg_file = f"{path}/{filename}"

    """
    The data format was derived from https://www.weather.gov/media/owp/oh/hrl/docs/xmrg.pdf,
    which has some information, but isn"t 100% correct for the NWM XMRG data.
    Also used the linux od tool to look at the binary and figure out the format.
    """

    # read xmrg binary file
    with open(xmrg_file, "rb") as f:
        # skip first byte
        f.seek(4, 0)
        x_orig = struct.unpack("<f", f.read(4))[0]
        y_orig = struct.unpack("<f", f.read(4))[0]
        x_size = struct.unpack("<i", f.read(4))[0]
        y_size = struct.unpack("<i", f.read(4))[0]
        f.seek(28, 0)
        scale_factor = struct.unpack("<i", f.read(4))[0]
        num_bytes = struct.unpack("<i", f.read(4))[0]
        cell_size = struct.unpack("<f", f.read(4))[0]
        fill_value = struct.unpack("<f", f.read(4))[0]

        # data is either a 16 bit integer or a 32 bit floating point
        # as shown in the num_bytes field
        if num_bytes == 4:
            asc = np.ndarray(shape=(y_size, x_size), dtype=np.float32)
        elif num_bytes == 2:
            asc = np.ndarray(shape=(y_size, x_size), dtype=np.int16)
        else:
            sys.exit("number of bytes for data is not equal to 2 or 4")

        # read columns of data for each row starting at byte 48
        # There is a 4 byte empty pad at the beginning and end of each column
        f.seek(48, 0)
        for i in range(0, y_size):
            f.read(4)
            for j in range(0, x_size):
                if num_bytes == 4:
                    asc[i, j] = struct.unpack("<f", f.read(4))[0]
                elif num_bytes == 2:
                    asc[i, j] = struct.unpack("<h", f.read(2))[0]
            f.read(4)

    output = {
        "ncols": x_size,
        "nrows": y_size,
        "xllcorner": x_orig,
        "yllcorner": y_orig,
        "cellsize": cell_size,
        "NODATA_value": fill_value,
    }

    asc_output = np.where(asc > fill_value, asc / scale_factor, np.nan)

    output["asc"] = asc_output
    return output


def ascii_to_raster(input_data: dict, path: str, filename: str) -> None:
    """Create raster from xmrg ascii data

    Parameters
    ----------
    input_data : dict
            The dict output from xmrg_to_ascii
    path : str
            The path to the xmrg binary file and where the raster will
            be saved
    filename : str
            The filename of the xmrg binary file

    """
    # create tif filename from xmrg filename
    output_file = filename.split(".")[0]
    output_file = f"{filename}.tif"

    # get xmrg data from dictionary
    data = input_data["asc"]
    ncols = input_data["ncols"]
    nrows = input_data["nrows"]
    xllcorner = input_data["xllcorner"]
    yllcorner = input_data["yllcorner"]

    # get x and y min/max points in the stereographic projection
    # https://www.weather.gov/owp/oh_hrl_distmodel_hrap
    xmn_ster = xllcorner * 4762.5 - 401 * 4762.5
    xmx_ster = (xllcorner + ncols) * 4762.5 - 401 * 4762.5
    ymn_ster = yllcorner * 4762.5 - 1601 * 4762.5
    ymx_ster = (yllcorner + nrows) * 4762.5 - 1601 * 4762.5

    print(f"ncols: {ncols}")
    print(f"nrows: {nrows}")
    print(f"stereographic x min: {xmn_ster}")
    print(f"stereographic x max: {xmx_ster}")
    print(f"stereographic y min: {ymn_ster}")
    print(f"stereographic x max: {ymx_ster}")

    # create raster
    x_coords = np.linspace(xmn_ster, xmx_ster, ncols)
    y_coords = np.linspace(ymn_ster, ymx_ster, nrows)
    da = xr.DataArray(
        data,
        coords={"y": y_coords, "x": x_coords},
        dims=("y", "x"),
    )

    # set CRS to stereographic projection
    # see https://www.weather.gov/owp/oh_hrl_distmodel_hrap
    crs = "+proj=stere +lat_0=90 +lat_ts=60 +lon_0=-105"
    da.rio.write_crs(crs, inplace=True)

    # reproject to Alaska Albers
    dst = da.rio.reproject("EPSG:3338", resampling=Resampling.nearest, nodata=np.nan)
    # write raster to tif file
    dst.rio.to_raster(f"{path}/{output_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A script to convert an XRMG file to a raster")

    parser.add_argument("--path", help="The path to the directory containing the XMRG file")
    parser.add_argument("--filename", help="The XMRG filename")

    args = parser.parse_args()
    asc_data = xmrgtoascii(
        path=args.path,
        filename=args.filename,
    )

    ascii_to_raster(
        asc_data,
        path=args.path,
        filename=args.filename,
    )
