"""
Build the lakes layer for CONUS

python lakes_hydrofabric.py --lakeparm_file --ana_res_file --ext_res_file --med_res_file --short_res_file --hffile --output_path --domain

python lakes_hydrofabric.py --lakeparm_file /home/daniel.cumpton/lakes/LAKEPARM_CONUS_216.nc --ana_res_file /home/daniel.cumpton/lakes/reservoir_index_AnA_309.nc --ext_res_file /home/daniel.cumpton/lakes/reservoir_index_Extended_AnA.nc --med_res_file /home/daniel.cumpton/lakes/reservoir_index_Medium_Range.nc --short_res_file /home/daniel.cumpton/lakes/reservoir_index_Short_Range.nc --hffile /home/daniel.cumpton/Hydrofabric/data/hydrofabric/v2.2/nextgen/CONUS/conus_nextgen.gpkg --output_path /home/daniel.cumpton/lakes --domain CONUS

"""

import argparse

from hydrofabric_builds.builds.lakes import build_lakes

if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--lakeparm_file",
        required=True,
        type=str,
        help="full path and filename of lakeparms netcdf file, e.g, LAKEPARM_CONUS_216.nc",
    )

    parser.add_argument(
        "--ana_res_file",
        required=True,
        type=str,
        help="full path and filename of ana res netcdf file, e.g., reservoir_index_AnA_309.nc",
    )

    parser.add_argument(
        "--ext_res_file",
        required=True,
        type=str,
        help="full path and filename of extended res netcdf file, e.g., reservoir_index_Extended_AnA.nc",
    )

    parser.add_argument(
        "--med_res_file",
        required=True,
        type=str,
        help="full path and filename of medium range res netcdf file, e.g., reservoir_index_Medium_Range.nc",
    )

    parser.add_argument(
        "--short_res_file",
        required=True,
        type=str,
        help="full path and filename of medium range res netcdf file, e.g., reservoir_index_Short_Range.nc",
    )

    parser.add_argument(
        "--hffile", required=True, type=str, help="full path and filename of hydrofabric geopackage"
    )

    parser.add_argument(
        "--output_path", required=True, type=str, help="full path to directory where outputs will be saved"
    )

    parser.add_argument(
        "--domain", required=True, type=str, help="hydrofabric domain (CONUS only at this time)"
    )

    args = parser.parse_args()

    lakeparm_file = args.lakeparm_file
    ana_res_file = args.ana_res_file
    ext_res_file = args.ext_res_file
    med_res_file = args.med_res_file
    short_res_file = args.short_res_file
    hf_file = args.hffile
    output_path = args.output_path
    domain = args.domain

    build_lakes(
        lakeparm_file, ana_res_file, ext_res_file, med_res_file, short_res_file, hf_file, output_path, domain
    )
