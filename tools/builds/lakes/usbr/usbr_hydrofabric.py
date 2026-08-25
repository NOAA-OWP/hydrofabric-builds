"""A script to build lakes, network table, POIs, and hydrolocations from a lakes point file

example usage:  python tools/builds/lakes/usbr/usbr_hydrofabric.py --catalog sql --usbr-file <CWD>/hydrofabric-builds/data/usbr/reservoir_matches.parquet --working-dir <CWD>/hydrofabric-builds/data/usbr
"""

import argparse
from pathlib import Path
from typing import Literal

from hydrofabric_builds.builds.network import find_usbr_network_rows, update_network_hydrolocations_table_usbr
from hydrofabric_builds.helpers.io import setup_glue_catalog, setup_sql_catalog


def build_usbr_lakes(
    catalog_type: Literal["glue", "sql"],
    usbr_file: str,
    working_dir: Path,
) -> None:
    """Builds the USBR lakes into the network table

    Parameters
    ----------
    catalog_type: Literal["glue", "sql"]
        The pyiceberg catalog type
    usbr_file: str,
        the string path to the USBR locations (generated from hydrofabric-builds/tools/builds/lakes/usbr/usbr_prep.ipynb)
    working_dir: Path
        The working dir to save output parquet files
    """
    hf_domain = "conus"  # only supporting CONUS for the time being
    catalog = setup_glue_catalog() if catalog_type == "glue" else setup_sql_catalog()
    network_path = working_dir / "usbr_network.parquet"
    hydrolocations_path = working_dir / "usbr_hydrolocations.parquet"

    network_filtered, usbr_df_filtered = find_usbr_network_rows(
        hf_domain=f"{hf_domain}_hf",
        usbr_file=usbr_file,
        catalog=catalog,
    )

    df_network, df_hydrolocations = update_network_hydrolocations_table_usbr(
        hf_domain=f"{hf_domain}_hf",
        catalog=catalog,
        network_filtered=network_filtered,
        usbr_df=usbr_df_filtered,
    )

    df_network.to_parquet(network_path)
    df_hydrolocations.to_parquet(hydrolocations_path)
    print("Saved network and hydrolocations tables with updated USBR references")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="A script to build the network table, and hydrolocations, for USBR reservoirs that are already in the HF"
    )
    parser.add_argument(
        "--catalog",
        required=False,
        choices=["sql", "glue"],
        type=str,
        help="Use 'glue' or 'sql' catalog",
        default="sql",
    )
    parser.add_argument(
        "--usbr-file",
        required=True,
        type=str,
        help="Path to the usbr reservoirs parquet on local disk.",
    )
    parser.add_argument(
        "--working-dir",
        required=False,
        type=Path,
        default=Path.cwd(),
        help="Working directory to save files.",
    )

    args = parser.parse_args()

    build_usbr_lakes(
        catalog_type=args.catalog,
        usbr_file=args.usbr_file,
        working_dir=args.working_dir,
    )
