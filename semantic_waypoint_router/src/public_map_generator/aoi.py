from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
from pyproj import CRS
from shapely.geometry import box

from .config import AOIConfig


@dataclass(frozen=True)
class AOI:
    projected: gpd.GeoDataFrame
    wgs84: gpd.GeoDataFrame

    @property
    def geometry(self):
        return self.projected.geometry.union_all()

    @property
    def geometry_wgs84(self):
        return self.wgs84.geometry.union_all()

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return tuple(float(v) for v in self.geometry.bounds)

    @property
    def bounds_wgs84(self) -> tuple[float, float, float, float]:
        return tuple(float(v) for v in self.geometry_wgs84.bounds)



def build_aoi(config: AOIConfig, target_crs: str, padding_m: float = 0.0) -> AOI:
    target = CRS.from_user_input(target_crs)

    if config.type == "center_radius":
        point_wgs = gpd.GeoSeries.from_xy([config.lon], [config.lat], crs="EPSG:4326")
        point_projected = point_wgs.to_crs(target)
        geom_projected = point_projected.iloc[0].buffer(float(config.radius_m) + padding_m)
        projected = gpd.GeoDataFrame({"name": ["aoi"]}, geometry=[geom_projected], crs=target)
    elif config.type == "bbox":
        west, south, east, north = config.bbox or [0, 0, 0, 0]
        geom_wgs = box(west, south, east, north)
        geom_projected = gpd.GeoSeries([geom_wgs], crs="EPSG:4326").to_crs(target).iloc[0]
        if padding_m:
            geom_projected = geom_projected.buffer(padding_m)
        projected = gpd.GeoDataFrame({"name": ["aoi"]}, geometry=[geom_projected], crs=target)
    else:
        source = gpd.read_file(Path(config.path or ""))
        if source.empty:
            raise ValueError("El GeoJSON del AOI no contiene geometrías")
        if source.crs is None:
            raise ValueError("El GeoJSON del AOI no define CRS")
        source = source.to_crs(target)
        geom = source.geometry.union_all()
        if padding_m:
            geom = geom.buffer(padding_m)
        projected = gpd.GeoDataFrame({"name": ["aoi"]}, geometry=[geom], crs=target)

    projected.geometry = projected.geometry.make_valid()
    wgs84 = projected.to_crs("EPSG:4326")
    return AOI(projected=projected, wgs84=wgs84)
