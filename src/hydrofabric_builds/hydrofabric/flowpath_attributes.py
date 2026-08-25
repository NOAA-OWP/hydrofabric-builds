import logging
import os

import geopandas as gpd
import numpy as np
import pandas as pd
import polars as pl
import rasterio
from shapely import get_point
from shapely.geometry import Point

from hydrofabric_builds.schemas.hydrofabric import (
    FlowpathAttributesConfig,
    FlowpathAttributesModelConfig,
)

logger = logging.getLogger(__name__)


def _dem_attributes(model_cfg: FlowpathAttributesModelConfig, gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Derive DEM-based attributes (slope, mean_elevation)

    Start and end points of all flowpaths are retrieved. The set of these is used to sample DEM.
    Slope is calculated as the absolute value of dz / dx (distance)
    Mean elevation is calculated as the mean of linestring start and end point
    Linestrings are converted to multistring when possible.
    Multilinestrings take mean of all points and slope of maximum length segment.

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
    # Assure lines are linestrings and not multi
    gdf["geometry"] = gdf["geometry"].line_merge()

    gdf_ls = gdf.loc[gdf.geometry.geometry.type == "LineString", [model_cfg.flowpath_id, "geometry"]].copy()
    gdf_mls = gdf.loc[
        gdf.geometry.geometry.type == "MultiLineString", [model_cfg.flowpath_id, "geometry"]
    ].copy()
    mls_exists = gdf_mls.shape[0]
    logger.info(f"Multilinestrings found - {mls_exists}")

    # Retrieve sorted set of linestring start and end points
    gdf_ls["point_1"] = get_point(gdf.geometry, 0)
    gdf_ls["point_2"] = get_point(gdf.geometry, -1)
    coords_starts = [(x, y) for x, y in zip(gdf_ls["point_1"].x, gdf_ls["point_1"].y, strict=False)]
    coords_ends = [(x, y) for x, y in zip(gdf_ls["point_2"].x, gdf_ls["point_2"].y, strict=False)]

    set_coords = set(coords_starts + coords_ends)

    # handle multilinestring
    if mls_exists:
        gdf_mls = gdf_mls.explode().reset_index(drop=True)
        gdf_mls["point_1"] = get_point(gdf_mls.geometry, 0)
        gdf_mls["point_2"] = get_point(gdf_mls.geometry, -1)
        coords_starts_mls = [(x, y) for x, y in zip(gdf_mls["point_1"].x, gdf_mls["point_1"].y, strict=False)]
        coords_ends_mls = [(x, y) for x, y in zip(gdf_mls["point_2"].x, gdf_mls["point_2"].y, strict=False)]

        set_coords = set(list(set_coords) + coords_starts_mls + coords_ends_mls)

    sorted = rasterio.sample.sort_xy(set_coords)

    logger.info("Sampling DEM points")
    # sample with S3 if needed
    if "s3" in str(model_cfg.dem_path):
        session = rasterio.session.AWSSession(
            aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
            aws_session_token=os.environ["AWS_SESSION_TOKEN"],
        )

        with rasterio.Env(session=session) as env:  # noqa: F841
            with rasterio.open(model_cfg.dem_path, mode="r") as src:
                points = dict(zip(sorted, [point[0] for point in src.sample(sorted)], strict=False))
    else:
        with rasterio.open(model_cfg.dem_path, mode="r") as src:
            points = dict(zip(sorted, [point[0] for point in src.sample(sorted)], strict=False))

    df_points = pd.DataFrame(
        data={"points": [Point(point) for point in points.keys()], "elev": points.values()}
    )

    # LINESTRINGS
    # --------------
    # merge the samples back for both start and end point
    gdf_ls = (
        gdf_ls.merge(df_points, left_on="point_1", right_on="points", how="left")
        .rename(columns={"elev": "elev_1"})
        .drop(columns=["points"])
        .merge(df_points, left_on="point_2", right_on="points", how="left")
        .rename(columns={"elev": "elev_2"})
        .drop(columns=["points"])
    )

    # distance is dx
    gdf_ls["distance"] = gpd.GeoSeries(gdf_ls["point_1"]).distance(gpd.GeoSeries(gdf_ls["point_2"]))

    # calculate mean_elevation and slope. Do not allow zero slope
    gdf_ls["mean_elevation"] = gdf_ls[["elev_1", "elev_2"]].mean(axis=1)
    gdf_ls["slope"] = abs((gdf_ls["elev_2"] - gdf_ls["elev_1"]) / gdf_ls["distance"])
    gdf_ls["slope"] = np.where(gdf_ls["slope"] == 0, 1e-4, gdf_ls["slope"])
    gdf_ls = gdf_ls.drop(columns=["point_1", "point_2", "elev_1", "elev_2", "distance", "geometry"])

    # MULTILINESTRINGS
    # ----------------
    if mls_exists:
        gdf_mls = (
            gdf_mls.merge(df_points, left_on="point_1", right_on="points", how="left")
            .rename(columns={"elev": "elev_1"})
            .drop(columns=["points"])
            .merge(df_points, left_on="point_2", right_on="points", how="left")
            .rename(columns={"elev": "elev_2"})
            .drop(columns=["points"])
        )

        # get mean of all elevations in flowpath segments
        gb_sum = gdf_mls.groupby(model_cfg.flowpath_id)[["elev_1", "elev_2"]].sum()
        gb_count = gdf_mls.groupby(model_cfg.flowpath_id)[["elev_1", "elev_2"]].count()

        gb = pd.DataFrame(
            pd.Series(
                ((gb_sum["elev_1"] + gb_sum["elev_2"]) / (gb_count["elev_1"] + gb_count["elev_2"])),
                name="mean_elevation",
            )
        )

        # distance is dx
        gdf_mls["distance"] = get_point(gdf_mls.geometry, 0).distance(get_point(gdf_mls.geometry, -1))

        # slope: take slope of max length segment - select the segment
        gdf_mls_max = gdf_mls.groupby(model_cfg.flowpath_id)[["distance"]].max().reset_index(drop=False)
        gdf_mls_max["use_flag"] = 1
        gdf_mls = gdf_mls.merge(
            gdf_mls_max[[model_cfg.flowpath_id, "distance", "use_flag"]],
            on=[model_cfg.flowpath_id, "distance"],
            how="left",
        )
        gdf_mls = gdf_mls.loc[gdf_mls["use_flag"] == 1].copy()

        gdf_mls["slope"] = abs((gdf_mls["elev_2"] - gdf_mls["elev_1"]) / gdf_mls["distance"])
        gdf_mls["slope"] = np.where(gdf_mls["slope"] == 0, 1e-4, gdf_mls["slope"])

        gdf_mls = gdf_mls.merge(gb, on=model_cfg.flowpath_id, how="left")
        gdf_mls = gdf_mls.drop(
            columns=["point_1", "point_2", "elev_1", "elev_2", "distance", "geometry", "use_flag"]
        )

        gdf_lsmls = pd.concat([gdf_ls, gdf_mls])

    # merge back to main
    gdf = (
        gdf.merge(gdf_lsmls, on=model_cfg.flowpath_id, how="left")
        if mls_exists
        else gdf.merge(gdf_ls, on=model_cfg.flowpath_id, how="left")
    )

    del df_points, points, gdf_ls

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
    df_ref = df_ref.cast({pl.Float64: pl.Int64})
    df_refj = df.join(df_ref, on="fp_id", how="left")

    # tw and y
    df_tw = pl.read_parquet(model_cfg.tw_path).rename({"FEATUREID": "ref_fp_id", "prediction": "topwdth_ml"})
    df_y = pl.read_parquet(model_cfg.y_path).rename({"FEATUREID": "ref_fp_id", "prediction": "y_ml"})
    df_r = pl.read_parquet(model_cfg.r_path).rename({"FEATUREID": "ref_fp_id", "prediction": "r_ml"})

    # join predictions to fp with ref fp and calculate mean for fp_id (multiple ref_fp_id)
    df_tmp = df_refj.join(df_tw, on="ref_fp_id", how="full")
    df_meantw = df_tmp[["fp_id", "topwdth_ml"]].group_by("fp_id").mean()
    del df_tmp

    df_tmp = df_refj.join(df_y, on="ref_fp_id", how="full")
    df_meany = df_tmp[["fp_id", "y_ml"]].group_by("fp_id").mean()
    del df_tmp

    df_tmp = df_refj.join(df_r, on="ref_fp_id", how="full")
    df_meanr = df_tmp[["fp_id", "r_ml"]].group_by("fp_id").mean()
    del df_tmp

    # join back to original fp_id df
    df = df.join(df_meantw, on="fp_id", how="left")
    df = df.join(df_meany, on="fp_id", how="left")
    df = df.join(df_meanr, on="fp_id", how="left")

    del df_ref, df_refj

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
            topwdth_ml=row["topwdth_ml"],
            y=row["y"],
            total_da_sqkm=row["total_da_sqkm"],
        )
        # exclude attributes already calculated
        models.append(
            model.model_dump(
                exclude=["use_stream_order", "stream_order", "mean_elevation", "slope", "total_da_sqkm"]
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
