# Validation

The NHF includes a validation step that will validate the output GPKG for various constraints. Validation includes:

- Counting nulls in divide attributes and flowpath attributes
- Summarizing nulls based on land use type (to understand why nulls are present)
- Validate that divide and flowpath attributes have correct values and are present if required
- Missing calibration and routelink gages
- Any gage, calibration, and routelink gages missing a flowpath or virtual flowpath
- Missing NWM or lakeparm (AK) lakes
- Lakes with no flowpath or virtual flowpath
- Lakes with multiple/duplicate points

During the reservoir DA pipeline, a warning will alert for any unmatched gages if DA is set to non-level pool.

During the lakes pipeline, it will fail if all NWM lakes are not included.
