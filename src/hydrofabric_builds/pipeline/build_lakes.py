"""Contains all code for building active NWM lakes in task"""

import logging
from pathlib import Path
from typing import Any, cast

from hydrofabric_builds.config import HFConfig
from hydrofabric_builds.helpers.flowpath_association import (
    associate_flowpaths_nearest_point,
    associate_flowpaths_polygon_outlet,
    join_attributes,
)
from hydrofabric_builds.hydrofabric.lakes import complete_lakes
from hydrofabric_builds.reservoirs.data_prep.hydraulics import populate_nwm_hydaulics

logger = logging.getLogger(__name__)


def build_lakes(**context: dict[str, Any]) -> dict[str, Any]:
    """Builds lakes layer from all NWM lake sources

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

    """
    cfg = cast(HFConfig, context["config"])

    # Preprocess lakes by associating with flowpaths if requested or if processed path does not exist
    if cfg.lakes.associate_flowpaths or not cfg.lakes.processed_path.exists():
        # Use nearest point association method
        if cfg.lakes.flowpath_association_method == "nearest_point":
            logger.info("Associating flowpath with points")
            gdf = associate_flowpaths_nearest_point(
                points_path=cfg.lakes.input_path,
                flowpaths_path=Path(cfg.build.reference_flowpaths_path),
                search_radius_m=cfg.lakes.search_radius_m,
                point_id=cfg.lakes.id_field,
                flowpath_id="flowpath_id",
                flowpath_id_out_field="ref_fp_id",
                points_layer=cfg.lakes.input_layer,
            )
        # use polygon flowpath outlet method
        elif cfg.lakes.flowpath_association_method == "polygon_outlet":
            logger.info("Associating flowpaths with polygons")
            gdf = associate_flowpaths_polygon_outlet(
                polygon_path=cfg.lakes.input_path,
                flowpaths_path=Path(cfg.build.reference_flowpaths_path),
                search_radius_m=cfg.lakes.search_radius_m,
                min_preferred_intersection_len_m=cfg.lakes.min_preferred_intersection_len_m,
                flowpath_id="flowpath_id",
                flowpath_id_out_field="ref_fp_id",
                polygon_layer=cfg.lakes.input_layer,
            )
            if cfg.lakes.attrib_src_path:
                gdf = join_attributes(
                    gdf,
                    attrib_dst_key=cfg.lakes.id_field,
                    attrib_src_path=cfg.lakes.attrib_src_path,
                    attrib_src_layer=cfg.lakes.attrib_src_layer,
                    attrib_src_key=cfg.lakes.attrib_src_key,
                    attrib_src_fields=cfg.lakes.fields.copy(),
                    rename=True,
                )

        # invalid method
        else:
            raise ValueError("Config contained invalid Lakes flowpath association method")

        # Save pre-processed file
        cfg.lakes.processed_path.parent.mkdir(parents=True, exist_ok=True)
        gdf.to_file(cfg.lakes.processed_path, overwrite=True, driver="GPKG")
        del gdf

    # Populate hydraulics if requested - use flowpath associated file
    # If hydaulics is skipped, columns will be assumed to be present in processed file or added as null
    if cfg.lakes.populate_hydaulics:
        gdf = populate_nwm_hydaulics(cfg.lakes.processed_path)
        gdf.to_file(cfg.lakes.processed_path, overwrite=True, driver="GPKG")

    # Complete the nwm_lakes file with requested columns and crosswalk to fps/virtual fp/nexus/vnexus
    gdf = complete_lakes(
        hf_path=cfg.output_file_path, nwm_lakes_path=cfg.lakes.processed_path, fields=cfg.lakes.fields
    )

    # Save nwm_lakes layer to NHF
    gdf.to_file(cfg.output_file_path, layer="lakes", driver="GPKG", overwrite=True)

    return {"lakes": "done"}
