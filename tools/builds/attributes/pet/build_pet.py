import argparse
import os
from pathlib import Path

import numpy as np
import rioxarray
import xarray as xr

from tools.builds.attributes.schemas.attributes import VegetationTypes, VegetationTypesCombined

params = {
    "veg_height": (2.0, 10.0, 0.20),
    "zero_plane": (1.3, 7.0, 0.13),
    "momentum_transfer": (0.26, 1.3, 0.13),
    "heat_transfer": (0.03, 0.13, 0.003),
    "longwave_emissivity": (0.95, 0.7, 0.10),
    "shortwave_albedo": (0.40, 0.15, 0.25),
}


moderate = [
    VegetationTypes.DRYLAND_CROPLAND_AND_PASTURE.value,
    VegetationTypes.IRRIGATED_CROPLAND_AND_PASTURE.value,
    VegetationTypes.MIXED_DRYLAND_IRRIGATED_CROPLAND_AND_PASTURE.value,
    VegetationTypes.CROPLAND_GRASSLAND_MOSAIC.value,
    VegetationTypes.CROPLAND_WOODLAND_MOSAIC.value,
    VegetationTypes.GRASSLAND.value,
    VegetationTypes.SHRUBLAND.value,
    VegetationTypes.MIXED_SHRUBLAND_GRASSLAND.value,
    VegetationTypes.SAVANNA,
    VegetationTypes.HERBACEOUS_WETLAND.value,
    VegetationTypes.WOODED_TUNDRA.value,
    VegetationTypes.HERBACEOUS_TUNDRA.value,
    VegetationTypes.MIXED_TUNDRA.value,
]

forested = [
    VegetationTypes.DECIDUOUS_BROADLEAF_FOREST.value,
    VegetationTypes.DECIDUOUS_NEEDLELEAF_FOREST.value,
    VegetationTypes.EVERGREEN_BROADLEAF_FOREST.value,
    VegetationTypes.EVERGREEN_NEEDLELEAF_FOREST.value,
    VegetationTypes.MIXED_FOREST.value,
    VegetationTypes.WOODED_WETLAND.value,
]

sparse = [
    VegetationTypes.URBAN_AND_BUILT_UP_LAND.value,
    VegetationTypes.BARREN_OR_SPARSELY_VEGETATED.value,
]

no_veg = [
    VegetationTypes.WATER_BODIES.value,
    VegetationTypes.SNOW_OR_ICE.value,
    VegetationTypes.PLAYA.value,
    VegetationTypes.LAVA.value,
    VegetationTypes.BARE_GROUND_TUNDRA.value,
]


def built_pet(ivgtyp_path: Path, ivgtyp_file: Path, output_grouped: bool) -> None:
    """Build PET rasters based on vegetation type

    Parameters
    ----------
    data_dir : Path
        Path to directory containing the IVGTYP raster
    filename : Path
        The filename of the IVGTYP raster
    output_grouped : bool
        if true, output a raster showing the vegetation type grouping

    Returns
    -------
    None
    """
    # read IVGTYP raster
    ivgtyp_raster = rioxarray.open_rasterio(os.path.join(ivgtyp_path, ivgtyp_file))

    if np.isnan(ivgtyp_raster.rio.nodata):
        ivgtyp_raster = ivgtyp_raster.rio.write_nodata(-9999)
    ivgtyp_raster = ivgtyp_raster.fillna(ivgtyp_raster.rio.nodata)
    ivgtyp_raster = ivgtyp_raster.astype(np.int32)

    # group IVGTYP vegetation types in to moderate, forested, sparse and NA categories
    grouped = xr.apply_ufunc(group_types, ivgtyp_raster, vectorize=True)
    grouped = grouped.fillna(ivgtyp_raster.rio.nodata)
    grouped = grouped.astype(np.int32)

    # loop through PET parameters and assign values based on grouped vegetation type
    for key, value in params.items():
        new_array = (
            grouped.where(grouped != 1, value[0])
            .where(grouped != 2, value[1])
            .where(grouped != 3, value[2])
            .where(grouped != 4, ivgtyp_raster.rio.nodata)
        )

        # add geospatial attributes and write raster file
        new_array = new_array.assign_coords(x=ivgtyp_raster.x, y=ivgtyp_raster.y, band=ivgtyp_raster.band)
        new_array = new_array.rio.write_crs(ivgtyp_raster.rio.crs)
        new_array.rio.write_transform(ivgtyp_raster.rio.transform(), inplace=True)
        new_array.attrs = ivgtyp_raster.attrs
        new_array.rio.write_nodata = ivgtyp_raster.rio.nodata
        filename = os.path.join(ivgtyp_path, f"{key}.tif")
        new_array.rio.to_raster(filename, tiled=True, compress="deflate")

    # if true, output a grouped vegetation type raster if needed for checking the PET parameter rasters.
    if output_grouped:
        grouped = grouped.assign_coords(x=ivgtyp_raster.x, y=ivgtyp_raster.y, band=ivgtyp_raster.band)
        grouped.rio.write_crs(ivgtyp_raster.rio.crs, inplace=True)
        grouped.rio.write_transform(ivgtyp_raster.rio.transform(), inplace=True)
        grouped.attrs = ivgtyp_raster.attrs
        grouped.rio.write_nodata = ivgtyp_raster.rio.nodata
        filename = os.path.join(ivgtyp_path, "grouped.tif")
        grouped.rio.to_raster(filename, tiled=True, compress="deflate")


def group_types(vegtype: int) -> VegetationTypesCombined:
    """Group vegetation types to moderate,forest,sparse, or NA

    Parameters
    ----------
    vegtype : int
        vegetation type from IVGTYP raster

    Returns
    -------
    VegetationTypesCombined
        combined vegetation type
    """
    if vegtype in moderate:
        return VegetationTypesCombined.MODERATE
    elif vegtype in forested:
        return VegetationTypesCombined.FOREST
    elif vegtype in sparse:
        return VegetationTypesCombined.SPARSE
    elif vegtype in no_veg:
        return VegetationTypesCombined.NA
    else:
        return VegetationTypesCombined.NA


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A script create PET parameters based on vegetation type")
    parser.add_argument(
        "--data_dir",
        type=str,
        help="Path to directory containing the input vegetation type raster and where the output rasters will be written.",
    )
    parser.add_argument(
        "--veg_type_file",
        type=str,
        help="the vegetation type raster filename, e.g., IVGTYP.tif",
    )
    parser.add_argument(
        "--output_grouped",
        action="store_true",
        help="output raster showing the vegetation grouping",
    )

    args = parser.parse_args()


built_pet(ivgtyp_path=args.data_dir, ivgtyp_file=args.veg_type_file, output_grouped=args.output_grouped)
