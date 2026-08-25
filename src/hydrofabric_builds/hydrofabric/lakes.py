from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd

from hydrofabric_builds.hydrofabric.utils import _crosswalk_nexus, _crosswalk_reference

logger = logging.getLogger(__name__)


def complete_lakes(hf_path: Path, nwm_lakes_path: Path, fields: list[str]) -> gpd.GeoDataFrame:
    """Complete requested columns. A new nhf ID, fp_id, and geometry are included by default.

    Parameters
    ----------
    nwm_lakes_path : Path
        Path to input hydrofabric gpkg
    fields : list[str]
        Fields to keep in final layer

    """
    # create a table ID
    gdf_lakes = gpd.read_file(nwm_lakes_path)
    gdf_lakes["nhf_lake_id"] = range(1, gdf_lakes.shape[0] + 1)

    # crosswalk to reference and nexues
    gdf_lakes = _crosswalk_reference(hf_path=hf_path, gdf=gdf_lakes)
    gdf_lakes = _crosswalk_nexus(hf_path=hf_path, gdf=gdf_lakes)

    # add nulls for any missing columns requested
    for f in fields:
        if f not in gdf_lakes.columns:
            gdf_lakes[f] = None

    out_columns = (
        ["nhf_lake_id", "ref_fp_id", "fp_id", "virtual_fp_id", "dn_nex_id", "dn_virtual_nex_id", "div_id"]
        + fields
        + ["geometry"]
    )

    # select final attribute list
    return gdf_lakes[out_columns]
