"""
A script to build divide_attributes, POIs, and hydrolocations from a glacier polygon file

Sample call (glue):
python tools/builds/glaciers/glacier_hydrofabric.py --hf_domain ak --catalog glue --s3_files --glacier_path edfs-data/glaciers/glims_20250624.parquet --working_dir edfs-data/glaciers/test

Sample call (local) - Fill in local paths:
python tools/builds/glaciers/glacier_hydrofabric.py --hf_domain ak --catalog sql --glacier_path /{your_directory}/glims_20250624.parquet --working_dir /{your_directory}/test
"""

import argparse
from typing import Literal

from hydrofabric_builds.builds.glaciers import (
    calculate_glacier_percent,
    create_hydrolocations_ak,
    create_hydrolocations_conus,
    create_pois,
)
from hydrofabric_builds.builds.network import update_network_poi
from hydrofabric_builds.helpers.io import s3_fs, setup_glue_catalog, setup_sql_catalog


def build_glaciers(
    hf_domain: Literal["AK", "CONUS"],
    catalog_type: Literal["glue", "sql"],
    s3_files: bool,
    glacier_path: str,
    working_dir: str,
) -> None:
    """Function to call glacier:hydrofabric mapping build process

    Parameters
    ----------
    hf_domain : Literal["AK", "CONUS"]
        Availabile hydrofabric domains for glaciers
    catalog_type : Literal["glue", "sql"]
        Iceberg catalog source - glue or sql (local)
    s3_files : bool
        True to retrieve and save files on s3, False for local
    glacier_path : str
        Path to input glacier polygon gpkg
    working_dir : str
        Working directory for all outputs

    Raises
    ------
    ValueError
        If hydrofabric domain is not valid
    """
    domain = hf_domain.upper()
    if domain not in ["AK", "CONUS"]:
        raise ValueError("Valid domains are 'AK' for Alaska and 'CONUS' for CONUS Hydrofabric")

    divatt_path = f"{working_dir}/divide_attributes_glacier_{domain}.parquet"
    poi_path = f"{working_dir}/poi_glacier_{domain}.parquet"
    hydrolocations_path = f"{working_dir}/hydrolocations_glacier_{domain}.parquet"
    network_path = f"{working_dir}/network_glacier_{domain}.parquet"

    catalog = setup_glue_catalog() if catalog_type == "glue" else setup_sql_catalog()

    s3 = s3_fs() if s3_files else None

    calculate_glacier_percent(
        hf_domain=domain,  # type: ignore[arg-type]
        glacier_path=glacier_path,
        output_divatt_path=divatt_path,
        catalog=catalog,
        s3=s3,
    )

    create_pois(
        hf_domain=domain,  # type: ignore[arg-type]
        divatt_path=divatt_path,
        output_poi_path=poi_path,
        catalog=catalog,
        s3=s3,
    )

    if domain == "AK":
        create_hydrolocations_ak(
            hf_domain="AK",
            divatt_path=divatt_path,
            poi_path=poi_path,
            output_hl_path=hydrolocations_path,
            catalog=catalog,
            s3=s3,
        )

    if domain == "CONUS":
        create_hydrolocations_conus(
            hf_domain="CONUS",
            divatt_path=divatt_path,
            poi_path=poi_path,
            output_hl_path=hydrolocations_path,
            catalog=catalog,
            s3=s3,
        )

    df_network = update_network_poi(catalog=catalog, hf_domain=domain, poi_path=poi_path, s3=s3)
    (df_network.to_parquet(network_path, filesystem=s3) if s3_files else df_network.to_parquet(network_path))
    print("Saved network table")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="A script to build divide_attributes, POIs, and hydrolocations from a glacier polygon file"
    )
    parser.add_argument("--hf_domain")
    parser.add_argument("--catalog", required=True, type=str, help="Use 'glue' or 'sql' catalog")
    parser.add_argument(
        "--s3_files",
        action="store_true",
        help="Add argument if input and output files reside on s3",
    )
    parser.add_argument(
        "--glacier_path",
        required=True,
        type=str,
        help="Path to glacier parquet on s3 or local. For s3, use in form of 'bucket/prefix.parquet'",
    )
    parser.add_argument(
        "--working_dir",
        required=True,
        type=str,
        help="Working directory to save files. For s3, use in form of 'bucket/folder'",
    )

    args = parser.parse_args()

    build_glaciers(
        hf_domain=args.hf_domain,
        catalog_type=args.catalog,
        s3_files=args.s3_files,
        glacier_path=args.glacier_path,
        working_dir=args.working_dir,
    )
