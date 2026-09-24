from enum import Enum, IntEnum


class GroundWaterProjectionCONUS(Enum):
    """CRS, origin (top, left corner), and size of CONUS NWM grids for groundwater; source:  Fulldom_CONUS_FullRouting.nc"""

    PROJ4 = "+proj=lcc +lat_1=30 +lat_2=60 +lat_0=40.0000076293945 +lon_0=-97 +x_0=0 +y_0=0 +a=6370000 +b=6370000 +units=m +no_defs"
    X_ORIGIN = -2303874.17655
    Y_ORIGIN = -1919874.66329
    WIDTH = 18432
    HEIGHT = 15360
    DX = 250
    DY = 250


class GroundWaterProjectionAK(Enum):
    """CRS, origin (top, left corner), and size of NWM grids for groundwater; source:  Fulldom_AK_FullRouting.nc"""

    PROJ4 = "+proj=stere +lat_0=90 +lat_ts=60 +lon_0=-135 +x_0=0 +y_0=0 +R=6370000 +units=m +no_defs"
    X_ORIGIN = -1130764.7202253528
    Y_ORIGIN = -3163389.53353531
    WIDTH = 3516
    HEIGHT = 1816
    DX = 250
    DY = 250


class GroundWaterProjectionHI(Enum):
    """CRS, origin (top, left corner), and size of NWM grids for groundwater; source:  Fulldom_HI_FullRouting.nc"""

    PROJ4 = "+proj=lcc +units=m +a=6370000.0 +b=6370000.0 +lat_1=10.0 +lat_2=30.0 +lat_0=20.6 +lon_0=-157.42 +x_0=0 +y_0=0 +k_0=1.0 +nadgrids=@null +wktext +no_defs"
    X_ORIGIN = -294950.07097397465
    Y_ORIGIN = -194949.36969098
    WIDTH = 5900
    HEIGHT = 3900
    DX = 100
    DY = 100


class GroundWaterProjectionPRVI(Enum):
    """CRS, origin (top, left corner), and size of NWM grids for groundwater; source:  Fulldom_PRVI_FullRouting.nc"""

    PROJ4 = "+proj=lcc +units=m +a=6370000.0 +b=6370000.0 +lat_1=18.1 +lat_2=18.1 +lat_0=18.1 +lon_0=-65.91 +x_0=0 +y_0=0 +k_0=1.0 +nadgrids=@null +wktext  +no_defs"
    X_ORIGIN = -149949.83
    Y_ORIGIN = -54948.968
    WIDTH = 3000
    HEIGHT = 1100
    DX = 100
    DY = 100


class NWMProjectionCONUS(Enum):
    """Projection for NWM soilproperties for CONUS"""

    PROJ4 = "+proj=lcc +lat_0=40.0000076293945 +lon_0=-97 +lat_1=30 +lat_2=60 +x_0=0 +y_0=0 +R=6370000 +units=m +no_defs"
    XMIN = -2304000
    XMAX = 2304000
    YMIN = -1920001
    YMAX = 1919999
    DX = 1000
    DY = 1000


class NWMProjectionAK(Enum):
    """Projection for NWM soilproperties for AK"""

    PROJ4 = "+proj=stere +lat_0=90 +lat_ts=60 +lon_0=-135"
    XMIN = -1133617.2
    XMAX = -254617.2
    YMIN = -3175222.3
    YMAX = -2716222.3
    DX = 1000
    DY = 1000


class NWMProjectionHI(Enum):
    """Projection for NWM soilproperties for HI"""

    PROJ4 = "+proj=lcc +units=m +a=6370000.0 +b=6370000.0 +lat_1=10.0 +lat_2=30.0 +lat_0=20.6 +lon_0=-157.42 +x_0=0 +y_0=0 +k_0=1.0 +nadgrids=@null +wktext  +no_defs"
    XMIN = -295000.4746870845
    XMAX = 294999.5253129155
    YMIN = -194999.9185492387
    YMAX = 195000.0814507613
    DX = 1000
    DY = 1000


class NWMProjectionPRVI(Enum):
    """Projection for NWM soilproperties for PRVI"""

    PROJ4 = "+proj=lcc +units=m +a=6370000.0 +b=6370000.0 +lat_1=18.1 +lat_2=18.1 +lat_0=18.1 +lon_0=-65.91 +x_0=0 +y_0=0 +k_0=1.0 +nadgrids=@null +wktext  +no_defs"
    XMIN = -149999.83
    XMAX = 150000.17
    YMIN = -54998.968
    YMAX = 55001.032
    DX = 1000
    DY = 1000


class HydrofabricCRS(Enum):
    """The domains used when querying the hydrofabric

    Attributes
    ----------
    AK : str
        Alaska
    CONUS : str
        Conterminous United States
    GL : str
        The US Great Lakes
    HI : str
        Hawai'i
    PRVI : str
        Puerto Rico, US Virgin Islands
    """

    AK = 3338
    CONUS = 5070
    HI = 32604
    PRVI = 6566


class VegetationTypes(IntEnum):
    """IVGTYP vegetation types"""

    URBAN_AND_BUILT_UP_LAND = 1
    DRYLAND_CROPLAND_AND_PASTURE = 2
    IRRIGATED_CROPLAND_AND_PASTURE = 3
    MIXED_DRYLAND_IRRIGATED_CROPLAND_AND_PASTURE = 4
    CROPLAND_GRASSLAND_MOSAIC = 5
    CROPLAND_WOODLAND_MOSAIC = 6
    GRASSLAND = 7
    SHRUBLAND = 8
    MIXED_SHRUBLAND_GRASSLAND = 9
    SAVANNA = 10
    DECIDUOUS_BROADLEAF_FOREST = 11
    DECIDUOUS_NEEDLELEAF_FOREST = 12
    EVERGREEN_BROADLEAF_FOREST = 13
    EVERGREEN_NEEDLELEAF_FOREST = 14
    MIXED_FOREST = 15
    WATER_BODIES = 16
    HERBACEOUS_WETLAND = 17
    WOODED_WETLAND = 18
    BARREN_OR_SPARSELY_VEGETATED = 19
    HERBACEOUS_TUNDRA = 20
    WOODED_TUNDRA = 21
    MIXED_TUNDRA = 22
    BARE_GROUND_TUNDRA = 23
    SNOW_OR_ICE = 24
    PLAYA = 25
    LAVA = 26


class VegetationTypesCombined(IntEnum):
    """Combined vegetation types"""

    MODERATE = 1
    FOREST = 2
    SPARSE = 3
    NA = 4
