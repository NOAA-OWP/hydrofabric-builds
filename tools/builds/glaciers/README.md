# Glaciers

Glaciers are needed in the Hydrofabric to determine where TopoFlow-Glacier should be run.

Use `glacier_prep.ipynb` to download and process GLIMS glaciers. It will download all glaciers from most recent file and clip to buffered-US boundaries.

After creating a glacier parquet from `glacier_prep.ipynb`, run `glacier_hydrofabric.py` to map glaciers to divide attributes, hydolocations, and POIs. Divide attributes will include the percent glaciated area of divide.

Sample calls to `glacier_hydrofabric.py`:

Glue:
```
python tools/builds/glaciers/glacier_hydrofabric.py --hf_domain ak --catalog glue --glacier_path edfs-data/glaciers/glims_20250624.parquet --working_dir edfs-data/glaciers/test --s3_files
```

Local - Fill in local paths:
```
python tools/builds/glaciers/glacier_hydrofabric.py --hf_domain ak --catalog sql --glacier_path /{your_directory}/glims_20250624.parquet --working_dir /{your_directory}/test`
```
