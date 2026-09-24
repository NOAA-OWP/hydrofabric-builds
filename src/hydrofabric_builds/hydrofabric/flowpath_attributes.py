from __future__ import annotations

import logging
import os
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import polars as pl
import rasterio
from shapely import force_2d

from hydrofabric_builds.schemas.hydrofabric import (
    FlowpathAttributesConfig,
    FlowpathAttributesModelConfig,
)

logger = logging.getLogger(__name__)


def _compute_geom_attributes(geom: Any, elev_dict: dict, min_slope: float) -> pd.Series:
    if geom.geom_type == "LineString":
        p1, p2 = geom.coords[0], geom.coords[-1]
        e1_: Any | None = elev_dict.get(p1)
        e2_: Any | None = elev_dict.get(p2)

        valid_elevs = [float(e) for e in (e1_, e2_) if e is not None and pd.notnull(e)]
        mean_elev = np.mean(valid_elevs) if valid_elevs else np.nan

        # native distance calc faster than shapely
        dist = ((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2) ** 0.5

        if e1_ is not None and e2_ is not None and pd.notnull(e1_) and pd.notnull(e2_) and dist > 0:
            slope = max(abs(e2_ - e1_) / dist, min_slope)
        else:
            slope = np.nan

        return pd.Series([mean_elev, slope], index=["mean_elevation", "slope"])

    elif geom.geom_type == "MultiLineString":
        endpoints = []
        degree: dict[tuple[float, float], int] = {}
        for line in geom.geoms:
            p1, p2 = line.coords[0], line.coords[-1]
            endpoints.extend([p1, p2])
            degree[p1] = degree.get(p1, 0) + 1
            degree[p2] = degree.get(p2, 0) + 1

        elevs = [elev_dict.get(pt) for pt in endpoints]
        valid_elevs = [float(e) for e in elevs if e is not None and pd.notnull(e)]
        mean_elev = np.mean(valid_elevs) if valid_elevs else np.nan

        # Extract endpoints using degree-1 criterion
        candidates = [pt for pt, deg in degree.items() if deg == 1] or list(degree.keys())

        # Find furthest pair distance to act as the main stem's dx
        max_dist = -1
        best_pair = (candidates[0], candidates[0])
        for i in range(len(candidates)):
            for j in range(i + 1, len(candidates)):
                d = (
                    (candidates[i][0] - candidates[j][0]) ** 2 + (candidates[i][1] - candidates[j][1]) ** 2
                ) ** 0.5
                if d > max_dist:
                    max_dist, best_pair = d, (candidates[i], candidates[j])

        p1, p2 = best_pair
        e1: Any | None = elev_dict.get(p1)
        e2: Any | None = elev_dict.get(p2)

        if e1 is not None and e2 is not None and pd.notnull(e1) and pd.notnull(e2) and max_dist > 0:
            slope = max(abs(e2 - e1) / max_dist, min_slope)
        else:
            slope = np.nan

        return pd.Series([mean_elev, slope], index=["mean_elevation", "slope"])

    else:
        return pd.Series([np.nan, np.nan], index=["mean_elevation", "slope"])


def _dem_attributes(model_cfg: FlowpathAttributesModelConfig, gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Derive DEM-based attributes (slope, mean_elevation)

    Start and end points of all flowpaths are retrieved. The set of these is used to sample DEM.
    Slope is calculated as the absolute value of dz / dx (distance)
    Mean elevation is calculated as the mean of linestring start and end point
    Linestrings are converted to multistring when possible.
    Multilinestrings take mean of all points and slope of computed using the start and end points of the multi-line.

    Parameters
    ----------
    model_cfg : FlowpathAttributesModelConfig
        FlowpathAttributesModelConfig object
    gdf : gpd.GeoDataFrame
        Flowpaths GeoDataFrame

    Returns
    -------
    gpd.GeoDataFrame
        Flowpaths GeoDataFrame including slope and mean_elevation
    """
    gdf["geometry"] = gdf["geometry"].line_merge()
    gdf["geometry"] = gdf.geometry.apply(force_2d)

    mls_count = (gdf.geometry.type == "MultiLineString").sum()
    if mls_count > 0:
        logger.info(f"Multilinestrings found - {mls_count}")

    # Collect line endpoint coords
    coords_to_sample = set()
    for geom in gdf.geometry:
        if geom.geom_type == "LineString":
            coords_to_sample.update([geom.coords[0], geom.coords[-1]])
        elif geom.geom_type == "MultiLineString":
            for line in geom.geoms:
                coords_to_sample.update([line.coords[0], line.coords[-1]])

    sorted_coords = rasterio.sample.sort_xy(list(coords_to_sample))

    logger.info("Sampling DEM points")

    # sample with S3 if needed
    if "s3" in str(model_cfg.dem_path):
        session = rasterio.session.AWSSession(
            aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
            aws_session_token=os.environ["AWS_SESSION_TOKEN"],
        )
        env = rasterio.Env(session=session)
    else:
        env = rasterio.Env()

    with env:
        with rasterio.open(model_cfg.dem_path, mode="r") as src:
            samples = src.sample(sorted_coords)
            elev_dict = {
                coord: float(val[0]) if val[0] != -999999.0 else np.nan
                for coord, val in zip(sorted_coords, samples, strict=False)
            }

    min_slope: float = 1e-5

    # Create attributes for each linestring
    attrs = pd.DataFrame(
        [_compute_geom_attributes(geom, elev_dict, min_slope) for geom in gdf.geometry], index=gdf.index
    )

    gdf["mean_elevation"] = attrs["mean_elevation"]
    gdf["slope"] = attrs["slope"]

    # Replace NaN slopes with minimum slope value
    gdf["slope"] = gdf["slope"].fillna(min_slope)

    return gdf


def _create_base_polars(gdf: gpd.GeoDataFrame) -> pl.DataFrame:
    """Create a base polars dataframe with needed columns

    Parameters
    ----------
    gdf : gpd.GeoDataFrame
        Flowpaths geodataframe

    Returns
    -------
    pl.DataFrame
        Flowpaths polars dataframe
    """
    df = pl.from_pandas(gdf[["fp_id", "stream_order", "total_da_sqkm"]])
    df = df.with_columns(
        pl.lit(None).alias("n"),
        pl.lit(None).alias("r"),
        pl.lit(None).alias("y"),
        pl.lit(None).alias("ncc"),
        pl.lit(None).alias("btmwdth"),
        pl.lit(None).alias("chslp"),
        pl.lit(None).alias("musx"),
        pl.lit(None).alias("musk"),
        pl.lit(None).alias("topwdth"),
        pl.lit(None).alias("topwdthcc"),
        pl.lit(None).alias("topwdthcc_ml"),
    )

    return df


def _riverml_attributes(model_cfg: FlowpathAttributesModelConfig, df: pl.DataFrame) -> pl.DataFrame:
    """Retrieve riverml attributes (y, r, topwidth) from prediction parquets and join to df

    Parameters
    ----------
    model_cfg : FlowpathAttributesModelConfig
        FlowpathAttributesModelConfig object
    df : pl.DataFrame
        Flowpaths polars dataframe

    Returns
    -------
    pl.DataFrame
        Flowpaths polars dataframe including y and topwdth
    """
    # joining to reference flowpaths
    # df_refj has multiple fp_id for 1:many ref_fp_id relationship
    gdf_ref = gpd.read_file(model_cfg.hf_path, layer="reference_flowpaths")
    df_ref = pl.from_pandas(gdf_ref)
    # Cast fp_id on both sides to Int64 with strict=False to handle any NaN/null values
    df_ref = df_ref.with_columns(pl.col("fp_id").cast(pl.Int64, strict=False))
    df = df.with_columns(pl.col("fp_id").cast(pl.Int64, strict=False))
    df_refj = df.join(df_ref, on="fp_id", how="left")
    # Cast ref_fp_id to Int64 to match parquet predictions; f64 vs i64 mismatch causes join errors
    df_refj = df_refj.with_columns(pl.col("ref_fp_id").cast(pl.Int64, strict=False))

    # join predictions to fp with ref fp and calculate mean for fp_id (multiple ref_fp_id) for each ML field
    if model_cfg.tw_path:
        df_tw = (
            pl.read_parquet(model_cfg.tw_path)
            .rename({"FEATUREID": "ref_fp_id", "prediction": "topwdth_ml"})
            .with_columns(pl.col("ref_fp_id").cast(pl.Int64, strict=False))
        )
        df_tmp = df_refj.join(df_tw, on="ref_fp_id", how="full")
        df_meantw = df_tmp[["fp_id", "topwdth_ml"]].group_by("fp_id").mean()
        del df_tmp
    else:
        df_meantw = df_refj[["fp_id"]].unique(keep="first").with_columns(pl.lit(None).alias("topwdth_ml"))

    if model_cfg.y_path:
        df_y = (
            pl.read_parquet(model_cfg.y_path)
            .rename({"FEATUREID": "ref_fp_id", "prediction": "y_ml"})
            .with_columns(pl.col("ref_fp_id").cast(pl.Int64, strict=False))
        )
        df_tmp = df_refj.join(df_y, on="ref_fp_id", how="full")
        df_meany = df_tmp[["fp_id", "y_ml"]].group_by("fp_id").mean()
        del df_tmp
    else:
        df_meany = df_refj[["fp_id"]].unique(keep="first").with_columns(pl.lit(None).alias("y_ml"))

    if model_cfg.r_path:
        df_r = (
            pl.read_parquet(model_cfg.r_path)
            .rename({"FEATUREID": "ref_fp_id", "prediction": "r_ml"})
            .with_columns(pl.col("ref_fp_id").cast(pl.Int64, strict=False))
        )
        df_tmp = df_refj.join(df_r, on="ref_fp_id", how="full")
        df_meanr = df_tmp[["fp_id", "r_ml"]].group_by("fp_id").mean()
        del df_tmp
    else:
        df_meanr = df_refj[["fp_id"]].unique(keep="first").with_columns(pl.lit(None).alias("r_ml"))

    # join back to original fp_id df
    df = df.join(df_meantw, on="fp_id", how="left")
    df = df.join(df_meany, on="fp_id", how="left")
    df = df.join(df_meanr, on="fp_id", how="left")

    del df_ref, df_refj, df_meanr, df_meantw, df_meany

    return df


def _other_flowpath_attributes(model_cfg: FlowpathAttributesModelConfig, df: pl.DataFrame) -> pl.DataFrame:
    """Use pydantic model to popular other attributes

    Attributes added:
    - n
    - ncc
    - btmwdth
    - topwdthcc
    - topwdth (non-ML)
    - chslp
    - musx
    - musk

    See hydrofabric_builds.schemas.hydrofabric.FlowpathAttributesConfig model for full details

    Most variables calculated from defaults or stream-order derived values from WRF GIS pre-processor
    Source: https://github.com/NCAR/wrf_hydro_gis_preprocessor/blob/5781ad4788434e8fd4ec16f3a3805d98536a9f82/wrfhydro_gis/wrfhydro_functions.py#L128
    Accessed 10/20/25

    Parameters
    ----------
    model_cfg : FlowpathAttributesModelConfig
        FlowpathAttributesModelConfig object
    df : pl.DataFrame
        Flowpaths polars dataframe

    Returns
    -------
    pl.DataFrame
        Flowpaths polars dataframe with added n, ncc, btwmdth, tpwdthcc, chslp, musx, musk
    """
    # NOTE: This could likely be re-implemented faster in pure polars; however the pydantic schema preserves metadata and logic cleanly
    models = []
    for row in df.iter_rows(named=True):
        model = FlowpathAttributesConfig(
            use_stream_order=model_cfg.use_stream_order,
            stream_order=row["stream_order"],
            TopWdth_ml=row["topwdth_ml"],
            Y=row["y"],
            total_da_sqkm=row["total_da_sqkm"],
        )

        # exclude attributes already calculated (validators auto-populate remaining fields)
        models.append(
            model.model_dump(
                exclude={"use_stream_order", "stream_order", "mean_elevation", "slope", "total_da_sqkm"}
            )
        )

    df_models = pl.from_records(models)
    df = df.update(df_models)

    del df_models, models

    return df


def _write_output(model_cfg: FlowpathAttributesModelConfig, gdf: gpd.GeoDataFrame, df: pl.DataFrame) -> None:
    """Write output to parquet or gpkg

    Parameters
    ----------
    model_cfg : FlowpathAttributesModelConfig
        FlowpathAttributesModelConfig object
    gdf : gpd.GeoDataFrame
        Original flowpaths geodataframe
    df : pl.DataFrame
        Polars dataframe populated with new variables
    """
    # drop stream order for single join field
    df_pd = gpd.GeoDataFrame(df.drop(["stream_order", "total_da_sqkm"]).to_pandas())
    gdf = gdf.merge(df_pd, on="fp_id")

    gdf.to_file(model_cfg.hf_path, layer="flowpaths", driver="GPKG", overwrite=True)

    del df_pd, gdf, df


def flowpath_attributes_pipeline(model_cfg: FlowpathAttributesModelConfig) -> None:
    """Pipeline to run flowpath attributes"""
    logger.info("Reading hydrofabric flowpaths file")
    gdf = gpd.read_file(model_cfg.hf_path, layer="flowpaths")

    # dem attributes
    logger.info("Starting DEM processing")
    gdf = _dem_attributes(model_cfg, gdf)

    # to polars for other attributes
    df = _create_base_polars(gdf)

    # river ml
    logger.info("Processing RiverML attributes")
    df = _riverml_attributes(model_cfg=model_cfg, df=df)

    # other attributes
    logger.info("Calculating other attributes")
    df = _other_flowpath_attributes(model_cfg=model_cfg, df=df)

    logger.info("Writing flowpath attributes output")
    _write_output(model_cfg, gdf=gdf, df=df)
