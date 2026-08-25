import json
import logging
from pathlib import Path
from typing import Any, cast

import geopandas as gpd
import pandas as pd
from pandantic import Pandantic
from pydantic import ValidationError

from hydrofabric_builds.config import HFConfig
from hydrofabric_builds.schemas.validate_hydrofabric import (
    CRS,
    Divides,
    Domain,
    Flowpaths,
    Layer,
)

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
    layer_out: list[dict[str, int | list[str]]] = []

    # Get total number of rows in layer
    layer_out.append({f"Total number of {layer_name.value} rows": len(layer)})

    # Get total number of rows with NaNs
    rows_with_nan = layer[layer.isna().any(axis=1)]
    layer_out.append({f"Total number of {layer_name.value} rows with NaNs": len(rows_with_nan)})

    # Get NaN counts by attribute
    nan_counts = layer.isna().sum().to_dict()
    layer_out.append({"Number of NaNs per attribute": nan_counts})

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


def validate_lakes(gpkg_path_filename: Path, nwm_lakes_path: Path, id_col: str) -> list:
    """Validate that the lakes layer has all NWM lakes

    Parameters
    ----------
    gpkg_path_filename : Path
        full path and filename of the NHF geopackage
    nwm_lakes_path : Path
        full path and file name to NWM lakes geopackage
    id_col : str
        ID column in NWM lakes

    Returns
    -------
    list
        a list of missing lakes and lakes with no flowpaths/virtual flowpath
    """
    try:
        lakes_layer = gpd.read_file(gpkg_path_filename, layer="lakes")
    except FileNotFoundError:
        error_str = f"Error: The file {gpkg_path_filename} was not found."
        logger.warning(error_str)
        return [error_str]
    except ValueError:
        error_str = f"Error reading gages layer from {gpkg_path_filename}"
        logger.warning(error_str)
        return [error_str]

    try:
        nwm_lakes = gpd.read_file(nwm_lakes_path)
    except FileNotFoundError:
        error_str = f"Error: The file {nwm_lakes_path} was not found."
        logger.warning(error_str)
        return [error_str]

    lakes_out = []

    lakes_nhf = lakes_layer["lake_id"].to_list()
    lakes_nwm = nwm_lakes[id_col].to_list()

    lake_diffs = list(set(lakes_nwm) - set(lakes_nhf))
    lakes_out.append({"Missing NWM or Lakeparm Lakes": lake_diffs})

    missing_fp = lakes_layer[lakes_layer["fp_id"].isna() & lakes_layer["virtual_fp_id"].isna()][
        "lake_id"
    ].to_list()

    missing_fp_lakes = [lake for lake in set(missing_fp) if lake in set(lakes_nwm)]
    lakes_out.append({"Lakes with no flowpath or vitual flowpath": missing_fp_lakes})

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
        validate_lakes(file_name, nwm_lakes_path=cfg.lakes.input_path, id_col=cfg.lakes.id_field)
        if cfg.lakes.input_path
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
