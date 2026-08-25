Create lakes layer geopackage by running:
python lakes_hydrofabric.py --lakeparm_file --ana_res_file --ext_res_file --med_res_file --short_res_file --hffile --output_path

--lakeparm_file full path and filename of lakeparms netcdf file, e.g, LAKEPARM_CONUS_216.nc
--ana_res_file full path and filename of ana res netcdf file, e.g., reservoir_index_AnA_309.nc
--ext_res_file full path and filename of extended res netcdf file, e.g., reservoir_index_Extended_AnA.nc
--med_res_file full path and filename of medium range res netcdf file, e.g., reservoir_index_Medium_Range.nc
--short_res_file full path and filename of medium range res netcdf file, e.g., reservoir_index_Short_Range.nc
--hffile full path and filename of hydrofabric geopackage
--output_path full path to directory where outputs will be saved
--domain hydrofabric domain.  Only CONUS at this time.

Check this lake layer against hydrofabric lakes layer by running tools/check_lakes.py
This script checks for lakes that are present in one dataset but not in the other and
returns a csv file showing differences in the lakes layer table.
