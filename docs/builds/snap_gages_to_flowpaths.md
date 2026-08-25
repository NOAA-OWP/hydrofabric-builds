### gages to flowpaths assignment (area-match method)
This doc explains the following script:
`examples/snap_gages_to_flowpaths.py
`

This script assigns USGS gages (points) to reference flowpaths by comparing the basin area at each gage to the totdasqkm attribute on nearby flowpaths. It can run serially or in parallel.

### What it does (high level)

#### 1) Prepares candidates per gage (vectorized)

* Reprojects flowpaths to EPSG:5070 (good for buffering in CONUS).

* Buffers each gage by --buffer-m meters and finds all intersecting flowpaths.

* Stores candidate row indices for each gage (no Python loop here).

#### 2) Scores candidates (serial or parallel)

* For each gage, fetches an upstream basin from USGS NLDI:

  * First by site_no (NLDI nwissite/USGS-<site>/basin).

  * If missing, by geographic position (NLDI position → basin).

* Computes basin area (km²) in an equal-area projection (EPSG:6933). (Side note: EPSG:6933 was used to be able to calculate area for divides (watersheds) shapefiles in square kilometers across OCONUS. There might be small errors in area calculation with this projection, however, because it is a comparison between the area of two shapefiles, the errors cancel out each other mostly. please look at this [link](https://nsidc.org/data/user-resources/help-center/guide-ease-grids) for more information)

* Among the intersecting candidates, picks the flowpath whose totdasqkm is closest to the basin area, accepting only if the relative difference ≤ 15% (configurable in code).

* Records flowpath_id, the basin_km2, and basin_source (nldi_site or nldi_position).

### Inputs & Outputs

#### Inputs

* Flowpaths GeoPackage (e.g., sc_reference_fabric.gpkg)

  * Must include: geometry, flowpath_id, totdasqkm

  * Must have a valid CRS (any; script will reproject to EPSG:5070 internally).

* Gages GeoPackage (e.g., usgs_gages_all_conus_AK_Pr.gpkg)

  * Layer must include: geometry (EPSG:4326 expected) and site_no (string).

### Output

* A new GeoPackage (path set via --output) containing the same layer name as input gages layer.

* Adds/updates columns:

  * flowpath_id (assigned flowpath or left empty if no acceptable match)

  * basin_km2 (area used for matching)

  * basin_source (nldi_site / nldi_position / None)

### Usage
#### Serial run (default)
`python3 examples/snap_gages_to_flowpaths.py \
  --flowpaths /path/to/sc_reference_fabric.gpkg \
  --gages     /path/to/usgs_gages_all_conus_AK_Pr.gpkg \
  --buffer-m  500`

#### Parallel run
`python3 examples/snap_gages_to_flowpaths.py \
  --flowpaths /path/to/sc_reference_fabric.gpkg \
  --gages     /path/to/usgs_gages_all_conus_AK_Pr.gpkg \
  --parallel --max-workers 4`

#### Options (CLI)

* --flowpaths (required): path to flowpaths GPKG

* --flowpaths-layer (default reference_flowpaths): layer name inside flowpaths GPKG

* --gages (required): path to gages GPKG

* --gages-layer (default usgs_gages): layer name inside gages GPKG

* --buffer-m (default 500): search radius (meters) around each gage

* --parallel: enable multiprocessing with per-process flowpath init

* --max-workers (optional): number of workers when parallel is enabled

* --output (optional): output path; default is <gages_dir>/usgs_gages_all_conus_AK_Pr_flowpath_id.gpkg
