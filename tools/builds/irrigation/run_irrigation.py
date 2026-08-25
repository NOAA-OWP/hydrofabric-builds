"""A script to download the USDA Cropland Data Layer and convert to gridded irrigation input for CNN"""

import argparse
from pathlib import Path

from hydrofabric_builds.builds.irrigation import classify_irrigation, regrid_irrigation


def run_irrigation(
    wd: Path,
    min_yr: int,
    max_yr: int,
    download: bool,
    unzip: bool,
    classify: bool,
    aggregate: bool,
    grid: Path | None = None,
    grid_year: str | int | None = None,
) -> None:
    """Run the full irrigation pipeline.

    Here, irrigation is considered a list of crops that may be irrigated. This build downloads the USDA Cropland Data Layer for requested years, converts to a mask at native resolution (30 meter),
    and optionally regrids to a coarser grid and resolution where the new raster represents percent irrigated.

    Parameters
    ----------
    wd : Path
        Working directory for data files
    min_yr : str | int
        First CDL year to process. If you only want one year, set max_yr to equal min_yr
    max_yr : str | int
        Last year to process. If you only want one year, set max_yr to equal min_yr
    download : bool
        Optional flag. Set True to skip downloading files.
    unzip : bool
        Optional flag. Set True to skip unzipping files.
    classify : bool
        Optional flag. Set True to skip  skip classifying files.
    aggregate : bool
        Optional flag. Set True to skip skip aggregating files.
    grid : Path | None, optional
        Optional grid/raster to resample, align, and clip outputs to. Resampling will be percent of
        new resolution irrigated, by default None
    grid_year : str | int | None, optional
        Optionally specify a single year to regrid. Will ignore min/max year arguments. "20xx_20xx"
        for temporally aggregated layer is accepted, by default None
    """
    classify_irrigation(
        min_yr=min_yr,
        max_yr=max_yr,
        download=download,
        unzip=unzip,
        classify=classify,
        aggregate=aggregate,
        wd=wd,
    )

    # regrid if a grid was input
    if grid:
        # if a single year was input, only regrid this, else regrid min->max and temporally aggregated
        if grid_year:
            yrs = [grid_year]
        else:
            yrs = [min_yr] if min_yr == max_yr else list(range(min_yr, max_yr + 1)) + [f"{min_yr}_{max_yr}"]

        regrid_irrigation(wd=wd, grid_path=grid, regrid_list=[str(yr) for yr in yrs])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="A script to convert Cropland Data Layer crops into a gridded 'irrigation' mask for use in f1 Trainer"
    )
    parser.add_argument(
        "-w", "--working_dir", required=True, type=str, help="Working directory for data files"
    )
    parser.add_argument("--min_yr", required=True, type=int, help="First CDL year to process")
    parser.add_argument(
        "--max_yr",
        required=True,
        type=int,
        help="Last year to process. If you only want one year, set max_yr to equal min_yr",
    )
    parser.add_argument(
        "-g", "--grid", type=str, help="Optional grid to resample, align, and clip outputs to"
    )
    parser.add_argument(
        "--grid_yr", type=str, help="Optionally specify a single year to regrid. Will ignore min/max year"
    )
    parser.add_argument(
        "--no_download",
        action="store_true",
        help="Optional flag. Incldue argument to skip downloading files.",
    )
    parser.add_argument(
        "--no_unzip", action="store_true", help="Optional flag. Incldue argument to skip unzipping files."
    )
    parser.add_argument(
        "--no_classify", action="store_true", help="Optional flag. Incldue argumentto skip classifying files."
    )
    parser.add_argument(
        "--no_aggregate",
        action="store_true",
        help="Optional flag. Incldue argument to skip aggregating files.",
    )

    args = parser.parse_args()

    wd = Path(args.working_dir)
    download = False if args.no_download else True
    unzip = False if args.no_unzip else True
    classify = False if args.no_classify else True
    aggregate = False if args.no_aggregate else True
    grid = Path(args.grid) if args.grid else None
    grid_yr = args.grid_yr if args.grid_yr else None

    run_irrigation(
        wd=wd,
        min_yr=int(args.min_yr),
        max_yr=int(args.max_yr),
        grid=args.grid,
        download=download,
        unzip=unzip,
        classify=classify,
        aggregate=aggregate,
        grid_year=grid_yr,
    )
