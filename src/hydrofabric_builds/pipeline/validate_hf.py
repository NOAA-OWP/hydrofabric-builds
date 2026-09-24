import json
import logging
from pathlib import Path
from typing import Any, cast

import geopandas as gpd
import pandas as pd
from pandantic import Pandantic
from pydantic import ValidationError

from hydrofabric_builds.config import HFConfig
from hydrofabric_builds.schemas.validate_hydrofabric import CRS, Divides, Domain, Flowpaths, Layer, VegTypes

logger = logging.getLogger(__name__)


def validate_layer(gpkg_path_filename: Path, layer_name: Layer, crs: CRS) -> list[dict[Any, Any]]:
    """Validate the divides layer using Pydantic and check for NaNs

    Parameters
    ----------
    gpkg_path_filename : Path
        full path and filename of the NHF geopackage

    layer_name : Layer
        Name of geopackage layer for divides or flowpaths

    crs : CRS
        EPSG string for the CRS

    Returns
    -------
    list
        list of dictionaries containing divide validation
    """
    # Read layer and convert from geo data frame to a Pandas data frame
    try:
        layer = gpd.read_file(gpkg_path_filename, layer=layer_name.value)
    except FileNotFoundError:
        error_str = {"Error": f"The file {gpkg_path_filename} was not found."}
        logger.warning(error_str)
        return [error_str]
    except ValueError:
        error_str = {"Error": f"Unable to read {layer_name.value} layer from {gpkg_path_filename}"}
        logger.warning(error_str)
        return [error_str]
    layer = pd.DataFrame(layer)

    # Get CRS and set the domain name
    if crs == CRS.CONUS:
        domain = Domain.CONUS.value
    elif crs == CRS.AK:
        domain = Domain.AK.value
    elif crs == CRS.HI:
        domain = Domain.HI.value
    elif crs == CRS.PRVI:
        domain = Domain.PRVI.value

    # Create empty list to store output items
    layer_out: list[dict[str, int | list[str] | dict[Any, Any]]] = []

    # Get total number of rows in layer
    layer_out.append({f"Total number of {layer_name.value} rows": len(layer)})

    # Get total number of rows with NaNs
    rows_with_nan = layer[layer.isna().any(axis=1)]
    layer_out.append({f"Total number of {layer_name.value} rows with NaNs": len(rows_with_nan)})

    # Get NaN counts by attribute
    nan_counts = layer.isna().sum().to_dict()
    layer_out.append({"Number of NaNs per attribute": nan_counts})

    if layer_name.value == Layer.DIVIDES.value:
        with_nans = layer.columns[layer.isna().any()].tolist()
        attr_dict = {}
        for col in with_nans:
            counts = layer[layer[col].isna()]["ivgtyp_mode"].value_counts().to_frame(name="NA counts")
            counts = counts.reset_index()
            counts["ivgtyp_mode"] = counts["ivgtyp_mode"].astype(int)
            counts["ivgtyp_name"] = counts["ivgtyp_mode"].map(VegTypes.veg_types)
            values_dict = counts.to_dict(orient="records")
            attr_dict.update({col: values_dict})

        layer_out.append({"Vegetation type for NaN divide attributes": attr_dict})

    # Use Pandantic to validate the data frames with the Pydantic schema.
    if layer_name == Layer.DIVIDES:
        # Add the domain name to the dataframe to select the proper lat/lon ranges per domain
        layer["domain"] = domain
        pydantic_schema = Divides
        logger.info("Validate divide attributes format")
    elif layer_name == Layer.FLOWPATHS:
        pydantic_schema = Flowpaths
        logger.info("Validate flowpaths attributes format")

    # Empty list to collect validation errors
    validation_errors = []

    validator = Pandantic(schema=pydantic_schema)

    try:
        validator.validate(dataframe=layer, errors="raise")
    except ValidationError as e:
        error_details = e.errors()
        for error in error_details:
            validation_errors.append(f"{error['loc']}: {error['msg']}; value is {error['input']}")
    layer_out.append({f"Validate {layer_name.value} attributes format": validation_errors})

    return layer_out


def validate_calibration_gages(gpkg_path_filename: Path, crs: CRS, gages_list: Path) -> list:
    """Check NHF gages against the list of ~1600 gages

    Parameters
    ----------
    gpkg_path_filename : Path
        full path and filename of the NHF geopackage
    crs : CRS
        CRS EPSG string
    gages_list : Path
        path and filename for calibratable gages list in csv format

    Returns
    -------
    list
        a list of missing gages
    """
    try:
        gages_layer = gpd.read_file(gpkg_path_filename, layer="Gages")
    except FileNotFoundError:
        error_str = f"Error: The file {gpkg_path_filename} was not found."
        logger.warning(error_str)
        return [error_str]
    except ValueError:
        error_str = f"Error reading gages layer from {gpkg_path_filename}"
        logger.warning(error_str)
        return [error_str]

    gages_layer = pd.DataFrame(gages_layer)

    gages_nhf = gages_layer["site_no"].to_list()

    calibratable_gages = pd.read_csv(gages_list)

    if crs.value == CRS.CONUS.value:
        calibratable_gages_domain: list = calibratable_gages[
            calibratable_gages["domain"] == Domain.CONUS.value
        ]["gage_id"].to_list()
    elif crs.value == CRS.AK.value:
        calibratable_gages_domain = calibratable_gages[calibratable_gages["domain"] == Domain.AK.value][
            "gage_id"
        ].to_list()
    elif crs.value == CRS.HI.value:
        calibratable_gages_domain = calibratable_gages[calibratable_gages["domain"] == Domain.HI.value][
            "gage_id"
        ].to_list()
    elif crs.value == CRS.PRVI.value:
        calibratable_gages_domain = calibratable_gages[calibratable_gages["domain"] == Domain.PRVI.value][
            "gage_id"
        ].to_list()

    gages_out = []

    gage_diffs = list(set(calibratable_gages_domain) - set(gages_nhf))
    gages_out.append({"Missing Calibration Gages": gage_diffs})

    missing_fp = gages_layer[gages_layer["fp_id"].isna() & gages_layer["virtual_fp_id"].isna()][
        "site_no"
    ].to_list()
    gages_out.append({"Gages with no flowpath or virtual flowpath": missing_fp})

    missing_fp_calibratable_gage = [
        gage for gage in set(missing_fp) if gage in set(calibratable_gages_domain)
    ]
    gages_out.append(
        {"Calibratable gages with no flowpath or virtual flowpath": missing_fp_calibratable_gage}
    )

    return gages_out


def validate_routelink_gages(gpkg_path_filename: Path, routelink_path: Path, id_col: str = "gages") -> list:
    """Check NHF gages against the list of ~1600 gages

    Parameters
    ----------
    gpkg_path_filename : Path
        full path and filename of the NHF geopackage
    routelink_path : Path
        full pat hand filename to routelink inputs
    id_col : Str
        ID column for gages in routelink

    Returns
    -------
    list
        a list of missing gages and gages with no flowpath/virtual flowpath
    """
    try:
        gages_layer = gpd.read_file(gpkg_path_filename, layer="Gages")
    except FileNotFoundError:
        error_str = f"Error: The file {gpkg_path_filename} was not found."
        logger.warning(error_str)
        return [error_str]
    except ValueError:
        error_str = f"Error reading gages layer from {gpkg_path_filename}"
        logger.warning(error_str)
        return [error_str]

    try:
        routelink_gages = gpd.read_file(routelink_path)
    except FileNotFoundError:
        error_str = f"Error: The file {routelink_path} was not found."
        logger.warning(error_str)
        return [error_str]

    gages_nhf = gages_layer["site_no"].to_list()

    # strip whitespace from routelink and extract gages
    routelink_gages[id_col] = routelink_gages[id_col].str.strip()
    routelink_gages = routelink_gages.loc[routelink_gages[id_col] != ""].copy()
    routelink_list = routelink_gages[id_col].tolist()

    gages_out = []

    gage_diffs = list(set(routelink_list) - set(gages_nhf))
    gages_out.append({"Missing Routelink Gages": gage_diffs})

    missing_fp = gages_layer[gages_layer["fp_id"].isna() & gages_layer["virtual_fp_id"].isna()][
        "site_no"
    ].to_list()

    missing_fp_routelink_gage = [gage for gage in set(missing_fp) if gage in set(routelink_list)]
    gages_out.append({"Routelink gages with no flowpath or virtual flowpath": missing_fp_routelink_gage})

    return gages_out


def find_lake_duplicates(
    lakes_layer: gpd.GeoDataFrame,
    buffered_nwm_lakes: gpd.GeoDataFrame,
    id_col: str,
    save_duplicate_gpkgs: bool,
    duplicate_gpkg_save_dir: Path,
) -> dict:
    """Check for duplicate lake points

    Parameters
    ----------
    lakes_layer : geopandas.GeoDataFrame
        geodataframe containing the NHF lake points
    buffered_nwm_lakes : geopandas.GeoDataFrame
        geodataframe containing the NWM lake polygons
    id_col : str
        ID column in NWM lakes
    save_duplicate_gpkgs : bool
        flag to save the resulting duplicate lake points/polygons as geopackages
    duplicate_gpkg_save_dir : Path,
        directory in which to save resulting duplicate lake points/polygons

    Returns
    -------
    dict
        a dict, where each key is a lake and the corresponding value is a list of duplicate point(s) for that point
    """
    # Standardize to the points' CRS (should be ESRI:5070) for consistent spatial operations
    # EPSG:5070 uses meters, so a 1000m buffer is exactly 1km
    logger.info("Standardizing CRS between datasets...")
    target_crs = lakes_layer.crs
    if buffered_nwm_lakes.crs != target_crs:
        buffered_nwm_lakes = buffered_nwm_lakes.to_crs(target_crs)

    # Use overlay to keep only the NHF points that lie within the buffered NWM lake polygons
    logger.info("Performing spatial overlay intersection...")
    intersection_gdf = gpd.overlay(lakes_layer, buffered_nwm_lakes, how="intersection")

    # Group by the NWM lake polygon identifier to find which NWM lakes capture multiple NHF lake points
    # Transform 'count' gives us the total number of entries in that group for each row
    logger.info("Finding all NWM lake polygons that contain multiple NHF lake points...")
    intersection_gdf["point_count_per_poly"] = intersection_gdf.groupby(id_col)["lake_id"].transform("count")
    intersection_gdf = intersection_gdf.drop_duplicates(subset="lake_id")

    # Filter to keep only rows where a polygon contains more than 1 lake point
    nwm_lake_mult_nhf_points = intersection_gdf[intersection_gdf["point_count_per_poly"] > 1].copy()

    # Find all intersecting NHF points whose lake ID does not match any existing NWM lake ID
    mask = ~nwm_lake_mult_nhf_points["lake_id"].astype("int64").isin(buffered_nwm_lakes[id_col])
    nhf_points_no_match = nwm_lake_mult_nhf_points[mask]

    # Add NWM lake geometries to the found NWM lakes
    nwm_lake_mult_nhf_points = nwm_lake_mult_nhf_points.drop_duplicates(subset=id_col)
    nwm_lake_mult_nhf_points = nwm_lake_mult_nhf_points.merge(
        buffered_nwm_lakes[[id_col, "geometry"]], on=id_col
    )
    nwm_lake_mult_nhf_points = nwm_lake_mult_nhf_points.drop(columns=["geometry_x"])
    nwm_lake_mult_nhf_points = nwm_lake_mult_nhf_points.rename(columns={"geometry_y": "geometry"})

    # Clean up dataframe to only include the requested columns and geometry
    logger.info("Dropping unneeded columns from results...")
    polygon_columns_to_keep = [id_col, "point_count_per_poly", "geometry"]
    point_columns_to_keep = ["lake_id", id_col, "dam_id", "geometry"]
    nwm_lake_mult_nhf_points = nwm_lake_mult_nhf_points[polygon_columns_to_keep]
    nhf_points_no_match = nhf_points_no_match[point_columns_to_keep]

    # Create output dict and populate it with information to report back
    logger.info("Populating validation output dictionary...")
    nwm_lakes_to_report = {}
    for x in set(nwm_lake_mult_nhf_points[id_col]):
        a = nhf_points_no_match.loc[(nhf_points_no_match[id_col] == x)]["lake_id"].tolist()
        if len(a) != 0:
            nwm_lakes_to_report[x] = a
    nwm_lakes_to_report_gdf = nwm_lake_mult_nhf_points[
        nwm_lake_mult_nhf_points[id_col].isin(nwm_lakes_to_report.keys())
    ]

    if nwm_lakes_to_report:
        logger.info(f"{len(nwm_lakes_to_report)} lakes found with duplicate point(s).")
    else:
        logger.info("No lakes were found with any duplicate points!")

    # Save results as a geopackage, if wanted
    if save_duplicate_gpkgs:
        logger.info(
            f"Saving geopackages with problem lake points/polygons. Output dir: {duplicate_gpkg_save_dir}"
        )
        nwm_lakes_to_report_gdf.to_file(
            duplicate_gpkg_save_dir / "nwm_problem_lake_polygons.gpkg",
            layer="multi_point_lakes",
            driver="GPKG",
        )
        nhf_points_no_match.to_file(
            duplicate_gpkg_save_dir / "nhf_problem_lake_points.gpkg",
            layer="problem_lake_points",
            driver="GPKG",
        )
    else:
        logger.info("Skipping saving geopackage results...")

    logger.info("Lake duplication validation complete!")
    return nwm_lakes_to_report


def validate_lakes(
    gpkg_path_filename: Path,
    nwm_lakes_path: Path,
    buffer_path: Path,
    id_col: str,
    validate_duplicates: bool,
    save_duplicate_gpkgs: bool,
) -> list:
    """Validate that the lakes layer has all NWM lakes

    Parameters
    ----------
    gpkg_path_filename : Path
        full path and filename of the NHF geopackage
    nwm_lakes_path : Path
        full path and file name to NWM lakes geopackage
    buffer_path : Path
        full path and file name to NWM lakes geopackage, buffered out for duplicate validation
    id_col : str
        ID column in NWM lakes
    validate_duplicates : bool
        flag to run duplicate lake point validation
    save_duplicate_gpkgs : bool,
        flag to save the resulting duplicate lake points/polygons as geopackages

    Returns
    -------
    list
        a list of missing lakes, lakes with no flowpaths/virtual flowpath, and lakes with multiple (duplicate) points
    """
    logger.info("Loading NHF and NWM Lakes for validation...")
    try:
        lakes_layer = gpd.read_file(gpkg_path_filename, layer="lakes")
    except FileNotFoundError:
        error_str = f"Error: The file {gpkg_path_filename} was not found."
        logger.warning(error_str)
        return [error_str]
    except ValueError:
        error_str = f"Error reading 'lakes' layer from {gpkg_path_filename}"
        logger.warning(error_str)
        return [error_str]

    try:
        nwm_lakes = gpd.read_file(nwm_lakes_path)
    except FileNotFoundError:
        error_str = f"Error: The file {nwm_lakes_path} was not found."
        logger.warning(error_str)
        return [error_str]

    lakes_out = []

    # Normalize types: NHF lake_id is often str, NWM id_col is often int
    # Cast both to int for a fair comparison; skip non-numeric values
    logger.info("Normalizing ID types between NHF and NWM lakes...")
    lakes_layer.dropna(subset=["lake_id"], inplace=True)
    lakes_layer = lakes_layer[lakes_layer["lake_id"].astype(str).str.isdigit()]
    lakes_layer.loc[:, "lake_id"] = lakes_layer["lake_id"].astype(int)
    nhf_lake_ids = set(lakes_layer["lake_id"].tolist())
    nwm_lake_ids = set(nwm_lakes[id_col].tolist())

    lake_diffs = list(nwm_lake_ids - nhf_lake_ids)
    if lake_diffs:
        logger.info(f"{len(lake_diffs)} missing NWM/Lakeparm lakes found.")
    else:
        logger.info("No missing NWM/Lakeparm lakes!")
    lakes_out.append({"Missing NWM or Lakeparm Lakes": lake_diffs})

    missing_fp = lakes_layer[lakes_layer["fp_id"].isna() & lakes_layer["virtual_fp_id"].isna()][
        "lake_id"
    ].to_list()
    missing_fp_lakes = list(nwm_lake_ids.intersection(missing_fp))
    if missing_fp_lakes:
        logger.info(f"{len(missing_fp_lakes)} lakes found with no flowpath/virtual flowpath.")
    else:
        logger.info("No lakes found with a missing flowpath/virtual flowpath!")
    lakes_out.append({"Lakes with no flowpath or virtual flowpath": missing_fp_lakes})

    if validate_duplicates:
        logger.info("Now validating lakes for any duplicate points...")
        try:
            buffered_nwm_lakes = gpd.read_file(buffer_path, layer="nwm_lakes")
        except FileNotFoundError:
            error_str = f"Error: The file {buffer_path} was not found."
            logger.warning(error_str)
            lakes_out.append({"Lakes with multiple/duplicate points": [error_str]})
        except ValueError:
            error_str = f"Error reading 'nwm_lakes' layer from {buffer_path}"
            logger.warning(error_str)
            lakes_out.append({"Lakes with multiple/duplicate points": [error_str]})
        else:
            dups = find_lake_duplicates(
                lakes_layer=lakes_layer,
                buffered_nwm_lakes=buffered_nwm_lakes,
                id_col=id_col,
                save_duplicate_gpkgs=save_duplicate_gpkgs,
                duplicate_gpkg_save_dir=buffer_path.parent,
            )
            lakes_out.append({"Lakes with multiple/duplicate points": [dups]})
    else:
        logger.info("Skipping lake duplicate validation...")
        lakes_out.append({"Lakes with multiple/duplicate points": [{}]})

    logger.info("Lakes validation complete!")
    return lakes_out


def validate_hf(**context: dict[str, Any]) -> dict[str, Any]:
    """Validates the divides and flowpath layers

    Parameters
    ----------
    **context : dict
        Airflow-compatible context containing:
        - ti : TaskInstance for XCom operations
        - config : HFConfig with pipeline configuration
        - task_id : str identifier for this task
        - run_id : str identifier for this pipeline run
        - ds : str execution date
        - execution_date : datetime object

    Returns
    -------
    dict
        validation status
    """
    cfg = cast(HFConfig, context["config"])
    file_name = cfg.output_file_path
    path = cfg.output_dir
    gpkg_file_root = cfg.output_name.stem
    crs = cfg.crs
    calibration_gages = cfg.validate_hf.calibration_gages_path

    # Check if CRS is valid
    try:
        crs_enum = CRS(crs)
    except ValueError:
        error_str = f"CRS {crs} is not valid"
        logger.warning(error_str)
        return {"validation": error_str}

    divides = validate_layer(file_name, Layer.DIVIDES, crs_enum)
    flowpaths = validate_layer(file_name, Layer.FLOWPATHS, crs_enum)
    calibration_gage_output = validate_calibration_gages(file_name, crs_enum, calibration_gages)
    routelink_gage_output = (
        validate_routelink_gages(
            file_name,
            routelink_path=cfg.validate_hf.routelink_gages_path,
            id_col=cfg.gages.gages.inputs.routelink.id_col_name,
        )
        if cfg.validate_hf.routelink_gages_path
        else ["No routelink file found; validation not run"]
    )
    lakes = (
        validate_lakes(
            gpkg_path_filename=file_name,
            nwm_lakes_path=cfg.lakes.nwm.path,
            buffer_path=cfg.lakes.nwm.buffered_path,
            id_col=cfg.lakes.nwm.id_field,
            validate_duplicates=cfg.lakes.validate_duplicates,
            save_duplicate_gpkgs=cfg.lakes.save_duplicate_gpkgs,
        )
        if cfg.lakes.nwm.path
        else ["No NWM lakes file found; validation not run"]
    )

    output_items = {
        "Divides": divides,
        "Flowpaths": flowpaths,
        "Gages": calibration_gage_output + routelink_gage_output,
        "Lakes": lakes,
    }

    with open(f"{path}/{gpkg_file_root}_validation.json", "w") as file:
        json.dump(output_items, file, indent=4)

    return {"validation": "done"}
