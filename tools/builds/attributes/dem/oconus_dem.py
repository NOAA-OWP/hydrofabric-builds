"""Script to process DEM for OCONUS domains. Resamples to specified resolution and calculates slope + aspect.

Sample calls:
python tools/builds/attributes/dem/oconus_dem.py --dem data/USGS_Seamless_DEM_1.vrt --outdir data --all-domains --res 250
python tools/builds/attributes/dem/oconus_dem.py --dem data/USGS_Seamless_DEM_1.vrt --outdir data --hi --res 250
"""

import argparse
from pathlib import Path

import xarray as xr
from pydantic import BaseModel
from rasterio.enums import Resampling
from xrspatial import aspect, slope


class DomainModel(BaseModel):
    """Model for storing domain information"""

    name: str
    crs: int | str
    minx: float
    miny: float
    maxx: float
    maxy: float


class AK(DomainModel):
    """Bounds for southern AK domain"""

    name: str = "ak"
    crs: int | str = 3338
    minx: float = -154.9
    miny: float = 58.4
    maxx: float = -139.7
    maxy: float = 63.9


class PRVI(DomainModel):
    """Bounds for PRVI domain"""

    name: str = "prvi"
    crs: int | str = 6566
    minx: float = -67.43
    miny: float = 17.5
    maxx: float = -64.19
    maxy: float = 18.8


class HI(DomainModel):
    """Bounds for HI domain"""

    name: str = "hi"
    crs: int | str = 32604
    minx: float = -160.35
    miny: float = 18.6
    maxx: float = -154.6
    maxy: float = 22.3


# Mapping to call domain models in function
DOMAIN_MAPPING = {"ak": AK, "hi": HI, "prvi": PRVI}


def _clip_raster(
    ras: Path | str,
    minx: float,
    miny: float,
    maxx: float,
    maxy: float,
    crs: int | str,
    out_file: Path,
    resampling: Resampling = Resampling.bilinear,
) -> None:
    """Clip, reproject, and save a raster"""
    ds = xr.open_dataset(ras, engine="rasterio")
    ds_cl = ds.rio.clip_box(minx=minx, miny=miny, maxx=maxx, maxy=maxy)
    ds_cl = ds_cl.rio.reproject(crs, resampling=resampling)
    ds_cl.band_data.rio.to_raster(
        out_file, tiled=True, windowed=True, bigtiff="YES", compress="deflate", driver="GTiff"
    )
    print(f"Saved {out_file}")


def _resample(dem: Path, out_file: Path, resolution: int) -> None:
    """Resample a DEM and save"""
    ds = xr.open_dataset(dem, engine="rasterio")
    ds = ds.rio.reproject(ds.rio.crs, resolution=(resolution, resolution))
    ds.band_data.rio.to_raster(out_file, tiled=True, compress="deflate")
    print(f"Saved {out_file}")


def _slope(dem: Path, out_file: Path) -> None:
    """Generate slope from a DEM and save"""
    ds = xr.open_dataset(dem, engine="rasterio")
    da_slope = slope(ds.band_data[0])
    ds.band_data[0] = da_slope
    ds.band_data.rio.to_raster(out_file, tiled=True, compress="deflate")
    print(f"Saved {out_file}")


def _aspect(dem: Path, out_file: Path) -> None:
    """Generate aspect from a DEM and save"""
    ds = xr.open_dataset(dem, engine="rasterio")
    da_aspect = aspect(ds.band_data[0])
    ds.band_data[0] = da_aspect
    ds.band_data.rio.to_raster(out_file, tiled=True, compress="deflate")
    print(f"Saved {out_file}")


def _dem_to_derivatives(dem: Path, out_dir: Path | str, region: str, resolution: int) -> None:
    """Generate resample, slope, and aspect from DEM

    Parameters
    ----------
    dem : Path
        Path to input DEM
    out_dir : Path | str
        Folder to save outputs
    region : str
        Region name for outputs e.g. "prvi"
    resolution : int
        resample resolution
    """
    out_dir = Path(out_dir)
    resampled = out_dir / f"{region}_dem_{resolution}.tif"
    _resample(dem, resampled, resolution)
    _slope(resampled, out_dir / f"{region}_slope_{resolution}.tif")
    _aspect(resampled, out_dir / f"{region}_aspect_{resolution}.tif")


def run_domain(dem: Path, out_dir: Path, domain: type[DomainModel], resolution: int) -> None:
    """Run a single domain - clip, resample, calculate slope and aspect

    Parameters
    ----------
    dem : Path
        Input DEM to clip from, e.g. a VRT
    out_dir : Path
        Output folder
    domain : type[DomainModel]
        pydantic model containing the domain's attributes
    resolution : int, optional
        Resample resolution
    """
    domain = domain()
    tmp_file = out_dir / f"{domain.name}_raw.tif"
    _clip_raster(
        dem,
        minx=domain.minx,
        miny=domain.miny,
        maxx=domain.maxx,
        maxy=domain.maxy,
        out_file=tmp_file,
        crs=domain.crs,
    )
    _dem_to_derivatives(dem=tmp_file, out_dir=out_dir, region=domain.name, resolution=resolution)


def main(dem: Path, out_dir: Path, domains: list[str], resolution: int) -> None:
    """Run any combination of OCONUS domains

    Parameters
    ----------
    dem : Path
        Input DEM to clip from, e.g. a VRT. If running multiple regions, should contain all.
    out_dir : Path
        Output folder
    domains : list[str], optional
        List of OCONUS domains ('hi', 'ak', and/or 'prvi')
    resolution : int, optional
        Resample resolution

    """
    for domain in domains:
        print(f"Running {domain}")
        try:
            run_domain(dem=dem, out_dir=out_dir, domain=DOMAIN_MAPPING[domain], resolution=resolution)
        except KeyError as e:
            raise ValueError(f"Requested unknown domain: {domain}. Use 'ak', 'hi', or 'prvi'") from e
        except Exception as e:
            raise e


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="A script to process DEM, slope, and aspect for OCONUS domains [Southern Alaska, Hawaii, Puerto Rico/Virgin Islands]. "
        "Recommend to use with a seamless USGS VRT (e.g. 1 arc second (~30 m) or 1/3 arcsecond (~10 m)"
    )
    parser.add_argument(
        "--dem", required=True, type=str, help="Path to source DEM to clip from (e.g. a USGS seamless VRT)"
    )
    parser.add_argument("--outdir", required=True, type=str, help="Path to store outputs")
    parser.add_argument("--all-domains", required=False, action="store_true", help="Run all domains")
    parser.add_argument("--hi", required=False, action="store_true", help="Run Hawaii")
    parser.add_argument("--ak", required=False, action="store_true", help="Run Alaska")
    parser.add_argument(
        "--prvi", required=False, action="store_true", help="Run Puerto Rico / Virgin Islands"
    )
    parser.add_argument("--res", required=False, type=int, default=250, help="Resolution to resample to")

    args = parser.parse_args()

    if args.all_domains:
        domains = ["hi", "prvi", "ak"]
    else:
        domains = []
        if args.ak:
            domains.append("ak")
        if args.hi:
            domains.append("hi")
        if args.prvi:
            domains.append("prvi")

    if not domains:
        raise ValueError(
            "No domains input to run. Use flags for --all-domains or a combination of --ak, --hi, and --prvi"
        )

    main(dem=Path(args.dem), out_dir=Path(args.outdir), domains=domains, resolution=args.res)
