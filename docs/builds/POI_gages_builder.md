## POI/Gages Builder — Integration Guide

This document explains how to assemble a single, canonical gages layer by merging USGS (active + discontinued) and partner gage sources (TXDOT, CADWR, ENVCA, NWM calibration sets, AK/HI/PR supplements). You’ll download the source files from the S3 bucket (see below), place them in your local user directory.
### Download source files:
Download the entire set of source files from the project’s S3 bucket (Data account) can be found here:

s3://hydrofabric-data/POI_gage_sources/gages/

Before running, download source files from the S3 bucket into your local user directory,
e.g. /home/<you>/Documents/hydrofabric-builds/data/gages/, preserving the expected subfolder structure:

    data/
     └─ gages/
         ├─ usgs_gages_discontinued/      # USGS KMZ bundles
         ├─ usgs_active_gages/            # USGS active shapefiles
         ├─ TXDOT_gages/TXDOT_gages.txt
         ├─ gage_xy.csv                   # CADWR/ENVCA/AK/HI/PR etc.
         └─ all_gages_gpkgs/nwm_calib_gages.txt
         ├─ nldi_upstream_basins.gpkg     # USGS API, optional
         ├─ CIROH_UA/gage_area.csv        # CIROH csv file for upstream area
`

## What you’ll get (final dataset)
* Live USGS gages: ~9,000
* Discontinued USGS gages: ~14,700 (some overlap with “live”)
* TXDOT gages: ~77
* CADWR gages: ~25
* ENVCA gages: ~27
* NWM calibration gages list: ~1640
* Alaska, Puerto Rico, Hawaii: ~30
* Total: 26,754 point features

## How it works (high-level):

The gages task in hydrofabric-builds coordinates these steps:

### 1- USGS discontinued (KMZ)

* build_usgs_gages_from_kmz() scans a folder of USGS KML-in-KMZ files and extracts points + site_no.

* The source files are already provided. However, they can be downloaded by  users from this [link](https://waterwatch.usgs.gov/?m=stategage).

* Result becomes the initial gages GeoDataFrame.

### 2-USGS live (SHP)

* merge_usgs_shapefile_into_gages() reads several USGS shapefiles and adds missing sites.

* The shapefiles are already provided as source files. However, the user can download them form this [link](https://waterwatch.usgs.gov/index.php?id=wwds_shp).

* With update_existing=True, it overwrites geometry/name/state for matching site_no when the shapefile has better data.

### 3- TXDOT RDB/TXT

* txdot_read_file() parses the RDB-style text.

* merge_minimal_gages() maps: geometry→geometry, site_no→site_no, station_nm→name_raw, and fills everything else with "-".

* With update_existing=True, coordinates/names are refreshed for matches.

### 4- Generic XY CSV (CADWR, ENVCA, AK/HI/PR, misc.)

* merge_gage_xy_into_gages() maps gageid→site_no, (lon,lat)→geometry.

* Appends new sites and (optionally) updates geometry for existing ones.

* You can exclude IDs (e.g., two Alaska gages outside the domain) if desired.

### 5- NWM Calibration gages

* The list is checked against gages.

* Any missing USGS-style IDs are fetched from the NWIS Site Service via add_missing_usgs_sites() and appended.

* Non-USGS IDs (e.g., Canadian IDs with letters) are reported and skipped.

### 6) Finding upstream area for USGS gages using API (NLDI)

* The upstream area are read from USGS API and are compared with total upstream area calculated in hydrofabric (NHF). It is a method to make sure the flowpaths are assigned cor recently to gages.

* The USGS Network Linked Data Index (NLDI) API is available in this [link](https://api.water.usgs.gov/nldi/swagger-ui/index.html?configUrl=/api/nldi/v3/api-docs/swagger-config#/linked-data-controller/getDataSources).

### 7) Add upstream basin area from CIROH-UA csv file to gages

* the upstream areas are read for CIROH csv file and added to gages wherever USGS API does not provide upstream area values. It adds ~ 10000 upstream area values to the list.
* The file is accessible from the following [link](https://github.com/CIROH-UA/community_hf_patcher/blob/main/scripts/hydro/gages/gage_area.csv).

### 8) Assign flowpath to gages

* For each USGS gage, we assigned a corresponding flowpath in the NHF product using a hierarchical procedure based on upstream drainage area and spatial proximity. First, we queried the USGS NLDI API to obtain the reported upstream drainage area for all gages with available information and compared these values to the upstream area of candidate NHF flowpaths, assigning the gage to the flowpath whose upstream area most closely matched the USGS value. For gages where NLDI drainage area was unavailable, we instead used upstream area estimates from the CIROH community_hf_patcher dataset and repeated the same area-matching procedure. If neither the USGS NLDI nor CIROH-based upstream areas produced a sufficiently close match to the NHF upstream area, we then applied a purely spatial method, assigning the gage to the nearest NHF flowpath within a 1,000 m search radius.

### 9) drop the columns we don't need
`keep_cols = ["site_no", "geometry", "status", "USGS_basin_km2", "fp_id", "method_fp_to_gage"]
`
### 10) Write final output

* Exports a single GeoPackage with the unified usgs_gages layer.
