from typing import Literal

import geopandas as gpd
import numpy as np
import pandas as pd
from pyarrow import fs
from pyiceberg.catalog import Catalog

from hydrofabric_builds.schemas import HydrofabricCRS, HydrofabricDomains


def calculate_glacier_percent(
    hf_domain: Literal["CONUS", "AK"],
    glacier_path: str,
    output_divatt_path: str,
    catalog: Catalog,
    s3: fs.S3FileSystem | None = None,
) -> None:
    """Function to join glaciers to divide attributes and calculate percent glacier

    Parameters
    ----------
    hf_domain : HydrofabricDomains
        Hydrofabric domain
    glacier_path : str
        Path for glacier parquet.
        For s3, do not incldue s3://. ex. "edfs-data/glaciers/glims_20250624.parquet"
        For local, provide full file path.
    output_divatt_path : str
        Path to output the divide attributes table including glacier_percent field
        For s3, do not incldue s3://. ex. "edfs-data/glaciers/divide_attributes_glaciers.parquet"
        For local, provide full file path.
    catalog : Catalog
        The instantiated pyiceberg catalog
    s3 : fs.S3 | None
        A pyarrow s3 file system to optionally read and write parquets. If None, uses local
    """
    # hydrofabric imports and setup
    df_div = catalog.load_table(f"{HydrofabricDomains[hf_domain].value}.divides").scan().to_pandas()
    df_divatt = (
        catalog.load_table(f"{HydrofabricDomains[hf_domain].value}.divide-attributes").scan().to_pandas()
    )

    gdf_div = gpd.GeoDataFrame(
        df_div, geometry=gpd.GeoSeries.from_wkb(df_div["geometry"]), crs=HydrofabricCRS[hf_domain].value
    )
    print(f"Imported {hf_domain} tables from catalog")

    # glacier imports and setup
    gdf_gl = gpd.read_parquet(glacier_path, filesystem=s3) if s3 else gpd.read_parquet(glacier_path)
    gdf_gl["geometry"] = gdf_gl["geometry"].to_crs(HydrofabricCRS[hf_domain].value)
    print(f"Read glacier parquet from {glacier_path}")

    # get hf divide area
    gdf_div["area_hf"] = gdf_div.area

    # intersect divides and glacier polygons
    print("Intersecting glaciers and divides - this may take a while")
    gdf_int = gdf_div.overlay(gdf_gl, how="intersection")
    print("Finished intersecting glaciers and divides")

    # dissolve intersection by div_id so there is one glacier polygon per divide
    # reset index to keep `divide_id` column
    print("Dissolving glacier and divides - this may take a while")
    gdf_int_dissolve = gdf_int.dissolve("divide_id")
    gdf_int_dissolve = gdf_int_dissolve.reset_index(drop=False)

    # drop all columns except geometry and divide_id
    # rename secondary geom column
    gdf_int_dissolve = gdf_int_dissolve[["geometry", "divide_id"]]
    gdf_int_dissolve["area_gl"] = gdf_int_dissolve.area
    gdf_int_dissolve = gdf_int_dissolve.rename(columns={"geometry": "geom_int"})
    print("Finished dissolving glaciers and divides")

    # merge divides with dissolved
    gdf_merge = gdf_div[["geometry", "divide_id", "area_hf"]].merge(
        gdf_int_dissolve, on="divide_id", how="inner"
    )

    # calculate % glacier
    gdf_merge["glacier_percent"] = (gdf_merge["area_gl"] / gdf_merge["area_hf"] * 100).round(2)

    # left join glacier_percent to div_attributes
    df_divatt = df_divatt.merge(gdf_merge[["divide_id", "glacier_percent"]], on="divide_id", how="left")

    # fill na
    df_divatt["glacier_percent"] = df_divatt["glacier_percent"].fillna(0)
    print("Finished calculating glacier percent")

    # save out new divide_attributes
    if s3:
        print("Saving divide attributes to s3")
        df_divatt.to_parquet(output_divatt_path, filesystem=s3)
        print("Saved to s3")
    else:
        df_divatt.to_parquet(output_divatt_path)
        print(f"Saved locally to {output_divatt_path}")


def create_pois(
    hf_domain: Literal["CONUS", "AK"],
    divatt_path: str,
    output_poi_path: str,
    catalog: Catalog,
    s3: fs.S3FileSystem | None = None,
) -> None:
    """Create new POIs from glacier locations determined by divide attributes layer glacier_percent

    Parameters
    ----------
    hf_domain : Literal["CONUS", "AK"]
        Hydrofabric domain supporting glaciers
    divatt_path : str
        Path to the input divide attributes table including glacier_percent field
        For s3, do not include s3://. ex. "edfs-data/glaciers/divide_attributes_glaciers.parquet"
        For local, provide full file path.
    output_poi_path : str
        Path to output the new POI table
        For s3, do not incldue s3://. ex. "edfs-data/glaciers/pois.parquet"
        For local, provide full file path.
    catalog : Catalog
        The instantiated pyiceberg catalog
        s3 : fs.S3 | None
        A pyarrow s3 file system to optionally read and write parquets. If None, uses local
    """
    # hydrofabric imports and setup
    df_network = catalog.load_table(f"{HydrofabricDomains[hf_domain].value}.network").scan().to_pandas()
    df_poi = catalog.load_table(f"{HydrofabricDomains[hf_domain].value}.pois").scan().to_pandas()
    print("Loaded icefabric tables")

    # set up new indices - FID is the index and starts with 1 in qgis
    df_poi.index = df_poi.index + 1
    df_poi.index.name = "fid"

    # previously created; includes glacier %
    df_div_atts = pd.read_parquet(divatt_path, filesystem=s3) if s3 else pd.read_parquet(divatt_path)
    df_gl = df_div_atts.loc[df_div_atts["glacier_percent"] > 0, "divide_id"].copy()

    # get divides from network in glacier list
    df_network_glac = df_network.loc[df_network["divide_id"].isin(df_gl)].copy()

    # get locations with no existing POI
    df_new_poi = df_network_glac.loc[df_network_glac["poi_id"].isnull(), ["id", "toid", "vpuid"]].copy()
    # some new POI (from div id) are dupes because of hf_id (nhd)
    df_new_poi = df_new_poi.drop_duplicates()

    # get max index and poi to start next bit of table
    max_poi = df_poi["poi_id"].max()
    new_pois = np.arange(max_poi + 1, max_poi + len(df_new_poi) + 1, 1)
    max_ind = df_poi.index.max()
    new_ind = np.arange(max_ind + 1, max_ind + len(df_new_poi) + 1, 1)

    # build new POI table with new index
    df_new_poi.index = new_ind
    df_new_poi = df_new_poi.rename(columns={"toid": "nex_id"})
    df_new_poi.insert(0, "poi_id", new_pois)

    # concat old and new POI table
    df_appended_poi = pd.concat([df_poi, df_new_poi], axis=0)  # concat old and new
    df_appended_poi.index.name = "fid"

    # there are some dupelicate POIs with nex_id and id
    df_appended_poi = df_appended_poi.drop_duplicates(
        subset=["nex_id", "id"], keep="first", ignore_index=False
    )
    print("Finished building new POI table")

    if s3:
        print("Saving POI to s3")
        df_appended_poi.to_parquet(output_poi_path, filesystem=s3)
        print("Saved to s3")
    else:
        df_appended_poi.to_parquet(
            output_poi_path,
        )
        print(f"Saved locally to {output_poi_path}")


def create_hydrolocations_ak(
    hf_domain: Literal["CONUS", "AK"],
    divatt_path: str,
    poi_path: str,
    output_hl_path: str,
    catalog: Catalog,
    s3: fs.S3FileSystem | None = None,
) -> None:
    """Create Hydrolocations for Alaska domain based on new POIs

    Parameters
    ----------
    hf_domain : Literal["CONUS", "AK"]
        Hydrofabric domain
    divatt_path : str
        Path to input the divide attributes table including glacier_percent field
        For s3, do not incldue s3://. ex. "edfs-data/glaciers/divide_attributes_glaciers.parquet"
        For local, provide full file path.
    poi_path : str
        Path to input the new POI table
        For s3, do not incldue s3://. ex. "edfs-data/glaciers/pois.parquet"
        For local, provide full file path.
    output_hl_path : str
        Path to output the new glacier hydrolocations table
        For s3, do not incldue s3://. ex. "edfs-data/glaciers/hydrolocations_glaciers.parquet"
        For local, provide full file path.
    catalog : Catalog
        The instantiated pyiceberg catalog
        s3 : fs.S3 | None
        A pyarrow s3 file system to optionally read and write parquets. If None, uses local
    """
    # hydrofabric imports and setup
    df_network = catalog.load_table(f"{HydrofabricDomains[hf_domain].value}.network").scan().to_pandas()
    df_hl = catalog.load_table(f"{HydrofabricDomains[hf_domain].value}.hydrolocations").scan().to_pandas()
    print("Loaded icefabric tables")

    # set up new indices - FID is the index and starts with 1 in qgis
    df_hl.index = df_hl.index + 1
    df_hl.index.name = "fid"

    # previously created div atts; includes glacier %
    df_div_atts = pd.read_parquet(divatt_path, filesystem=s3) if s3 else pd.read_parquet(divatt_path)
    df_gl = df_div_atts.loc[df_div_atts["glacier_percent"] > 0, "divide_id"].copy()

    # get divides from network in glacier list
    df_network_glac = df_network.loc[df_network["divide_id"].isin(df_gl)].copy()

    # previously created POIs
    df_new_pois = pd.read_parquet(poi_path, filesystem=s3) if s3 else pd.read_parquet(poi_path)

    # make new HLs from glacier pois
    df_network_glac_rename = df_network_glac.rename(columns={"toid": "nex_id"})
    df_new_hl = df_new_pois.merge(
        df_network_glac_rename[["nex_id", "id"]],
        on=["nex_id", "id"],
        how="inner",
    )
    df_new_hl = df_new_hl.drop(columns=["vpuid"])

    # drop dupes from overlapping hf_id
    df_new_hl = df_new_hl.drop_duplicates()

    # make new columns, drop old vpuid column in wrong place, and merge
    df_new_cols = pd.DataFrame(
        index=df_new_hl.index,
        columns=["hl_link", "hl_reference", "hl_source", "hf_id", "vpuid"],
    )
    df_new_cols["hl_reference"] = "glacier"
    df_new_cols["hl_link"] = None
    df_new_cols["hl_source"] = "glims"
    df_new_cols["vpuid"] = "ak"
    df_new_hl = df_new_hl.merge(df_new_cols, left_index=True, right_index=True)

    # get new fid where previous leaves off and set
    max_fid = df_hl.index.max()
    new_fid = np.arange(max_fid + 1, max_fid + len(df_new_hl) + 1)
    df_new_hl.index = new_fid
    df_new_hl.index.name = "fid"

    df_appended_hl = pd.concat([df_hl, df_new_hl], axis=0, ignore_index=False)
    print("Finished creating new hydrolocations")

    if s3:
        print("Saving hydrolocationsto s3")
        df_appended_hl.to_parquet(output_hl_path, filesystem=s3)
        print("Saved to s3")
    else:
        df_appended_hl.to_parquet(
            output_hl_path,
        )
        print(f"Saved locally to {output_hl_path}")


def create_hydrolocations_conus(
    hf_domain: Literal["CONUS", "AK"],
    divatt_path: str,
    poi_path: str,
    output_hl_path: str,
    catalog: Catalog,
    s3: fs.S3FileSystem | None = None,
) -> None:
    """Create Hydrolocations for Alaska domain based on new POIs

    Parameters
    ----------
    hf_domain : Literal["CONUS", "AK"]
        Hydrofabric domain
    divatt_path : str
        Path to input the divide attributes table including glacier_percent field
        For s3, do not incldue s3://. ex. "edfs-data/glaciers/divide_attributes_glaciers.parquet"
        For local, provide full file path.
    poi_path : str
        Path to input the new POI table
        For s3, do not incldue s3://. ex. "edfs-data/glaciers/pois.parquet"
        For local, provide full file path.
    output_hl_path : str
        Path to output the new glacier hydrolocations table
        For s3, do not incldue s3://. ex. "edfs-data/glaciers/hydrolocations_glaciers.parquet"
        For local, provide full file path.
    catalog : Catalog
        The instantiated pyiceberg catalog
    s3 : fs.S3 | None
        A pyarrow s3 file system to optionally read and write parquets. If None, uses local
    """
    # hydrofabric imports and setup
    df_network = catalog.load_table(f"{HydrofabricDomains[hf_domain].value}.network").scan().to_pandas()
    df_hl = catalog.load_table(f"{HydrofabricDomains[hf_domain].value}.hydrolocations").scan().to_pandas()
    df_nex = catalog.load_table("conus_hf.nexus").scan().to_pandas()
    gdf_nex = gpd.GeoDataFrame(
        df_nex,
        geometry=gpd.GeoSeries.from_wkb(df_nex["geometry"], crs=HydrofabricCRS[hf_domain].value),
    )
    print("Loaded icefabric tables")

    # set up new indices - FID is the index and starts with 1 in qgis
    df_hl.index = df_hl.index + 1
    df_hl.index.name = "fid"

    # previously created div atts; includes glacier %
    df_div_atts = pd.read_parquet(divatt_path, filesystem=s3) if s3 else pd.read_parquet(divatt_path)
    df_gl = df_div_atts.loc[df_div_atts["glacier_percent"] > 0, "divide_id"].copy()

    # get divides from network in glacier list
    df_network_glac = df_network.loc[df_network["divide_id"].isin(df_gl)].copy()

    # previously created POIs
    df_new_pois = pd.read_parquet(poi_path, filesystem=s3) if s3 else pd.read_parquet(poi_path)

    # make new HLs from glacier pois
    df_network_glac_rename = df_network_glac.rename(columns={"toid": "nex_id"})
    df_new_hl = df_new_pois.merge(
        df_network_glac_rename[["nex_id", "id", "vpuid"]],
        on=["nex_id", "id", "vpuid"],
        how="inner",
    )

    # drop dupes from overlapping hf_id
    df_new_hl = df_new_hl.drop_duplicates()

    # make new columns and merge
    df_new_cols = pd.DataFrame(
        index=df_new_hl.index,
        columns=[
            "hf_id",
            "hl_link",
            "hl_reference",
            "hl_uri",
            "hl_source",
            "hl_x",
            "hl_y",
        ],
    )
    df_new_cols["hf_id"] = None
    df_new_cols["hl_reference"] = "glacier"
    df_new_cols["hl_source"] = "glims"
    df_new_hl = df_new_hl.merge(df_new_cols, left_index=True, right_index=True)

    # make the hl_link (unique_id) be g(lacier)-nex_id
    df_new_hl["hl_link"] = "g" + df_new_hl["nex_id"]
    df_new_hl["hl_uri"] = df_new_hl["hl_reference"] + "-" + df_new_hl["hl_link"]

    # merge in nexus to get geometry for hl_x and hl_y
    df_new_hl = df_new_hl.merge(gdf_nex[["geometry", "id"]], left_on="nex_id", right_on="id")
    gdf_new_hl = gpd.GeoDataFrame(df_new_hl, geometry="geometry", crs=HydrofabricCRS[hf_domain].value)
    gdf_new_hl["hl_x"] = gdf_new_hl.geometry.x
    gdf_new_hl["hl_y"] = gdf_new_hl.geometry.y

    # create a df of the new hl points in correct attribute order
    df_final_new_hl = (
        gdf_new_hl[
            [
                "poi_id",
                "id_x",
                "nex_id",
                "hf_id",
                "hl_link",
                "hl_reference",
                "hl_uri",
                "hl_source",
                "hl_x",
                "hl_y",
                "vpuid",
            ]
        ]
        .copy()
        .rename(columns={"id_x": "id"})
    )

    # get new fid
    max_fid = df_hl.index.max()
    new_fid = np.arange(max_fid + 1, max_fid + len(df_new_hl) + 1)
    df_final_new_hl.index = new_fid
    df_final_new_hl.index.name = "fid"

    # append the new hls and originals
    df_appended_hl = pd.concat([df_hl, df_final_new_hl], axis=0, ignore_index=False)
    print("Finished creating new hydrolocations")

    if s3:
        print("Saving hydrolocations to s3")
        df_appended_hl.to_parquet(output_hl_path, filesystem=s3)
        print("Saved to s3")
    else:
        df_appended_hl.to_parquet(
            output_hl_path,
        )
        print(f"Saved locally to {output_hl_path}")
