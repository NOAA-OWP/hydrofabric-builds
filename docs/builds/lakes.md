# Lakes vs Waterbodies

Separate "lakes" and "waterbodies" layers are currently retained to reflect separate workstreams. These workstreams will be fused in future work for a single "lakes" layer.

## Lakes
The "lakes" layer is built to retain all active NWM lakes. These are derived from `nwm_lakes.gpkg` polygons and Hydrofabric v2.2 attributes. Lakes are spatially placed at the most downstream nexus intersecting the waterbody. Attributes are retained from Hydrofabric 2.2 when available. If not available, defaults are used.

## Waterbodies
The "waterbodies" layer includes both active NWM lakes and potential RFC-DA candidates based on the "reference reservoirs" dataset. The waterbodies layer may not include all NWM lakes. Spatial placement may differ.

## Future Work
The workstreams will be fused to create one lakes layer including all active NWM lakes and additional RFC-DA candidates identified in waterbodies. New data collected in waterbodies will improve NWM lake attributes where available.

## Data sources
### Inputs
s3: `s3://edfs-data/lakes/input`
local: `data/lakes/input`

#### Lake polygons:
OWP sent a file called `nwm_lakes.gpkg` in January 2026. This polygon file was split into `nwm_lakes_hi_input.gpkg`, `nwm_lakes_prvi_input.gpkg`, and `nwm_lakes_sconus_input.gpkg`. There are no AK polygons. Polygons are associated with most downstream flowpath intersecting.

#### Attribute data:
Attribute data from HF 2.2 is joined to retain all attributes and RFC-DA categories. Defaults are filled in for lakes not present in 2.2 (Canada, ~30 missing CONUS lakes)
- AK: `ak_lakeparm.gpkg` - lakeparm points from NWMv3
- HI: `hi_nextgenwork_around.gpkg` The final HF 2.2 Hawaii version used by NGWPC
- PRVI: `prvi_nextgen_workaround.gpkg` The final HF 2.2 PRVI version used by NGWPC
- SuperCONUS: `nwm_patch_conus_nextgen.gpg` The final HF 2.2 CONUS version used by NGWPC

### Outputs:
s3: `s3://edfs-data/lakes/output`
local: `data/lakes/output`
The output of the flowpath-associated and merged file is saved when flowpath_association is run. It can also be loaded a priori to skip running flowpath association.

- AK: `ak_lakes_fp_associated.gpkg`
- HI: `hawaii_lakes_fp_associated.gpkg`
- PRVI: `prvi_lakes_fp_associated.gpkg`
- SuperCONUS: `sconus_lakes_fp_associated.gpkg`

These outputs are used to create the final `lakes` layer in the NHF gpkg.
