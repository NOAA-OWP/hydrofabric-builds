from pathlib import Path

import geopandas as gpd
import pandas as pd
import xarray as xr
from pyproj import Transformer

from hydrofabric_builds.schemas.hydrofabric import HydrofabricCRS, HydrofabricDomainsGPKG


def build_lakes(
    lakeparm_file: str,
    ana_res_file: str,
    ext_res_file: str,
    med_res_file: str,
    short_res_file: str,
    hf_file: str,
    output_path: str,
    domain: str,
) -> None:
    """Builds the hydrofabric lakes layer

    Parameters
    ----------
    lakeparm_file : str
        full path and filename of lakeparms netcdf file, e.g, LAKEPARM_CONUS_216.nc
    ana_res_file : str
        full path and filename of ana res netcdf file, e.g., reservoir_index_AnA_309.nc
    ext_res_file : str
        full path and filename of extended res netcdf file, e.g., reservoir_index_Extended_AnA.nc
    med_res_file : str
        full path and filename of medium range res netcdf file, e.g., reservoir_index_Medium_Range.nc
    short_res_file : str
        full path and filename of short range res netcdf file, e.g., reservoir_index_Short_Range.nc
    hf_file : str
        full path and filename of hydrofabric geopackage
    output_path : str
        full path to directory where outputs will be saved
    domain : str
        domain to be created (CONUS, AK, GL, HI, PRVI)
    """
    # Set domain and crs
    domain = HydrofabricDomainsGPKG[domain].value
    domain_crs = HydrofabricCRS[domain].value

    # read lakesparm file into dataframe
    lakes = xr.open_dataset(lakeparm_file)
    lakes = lakes.to_dataframe()

    # Read data from reservoir files
    ana = xr.open_dataset(ana_res_file)
    ana_featureid = pd.DataFrame({"lake_id": ana["lake_id"], "reservoir_type": ana["reservoir_type"]})
    ana_usgs_comid = pd.DataFrame({"usgs_lake_id": ana["usgs_lake_id"], "usgs_gage_id": ana["usgs_gage_id"]})
    ana_usace_comid = pd.DataFrame(
        {"usace_lake_id": ana["usace_lake_id"], "usace_gage_id": ana["usace_gage_id"]}
    )
    ana_rfc_comid = pd.DataFrame({"rfc_lake_id": ana["rfc_lake_id"], "rfc_gage_id": ana["rfc_gage_id"]})
    ana_usgs_comid["usgs_gage_id"] = ana_usgs_comid["usgs_gage_id"].str.decode("utf-8")
    ana_usace_comid["usace_gage_id"] = ana_usace_comid["usace_gage_id"].str.decode("utf-8")
    ana_rfc_comid["rfc_gage_id"] = ana_rfc_comid["rfc_gage_id"].str.decode("utf-8")

    ext = xr.open_dataset(ext_res_file)
    ext = pd.DataFrame({"lake_id": ext["lake_id"], "reservoir_type": ext["reservoir_type"]})

    med = xr.open_dataset(med_res_file)
    med = pd.DataFrame({"lake_id": med["lake_id"], "reservoir_type": med["reservoir_type"]})

    short = xr.open_dataset(short_res_file)
    short = pd.DataFrame({"lake_id": short["lake_id"], "reservoir_type": short["reservoir_type"]})

    lake_ids = lakes["lake_id"].to_list()

    # create reservoir layer
    # get list of lakes in reserviors table where reservoir type > 1.
    ana_filtered = ana_featureid[ana_featureid["reservoir_type"] > 1]
    ana_res = ana_filtered["lake_id"].to_list()

    ext_filtered = ext[ext["reservoir_type"] > 1]
    ext_res = ext_filtered["lake_id"].to_list()

    med_filtered = med[med["reservoir_type"] > 1]
    med_res = med_filtered["lake_id"].to_list()

    short_filtered = short[short["reservoir_type"] > 1]
    short_res = short_filtered["lake_id"].to_list()

    # read hydrolocations layer from hydrofabric
    hl = gpd.read_file(hf_file, layer="hydrolocations")

    # create empty lists for reservoir table rows and for storing the res id cooresponding to a lake id
    res_rows = []
    res_id_lakes = []

    # create reservoir layer
    # loop through lake ids cooresponding to reservoirs
    for lake in ana_res:
        # get lat/lon from lake parm dataframe
        x = lakes[lakes["lake_id"] == lake]["lon"].item()
        y = lakes[lakes["lake_id"] == lake]["lat"].item()

        # find poi in hydrolocations using lake hl_uri
        hl_uri = f"lake-{lake}"
        poi = hl[hl["hl_uri"] == hl_uri]["poi_id"]
        if not poi.empty:
            poi = poi.unique().item()
        else:
            poi = None

        res = {
            "poi_id": poi,
            "comid": None,
            "domain": domain,
            "hl_reference": "lake",
            "hl_link": lake,
            "x": x,
            "y": y,
            "hl_source": "NOAAOWP",
        }
        res_rows.append(res)

        # hl_reference = usgs-gage row
        usgs_gage_id = ana_usgs_comid[ana_usgs_comid["usgs_lake_id"] == lake]["usgs_gage_id"]
        if not usgs_gage_id.empty:
            usgs_gage_id = usgs_gage_id.unique().item()
            hl_uri = f"usgs-gage-{usgs_gage_id}"
            poi = hl[hl["hl_uri"] == hl_uri]["poi_id"]
            if not poi.empty:
                poi = poi.unique().item()
            else:
                poi = None

            res = {
                "poi_id": poi,
                "comid": None,
                "domain": domain,
                "hl_reference": "usgs-gage",
                "hl_link": usgs_gage_id,
                "x": x,
                "y": y,
                "hl_source": "NOAAOWP",
            }
            res_rows.append(res)

        # hl_reference = usace-gage row
        usace_gage_id = ana_usace_comid[ana_usace_comid["usace_lake_id"] == lake]["usace_gage_id"]
        if not usace_gage_id.empty:
            usace_gage_id = usace_gage_id.unique().item()
            hl_uri = f"usace-gage-{usace_gage_id}"
            poi = hl[hl["hl_uri"] == hl_uri]["poi_id"]
            if not poi.empty:
                poi = poi.unique().item()
            else:
                poi = None

            res = {
                "poi_id": poi,
                "comid": None,
                "domain": domain,
                "hl_reference": "usace-gage",
                "hl_link": usace_gage_id,
                "x": x,
                "y": y,
                "hl_source": "NOAAOWP",
            }
            res_rows.append(res)

        # hl_reference = rfc-gage row
        rfc_gage_id = ana_rfc_comid[ana_rfc_comid["rfc_lake_id"] == lake]["rfc_gage_id"]
        if not rfc_gage_id.empty:
            rfc_gage_id = rfc_gage_id.unique().item()
            hl_uri = f"rfc-gage-{usace_gage_id}"
            poi = hl[hl["hl_uri"] == hl_uri]["poi_id"]
            if not poi.empty:
                poi = poi.unique().item()
            else:
                poi = None

            res = {
                "poi_id": poi,
                "comid": None,
                "domain": domain,
                "hl_reference": "rfc-gage",
                "hl_link": rfc_gage_id,
                "x": x,
                "y": y,
                "hl_source": "NOAAOWP",
            }
            res_rows.append(res)

        if lake in ext_res:
            hl_uri = f"lake-{lake}"
            poi = hl[hl["hl_uri"] == hl_uri]["poi_id"]
            if not poi.empty:
                poi = poi.unique().item()
                res_id = (
                    hl.loc[(hl["poi_id"] == poi) & (hl["hl_reference"] == "reservoir"), "hl_link"]
                    .unique()
                    .item()
                )
                res_id_lake = {"lake_id": lake, "res_id": res_id}
                res_id_lakes.append(res_id_lake)
                # res_id = hl.loc[(hl['poi_id'] == poi) & (hl['hl_reference'] == 'reservoir'),'hl_link'].item()
            else:
                poi = None
                res_id = None

            res = {
                "poi_id": poi,
                "comid": None,
                "domain": domain,
                "hl_reference": "reservoir",
                "hl_link": res_id,
                "x": x,
                "y": y,
                "hl_source": "reservoir_index_Extended_Range",
            }
            res_rows.append(res)

        if lake in med_res:
            res = {
                "poi_id": poi,
                "comid": None,
                "domain": domain,
                "hl_reference": "reservoir",
                "hl_link": res_id,
                "x": x,
                "y": y,
                "hl_source": "reservoir_index_Medium_Range",
            }
            res_rows.append(res)

        if lake in short_res:
            res = {
                "poi_id": poi,
                "comid": None,
                "domain": domain,
                "hl_reference": "reservoir",
                "hl_link": res_id,
                "x": x,
                "y": y,
                "hl_source": "reservoir_index_Short_Range",
            }
            res_rows.append(res)

        if lake in ana_res:
            res = {
                "poi_id": poi,
                "comid": None,
                "domain": domain,
                "hl_reference": "reservoir",
                "hl_link": res_id,
                "x": x,
                "y": y,
                "hl_source": "reservoir_index_AnA",
            }
            res_rows.append(res)

    df = pd.DataFrame(res_rows)
    df.to_csv(f"{output_path}/nwm_res.csv")

    res_id_lakes = pd.DataFrame(res_id_lakes)

    # Lakes Layer

    # copy lat and lon columns to y and x
    lakes["x"] = lakes["lon"]
    lakes["y"] = lakes["lat"]
    lakes["domain"] = domain

    # Get pois from hydrolocations layer
    lake_ids = [str(x) for x in lake_ids]
    pois = hl.loc[(hl["hl_link"].isin(lake_ids)) & (hl["hl_reference"] == "LAKEPARM")][["hl_link", "poi_id"]]
    pois = pois.rename(columns={"hl_link": "lake_id"})
    pois["lake_id"] = pois["lake_id"].astype(int)
    lakes = lakes.join(pois.set_index("lake_id"), on="lake_id")

    # join res ids to lakes
    lakes = lakes.join(res_id_lakes.set_index("lake_id"), on="lake_id")

    # join reservoir type for AnA if greater than 1
    lakes = lakes.join(ana_featureid.set_index("lake_id"), on="lake_id")
    lakes = lakes.rename(columns={"reservoir_type": "reservoir_index_AnA"})
    lakes.loc[lakes["reservoir_index_AnA"] == 1, "reservoir_index_AnA"] = None

    # join reservoir type for extended if greater than 1
    lakes = lakes.join(ext.set_index("lake_id"), on="lake_id")
    lakes = lakes.rename(columns={"reservoir_type": "reservoir_index_Extended_AnA"})
    lakes.loc[lakes["reservoir_index_Extended_AnA"] == 1, "reservoir_index_Extended_AnA"] = None

    # join reservoir type for medium if greater than 1
    lakes = lakes.join(med.set_index("lake_id"), on="lake_id")
    lakes = lakes.rename(columns={"reservoir_type": "reservoir_index_Medium_Range"})
    lakes.loc[lakes["reservoir_index_Medium_Range"] == 1, "reservoir_index_Medium_Range"] = None

    # join reservoir type for short if greater than 1
    lakes = lakes.join(short.set_index("lake_id"), on="lake_id")
    lakes = lakes.rename(columns={"reservoir_type": "reservoir_index_Short_Range"})
    lakes.loc[lakes["reservoir_index_Short_Range"] == 1, "reservoir_index_Short_Range"] = None

    # write lakes layer to csv
    lakes.to_csv(f"{output_path}/nwm_lakes.csv")

    # Create lakes layer from NWM lakes table
    lakes_layer = lakes

    # Convert wgs84 lat/lon to CONUS Albers coordinates for lakes_x and lakes_y columns
    transformer = Transformer.from_crs("EPSG:4326", domain_crs, always_xy=True)

    for index, row in lakes_layer.iterrows():
        y = row["y"]
        x = row["x"]
        conus_albers = transformer.transform(x, y)
        lakes_layer.loc[index, "x"] = conus_albers[0]  # latitude
        lakes_layer.loc[index, "y"] = conus_albers[1]  # longitude

    # rename x and y to lake_x and lake_y to match hf data model
    lakes_layer = lakes_layer.rename(columns={"y": "lake_y"})
    lakes_layer = lakes_layer.rename(columns={"x": "lake_x"})

    # add columns for hf_id and vpu_id.  Still need to figure out how to populate these
    lakes_layer["hf_id"] = None
    lakes_layer["vpu_id"] = None

    # remove time, lat, and lon columns to match hf data model.
    remove_cols = ["time", "lat", "lon"]
    lakes_layer = lakes_layer.drop(remove_cols, axis=1)

    # change order of columns to match hf lakes layer
    lakes_cols = [
        "lake_id",
        "LkArea",
        "LkMxE",
        "WeirC",
        "WeirL",
        "OrificeC",
        "OrificeA",
        "OrificeE",
        "WeirE",
        "ifd",
        "Dam_Length",
        "domain",
        "poi_id",
        "hf_id",
        "reservoir_index_AnA",
        "reservoir_index_Extended_AnA",
        "reservoir_index_GDL_AK",
        "reservoir_index_Medium_Range",
        "reservoir_index_Short_Range",
        "res_id",
        "vpuid",
        "lake_x",
        "lake_y",
    ]
    lakes_layer = lakes_layer.reindex(columns=lakes_cols)

    # Convert to a geo data frame and save as a geopackage
    gdf = gpd.GeoDataFrame(
        lakes_layer, geometry=gpd.points_from_xy(lakes_layer["lake_x"], lakes_layer["lake_y"], crs=domain_crs)
    )

    # Write geopackage
    gpkg_path = Path(f"{output_path}/lakes.gpkg")
    gdf.to_file(gpkg_path, layer="lakes", driver="GPKG")
