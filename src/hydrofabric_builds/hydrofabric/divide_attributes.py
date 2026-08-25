"""Functions to compute zonal statistics for divides."""

import logging
import multiprocessing
import shutil
from pathlib import Path
from time import perf_counter

import geopandas as gpd
import pandas as pd
import xarray as xr
from exactextract import exact_extract

from hydrofabric_builds.hydrofabric.graph import _validate_and_fix_geometries
from hydrofabric_builds.schemas.hydrofabric import (
    DivideAttributeConfig,
    DivideAttributesModelConfig,
    get_operation,
)

logger = logging.getLogger(__name__)


def _vpu_splitter(model_cfg: DivideAttributesModelConfig) -> list[Path]:
    """Splits the model config's divides file into separate file per VPU for parallel processing

    Temp files deleted upon script completion

    Parameters
    ----------
    model_cfg : DivideAttributeModelConfig
        Pydantic model for full model config

    Returns
    -------
    list[Path]
        list of temp VPU files created to be used in model_cfg's `divides_path_list`
    """
    gdf = gpd.read_file(model_cfg.hf_path, layer="divides")
    gdf = _validate_and_fix_geometries(gdf, geom_type="divides")
    vpus = gdf["vpu_id"].unique()
    for vpu in vpus:
        gdf_temp = gdf[gdf["vpu_id"] == vpu].copy()
        gdf_temp.to_file(
            model_cfg.tmp_dir / f"vpu_{vpu}.gpkg", layer="divides", driver="GPKG", overwrite=True
        )
        del gdf_temp

    return [model_cfg.tmp_dir / f"vpu_{vpu}.gpkg" for vpu in vpus]


def _calculate_attribute(
    model_cfg: DivideAttributesModelConfig,
    attribute_cfg: DivideAttributeConfig,
    alt_divides_path: Path | None = None,
) -> None:
    """Read divides, data, and calculate. Save to disk for future assembly

    Use alt_divides_path to specify a path other than model_cfg main divides path

    Parameters
    ----------
    model_cfg : DivideAttributeModelConfig
        Pydantic model for full model config
    attribute_cfg : DivideAttributeConfig
        Pydantic model for divide attribute
    alt_divides_path : Path | None, optional
        specify a path other than model_cfg main divides path, by default None
    """
    t0 = perf_counter()
    if alt_divides_path:
        divides = gpd.read_file(alt_divides_path, layer="divides")
        logger.info(f"Calculating {attribute_cfg.field_name} for {alt_divides_path}")
    else:
        divides = gpd.read_file(model_cfg.hf_path, layer="divides")
        logger.info(f"Calculating for {attribute_cfg.file_name} for {model_cfg.hf_path}")

    ds = xr.open_dataarray(attribute_cfg.file_name)

    df = exact_extract(
        ds,
        divides,
        ops=get_operation(attribute_cfg.agg_type.value),
        include_cols=[model_cfg.divide_id],
        include_geom=False,
        output="pandas",
    )

    if attribute_cfg.agg_type.value == "quantile_dist":
        rng = range(10, 101, 10)
        df = df.rename(
            columns=dict(
                zip([f"quantile_{num}" for num in rng], [f"twi_q{num}" for num in rng], strict=False)
            )
        )
    elif attribute_cfg.agg_type.value == "quartile_dist":
        rng = range(25, 101, 25)
        df = df.rename(
            columns=dict(
                zip([f"quantile_{num}" for num in rng], [f"twi_q{num}" for num in rng], strict=False)
            )
        )
    else:
        df = df.rename(columns={attribute_cfg.agg_type.value: attribute_cfg.field_name})

    df.to_parquet(
        attribute_cfg.tmp,
        compression="snappy",
    )
    del df, ds, divides
    logger.debug(f"{attribute_cfg.tmp}: {round((perf_counter() - t0) / 60, 2)} min")


def _concatenate_attributes(model_cfg: DivideAttributesModelConfig) -> None:
    """For non-parallel use: Read all saved parquets and aggregates to single divide gdf

    Parameters
    ----------
    model_cfg : DivideAttributeModelConfig
        Pydantic model for full model config
    """
    t0 = perf_counter()
    gdf = gpd.read_file(model_cfg.hf_path, layer="divides")
    tif_attributes = [cfg for cfg in model_cfg.attributes if ".tif" in cfg.file_name.name]

    for cfg in tif_attributes:
        df_temp = pd.read_parquet(cfg.tmp)
        gdf = gdf.merge(df_temp, on=model_cfg.divide_id, how="left")
        del df_temp

    if "twi_q50_x" in gdf.columns:
        gdf = gdf.drop(columns={"twi_q50_y", "twi_q100_y"})
        gdf = gdf.rename(columns={"twi_q50_x": "twi_q50", "twi_q100_x": "twi_q100"})

    gdf["centroid"] = gdf["geometry"].centroid
    gdf["centroid"] = gdf["centroid"].to_crs(4326)
    gdf["lat"] = gdf["centroid"].y
    gdf["lon"] = gdf["centroid"].x
    gdf = gdf.drop(columns=["centroid"])

    gdf.to_file(model_cfg.hf_path, layer="divides")
    del gdf
    logger.debug(f"{model_cfg.hf_path}: {round((perf_counter() - t0) / 60, 2)} min")


def _merge_divide_attributes_parallel(model_cfg: DivideAttributesModelConfig) -> None:
    """If divides were processed in parallel, merge the attributes for each divide and save to gpkg

    Parameters
    ----------
    model_cfg : DivideAttributeModelConfig
        Pydantic model for full model config
    """
    n_divides = len(model_cfg.divides_path_list)  # type: ignore[arg-type]

    # looks like: [[tmp_slope_1.parquet, tmp_slope_2.parquet], [tmp_soil_1.parquet, tmp_soil_2.parquet]]
    list_files = []
    tif_attrs = [attr for attr in model_cfg.attributes if "tif" in attr.file_name.name]
    for attr in tif_attrs:
        list_files.append(
            [model_cfg.tmp_dir / f"tmp_{attr.field_name}_{i}.parquet" for i in range(n_divides)]
        )

    # itterate through each divides file and join the corresponding attribute parquet
    for i, divides in enumerate(model_cfg.divides_path_list):  # type: ignore[arg-type]
        gdf = gpd.read_file(divides, layer="divides")
        for list_f in list_files:
            df_temp = pd.read_parquet(list_f[i])
            gdf = gdf.merge(df_temp, on=model_cfg.divide_id, how="left")
        tmp_file = model_cfg.tmp_dir / f"tmp_{divides.name}"
        gdf.to_file(tmp_file, layer="divides", driver="GPKG", overwrite=True)
        logger.debug(f"{tmp_file} created")


def _concatenate_divides_parallel(model_cfg: DivideAttributesModelConfig) -> None:
    """Concatenate the set of temporary divide files with attributes to final file

    Parameters
    ----------
    model_cfg : DivideAttributeModelConfig
        Pydantic model for full model config
    """
    t0 = perf_counter()
    file_list = [model_cfg.tmp_dir / f"tmp_{divides.name}" for divides in model_cfg.divides_path_list]  # type: ignore[union-attr]
    gdfs = [gpd.read_file(f, layer="divides") for f in file_list]
    gdf = pd.concat(gdfs, axis=0, ignore_index=True)
    if "twi_q50_x" in gdf.columns:
        gdf = gdf.drop(columns={"twi_q50_y", "twi_q100_y"})
        gdf = gdf.rename(columns={"twi_q50_x": "twi_q50", "twi_q100_x": "twi_q100"})

    gdf["centroid"] = gdf["geometry"].centroid
    gdf["centroid"] = gdf["centroid"].to_crs(4326)
    gdf["lat"] = gdf["centroid"].y
    gdf["lon"] = gdf["centroid"].x
    gdf = gdf.drop(columns=["centroid"])

    gdf.to_file(model_cfg.hf_path, layer="divides", overwrite=True)
    logger.debug(f"{model_cfg.hf_path}: {round((perf_counter() - t0) / 60, 2)} min")


def _prep_multiprocessing_rasters(
    processes: int, cfg: DivideAttributeConfig, n_divides: int, tmp_dir: Path
) -> list[Path]:
    """Create copies of rasters for multiprocessing. Ensures all processes can start without read conflicts.

    Parameters
    ----------
    processes : int
        number processes
    cfg : DivideAttributeConfig
        config of current divide attribute
    n_divides : int
        number divides files
    tmp_dir : Path
        temp directory to save to

    Returns
    -------
    list[Path]
        list of raster paths
    """
    raster_list = []
    copies = processes if processes < n_divides else n_divides

    for i in range(copies):
        # Looks like: /tmp_path/file_name_{i}.tif
        new_path = tmp_dir / f"{cfg.file_name.name.split('.')[0]}_{i}.tif"
        shutil.copy(cfg.file_name, new_path)
        raster_list.append(new_path)
        logger.debug(f"Copied raster to {new_path}")

    return raster_list


def _prep_multiprocessing_configs(
    processes: int, cfg: DivideAttributeConfig, n_divides: int, tmp_dir: Path, raster_list: list[Path]
) -> list[DivideAttributeConfig]:
    """Prepare attribute config files for multiprocessing

    Parameters
    ----------
    processes : int
        number processes
    cfg : DivideAttributeConfig
       config of current divide attribute
    n_divides : int
        number divides files
    tmp_dir : Path
        temp directory to save to
    raster_list : list[Path]
        list of duplicated rasters for multiprocessing

    Returns
    -------
    list[DivideAttributeConfig]
        list of updated divide attribute configs
    """
    # set up tmp paths and raster copies for each divide output
    tmp_c_divides = [cfg for _ in range(n_divides)]
    c_divides = []
    for i, c in enumerate(tmp_c_divides):
        _c = c.model_copy()
        _c.tmp = tmp_dir / f"tmp_{_c.field_name}_{i}.parquet"

        # get the duplicated raster file name based on amount of processes. +/- 1 to handle python 0-indexing
        # Note: There still may be some waiting of processes but this ensures all processes can start
        _c.file_name = raster_list[((i + 1) % processes) - 1]
        c_divides.append(_c)

    return c_divides


def _calculate_glaciers(
    model_cfg: DivideAttributesModelConfig, glaciers_attribute: DivideAttributeConfig
) -> None:
    """Calculate glacier percent in each divide

    Overwrites HF divides on output.

    Parameters
    ----------
    model_cfg : DivideAttributesModelConfig
       Pydantic model for full model config
    glaciers_attribute : DivideAttributeConfig
        Pydantic model for glacier attribute
    """
    logger.info("Calculating glaciers")
    gdf_gl = gpd.read_parquet(glaciers_attribute.file_name)
    gdf_gl["geometry"] = gdf_gl["geometry"].to_crs(model_cfg.crs)

    gdf_div = gpd.read_file(model_cfg.hf_path, layer="divides")
    gdf_div["area_hf"] = gdf_div.area

    # intersect divides and glacier polygons
    gdf_int = gdf_div.overlay(gdf_gl, how="intersection")

    # dissolve intersection by div_id so there is one glacier polygon per divide
    # reset index to keep `div_id` column
    gdf_int_dissolve = gdf_int.dissolve(model_cfg.divide_id)
    gdf_int_dissolve = gdf_int_dissolve.reset_index(drop=False)

    # drop all columns except geometry and divide_id
    # rename secondary geom column
    gdf_int_dissolve = gdf_int_dissolve[["geometry", model_cfg.divide_id]]
    gdf_int_dissolve["area_gl"] = gdf_int_dissolve.area
    gdf_int_dissolve = gdf_int_dissolve.rename(columns={"geometry": "geom_int"})
    logger.debug("Finished dissolving glaciers and divides")

    # merge divides with dissolved
    gdf_div = gdf_div.merge(gdf_int_dissolve, on=model_cfg.divide_id, how="left")

    # calculate % glacier
    gdf_div[glaciers_attribute.field_name] = (gdf_div["area_gl"] / gdf_div["area_hf"] * 100).round(2)
    gdf_div.loc[gdf_div[glaciers_attribute.field_name].isnull(), glaciers_attribute.field_name] = 0
    gdf_div = gdf_div.drop(columns=["area_hf", "area_gl", "geom_int"])

    # write out
    gdf_div.to_file(model_cfg.hf_path, layer="divides", driver="GPKG", overwrite=True)
    logger.info("Glaciers complete")


def _glaciers(model_cfg: DivideAttributesModelConfig) -> None:
    """Calculate the percent glacier in each divide

    Overwrites HF divides on output.

    Parameters
    ----------
    model_cfg : DivideAttributesModelConfig
        Pydantic model for full model config
    """
    try:
        glaciers_attribute = [
            cfg
            for cfg in model_cfg.attributes
            if ("glacier" in cfg.file_name.name) or ("glims" in cfg.file_name.name)
        ][0]

        _calculate_glaciers(model_cfg, glaciers_attribute)

    except IndexError:
        logger.info("Glaciers not found in attributes - skipping.")
        return


def _calculate_groundwater(
    model_cfg: DivideAttributesModelConfig, gw_attribute: DivideAttributeConfig
) -> None:
    """Reads and joins groundwater data

    ***Note***
    All fields in gw.csv will be joined. Does not honor field name in attribute config.

    Parameters
    ----------
    model_cfg : DivideAttributesModelConfig
        Pydantic model for full model config
    gw_attribute : DivideAttributeConfig
        Pydantic model for groundwater attribute
    """
    gdf_div = gpd.read_file(model_cfg.hf_path, layer="divides")
    df_gw = pd.read_csv(gw_attribute.file_name)
    gdf_div = gdf_div.merge(df_gw, on=model_cfg.divide_id, how="left")
    gdf_div.to_file(model_cfg.hf_path, layer="divides", driver="GPKG", overwrite=True)


def _groundwater(model_cfg: DivideAttributesModelConfig) -> None:
    """Test if groundwater is available and joins

    Groundwater attribute should include 'gw' or 'groundwater' in file name

    ***Note***
    All fields in gw.csv will be joined. Does not honor field name in attribute config.

    Parameters
    ----------
    model_cfg : DivideAttributesModelConfig
        Pydantic model for full model config
    """
    try:
        gw_attribute = [
            cfg
            for cfg in model_cfg.attributes
            if ("gw" in cfg.file_name.name) or ("groundwater" in cfg.file_name.name)
        ][0]
        logger.info(f"Joining groundwater from {gw_attribute.file_name}")
        _calculate_groundwater(model_cfg, gw_attribute)

    except IndexError:
        logger.info("Groundwater not found in attributes - skipping.")
        return


def _teardown(tmpdir: Path) -> None:
    """Delete tmp paths from tmp directory

    Parameters
    ----------
    tmpdir : Path
        tmp directory
    """
    path_list = tmpdir.glob("*")
    for f in path_list:
        f.unlink(missing_ok=True)


def divide_attributes_pipeline_single(model_cfg: DivideAttributesModelConfig) -> None:
    """A pipeline to calculate divide attributes for a single divides file

    Parameters
    ----------
    config_yaml : str
        Path to Divide Attributes Model Config
    """
    t0 = perf_counter()

    tif_attributes = [cfg for cfg in model_cfg.attributes if ".tif" in cfg.file_name.name]
    for cfg in tif_attributes:
        _calculate_attribute(model_cfg, cfg)

    _concatenate_attributes(model_cfg)

    # optional glaciers and groundwater - overwrites file if calculated
    _glaciers(model_cfg)
    _groundwater(model_cfg)

    _teardown(model_cfg.tmp_dir)
    logger.info(f"divide attributes total time: {round(((perf_counter() - t0) / 60), 2)} min")


def divide_attributes_pipeline_parallel(model_cfg: DivideAttributesModelConfig, processes: int) -> None:
    """A pipeline to calculate divide attributes for multiple divides files in parallel

    Notes
    -----
    - `split_vpu` or `divides_path_list` must be in config
    `split_vpu` will split `divides_path` into separate files to run in parallel
    `divides_path_list` can be any list of divides files to be run in parallel

    - Attributes are not run in parallel. Parallel running is for multiple divides files for each attribute.

    - tmp files will be deleted upon successful completion

    - for each process, a raster copy will be made to ensure all processes can start without conflicting reads


    Parameters
    ----------
    config_yaml : str
        Path to Divide Attributes Model Config
    processes : int
        number CPU processes
    """
    try:
        t0 = perf_counter()
        tmp_dir = model_cfg.tmp_dir
        tmp_dir.mkdir(parents=True, exist_ok=True)

        # Confirm model should be run in parallel
        if not model_cfg.split_vpu and not model_cfg.divides_path_list:
            raise ValueError(
                "Config did not include instructions to split by VPU (`split_vpu`) or a list of divides (`divides_path_list`)"
                " The model cannot be run in parallel. Either run `divide_attributes_pipeline_single` or add `split_vpu` or"
                "`divides_path_list` keys"
            )
        # creates gpkg for each VPU
        if model_cfg.split_vpu:
            model_cfg.divides_path_list = _vpu_splitter(model_cfg)

        # processing loop: 1 attribute for multiple divides layers at a time
        tif_attributes = [cfg for cfg in model_cfg.attributes if ".tif" in cfg.file_name.name]
        for cfg in tif_attributes:
            n_divides = len(model_cfg.divides_path_list)  # type: ignore[arg-type]

            # prep rasters and config files
            raster_list = _prep_multiprocessing_rasters(
                processes=processes, cfg=cfg, n_divides=n_divides, tmp_dir=tmp_dir
            )
            config_list = _prep_multiprocessing_configs(
                processes=processes, cfg=cfg, n_divides=n_divides, tmp_dir=tmp_dir, raster_list=raster_list
            )

            # multiprocess each divide
            args = zip(
                [model_cfg for _ in range(n_divides)],
                config_list,
                model_cfg.divides_path_list,  # type: ignore[arg-type]
                strict=False,
            )
            with multiprocessing.Pool(processes=processes) as pool:
                pool.starmap(_calculate_attribute, args)

            for f in raster_list:
                f.unlink(missing_ok=True)

        # concatenate the separate attributes to a single file per divide
        t1 = perf_counter()
        _merge_divide_attributes_parallel(model_cfg)
        t2 = perf_counter()
        logger.info(f"merge attributes for each divide: {round((t2 - t1) / 60, 2)} min")

        # concatenate and output file
        _concatenate_divides_parallel(model_cfg)
        logger.info(f"concatenate divides: {round((perf_counter() - t2) / 60, 2)} min")

        # optional glaciers and groundwater - overwrites file if calculated
        _glaciers(model_cfg)
        _groundwater(model_cfg)

        # delete all tmp files
        _teardown(tmp_dir)

        logger.info(f"divide attributes total time: {round(((perf_counter() - t0) / 60), 2)} min")

    except Exception as e:
        raise RuntimeError(f"Error creating divide attributes: {e}") from e

    finally:
        if not model_cfg.debug:
            _teardown(model_cfg.tmp_dir)
