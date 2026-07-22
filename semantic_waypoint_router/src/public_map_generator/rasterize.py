from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
from rasterio.features import rasterize
from shapely.geometry.base import BaseGeometry

from .grid import GridSpec, write_raster

ROAD_WIDTHS_M = {
    "motorway": 14.0,
    "trunk": 12.0,
    "primary": 10.0,
    "secondary": 9.0,
    "tertiary": 8.0,
    "residential": 7.0,
    "unclassified": 6.0,
    "service": 5.0,
    "track": 3.5,
    "path": 1.5,
    "footway": 1.5,
    "cycleway": 2.0,
}


def _burn(geometries: list[BaseGeometry], grid: GridSpec, value: float = 1.0) -> np.ndarray:
    valid = [geometry for geometry in geometries if geometry is not None and not geometry.is_empty]
    if not valid:
        return np.zeros((grid.height, grid.width), dtype=np.float32)
    return rasterize(
        [(geometry, value) for geometry in valid],
        out_shape=(grid.height, grid.width),
        transform=grid.transform,
        fill=0.0,
        all_touched=True,
        dtype="float32",
    )


def _as_areal(geometry: BaseGeometry, width_m: float) -> BaseGeometry:
    if geometry.geom_type in {"Polygon", "MultiPolygon"}:
        return geometry
    if geometry.geom_type == "Point":
        return geometry.buffer(width_m / 2.0)
    return geometry.buffer(width_m / 2.0, cap_style=2, join_style=2)


def rasterize_osm(gdf: gpd.GeoDataFrame, grid: GridSpec, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    names = [
        "buildings",
        "roads",
        "water",
        "waterways",
        "wetlands",
        "forest",
        "farmland",
        "grass",
        "scrub",
        "barriers",
    ]
    arrays = {name: np.zeros((grid.height, grid.width), dtype=np.float32) for name in names}
    if not gdf.empty:
        projected = gdf.to_crs(grid.crs)
        kind_map = {
            "building": "buildings",
            "road": "roads",
            "water": "water",
            "waterway": "waterways",
            "wetland": "wetlands",
            "forest": "forest",
            "farmland": "farmland",
            "grass": "grass",
            "scrub": "scrub",
            "barrier": "barriers",
        }
        for kind, destination_name in kind_map.items():
            subset = projected[projected["kind"] == kind]
            if subset.empty:
                continue
            geometries: list[BaseGeometry] = []
            for _, row in subset.iterrows():
                geometry = row.geometry
                if kind == "road":
                    width = ROAD_WIDTHS_M.get(str(row.get("subtype")), 3.0)
                    geometry = _as_areal(geometry, width)
                elif kind == "waterway":
                    geometry = _as_areal(geometry, 3.0)
                elif kind == "barrier":
                    geometry = _as_areal(geometry, 1.0)
                geometries.append(geometry)
            arrays[destination_name] = _burn(geometries, grid)

    return {
        name: write_raster(output_dir / f"{name}.tif", array, grid, nodata=0.0)
        for name, array in arrays.items()
    }
