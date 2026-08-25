import geopandas as gpd
import pandas as pd
from pyarrow import fs
from pyiceberg.catalog import Catalog
from pyiceberg.expressions import In
from tqdm import tqdm

from hydrofabric_builds.schemas.hydrofabric import HydrofabricDomains


def find_usbr_network_rows(
    hf_domain: str,
    usbr_file: str,
    catalog: Catalog,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Reads the catalog to determine which lake IDs are in the network table

    Parameters
    ----------
    hf_domain : str
        The domain of the hydrofabric to read
    usbr_file : str
        the string path to the USBR locations (generated from hydrofabric-builds/tools/builds/lakes/usbr/usbr_prep.ipynb)
    catalog : Catalog
        The pyiceberg catalog

    Returns
    -------
    tuple[pd.DataFrame, pd.DataFrame]
        the network table filtered based in lake IDs, the filtered usbr dataframe
    """
    usbr_df = gpd.read_parquet(usbr_file)
    valid_ids = usbr_df["id"].dropna().values.tolist()
    usbr_df_filtered = usbr_df[usbr_df["id"].isin(valid_ids)]

    network_filtered = (
        catalog.load_table(f"{hf_domain}.network").scan(row_filter=In("id", valid_ids)).to_pandas()
    )
    return network_filtered, usbr_df_filtered


def update_network_hydrolocations_table_usbr(
    hf_domain: str,
    network_filtered: pd.DataFrame,
    usbr_df: pd.DataFrame,
    catalog: Catalog,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Updates the network table to include USBR reservoirs where they already exist

    Parameters
    ----------
    hf_domain : str
        The domain of the hydrofabric to read
    network_filtered : pd.DataFrame
        the filtered network table
    usbr_df : pd.DataFrame
        the dataframe containing usbr lakes
    catalog : Catalog
        The pyiceberg catalog

    Returns
    -------
    tuple[pd.DataFrame, pd.DataFrame]
        returns the new network table and hydrolocations
    """
    df_lakes = catalog.load_table(f"{hf_domain}.lakes").scan().to_pandas()
    df_network = catalog.load_table(f"{hf_domain}.network").scan().to_pandas()
    df_hydrolocations = catalog.load_table(f"{hf_domain}.hydrolocations").scan().to_pandas()
    print("Loaded icefabric tables")

    existing_pois = network_filtered[~network_filtered["poi_id"].isna()]["poi_id"].unique().astype(int)
    filtered_lakes = df_lakes[df_lakes["poi_id"].isin(existing_pois)]
    for _, row in tqdm(
        filtered_lakes.iterrows(),
        total=len(filtered_lakes),
        desc="Creating new network table connections for existing USBR lakes",
    ):
        nidx = df_network.index[-1] + 1  # getting a fresh index for the new entry by appending from the end
        hydro_idx = (
            df_hydrolocations.index[-1] + 1
        )  # getting a fresh index for the new entry by appending from the end
        network_row = (
            network_filtered[network_filtered["poi_id"] == row.poi_id]
            .drop_duplicates(keep="first", subset=["poi_id"])
            .copy()
        )
        hydrolocation = (
            df_hydrolocations[df_hydrolocations["poi_id"] == row.poi_id]
            .drop_duplicates(keep="first", subset=["poi_id"])
            .copy()
        )
        usbr_row = usbr_df[usbr_df["id"] == network_row["id"].values[0]]
        try:
            location_id = usbr_row["location_id"].item()
        except ValueError:
            print("Multiple locations found for one divide. Using first location.")
            location_id = usbr_row["location_id"].iloc[0].item()
        network_row["hl_uri"] = f"usbr-{location_id}"
        network_row.index = [nidx]
        hydrolocation["hl_reference"] = "usbr"
        hydrolocation["hl_uri"] = f"usbr-{location_id}"
        hydrolocation["hl_link"] = str(location_id)
        hydrolocation["hl_source"] = "USBR"
        hydrolocation.index = [hydro_idx]
        df_network = pd.concat([df_network, network_row])
        df_hydrolocations = pd.concat([df_hydrolocations, hydrolocation])

    return df_network, df_hydrolocations


def update_network_poi(
    catalog: Catalog, hf_domain: str, poi_path: str, s3: fs.S3FileSystem | None = None
) -> pd.DataFrame:
    """Update a Hydrofabric network table with new POIs from a POI table

    Parameters
    ----------
    catalog : Catalog
        Pyicbeger catalog to read from
    hf_domain : str
        Hydrofabric Domain to use
    poi_path : _type_
        Path to new POI parquet
    s3 : fs.S3FileSystem | None, optional
        An s3 filesystem if reading and writing to s3, by default None

    Returns
    -------
    pd.DataFrame
        Updated network table with new POIs
    """
    print("Updating network table")
    df_network = catalog.load_table(f"{HydrofabricDomains[hf_domain].value}.network").scan().to_pandas()

    df_poi = pd.read_parquet(poi_path, filesystem=s3) if s3 else pd.read_parquet(poi_path)

    # merge on nex_id only
    df_network_merge = df_network.merge(
        df_poi[["poi_id", "nex_id"]], how="left", left_on=["toid"], right_on=["nex_id"]
    )

    # replace old POI with new POI
    df_network_merge["poi_id_x"] = df_network_merge["poi_id_y"]
    df_network_merge = df_network_merge.drop(columns=["poi_id_y", "nex_id"]).rename(
        columns={"poi_id_x": "poi_id"}
    )

    return df_network_merge
