from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from rasterio.transform import from_origin


@dataclass(frozen=True)
class GridSpec:
    crs: str
    resolution: float
    width: int
    height: int
    transform: Affine
    bounds: tuple[float, float, float, float]
    nodata: float = -9999.0

    @classmethod
    def from_bounds(
        cls,
        bounds: tuple[float, float, float, float],
        crs: str,
        resolution: float,
        nodata: float = -9999.0,
    ) -> GridSpec:
        minx, miny, maxx, maxy = bounds
        minx = floor(minx / resolution) * resolution
        miny = floor(miny / resolution) * resolution
        maxx = ceil(maxx / resolution) * resolution
        maxy = ceil(maxy / resolution) * resolution
        width = int(round((maxx - minx) / resolution))
        height = int(round((maxy - miny) / resolution))
        if width <= 0 or height <= 0:
            raise ValueError("La cuadrícula calculada no tiene tamaño válido")
        transform = from_origin(minx, maxy, resolution, resolution)
        return cls(
            crs=crs,
            resolution=resolution,
            width=width,
            height=height,
            transform=transform,
            bounds=(minx, miny, maxx, maxy),
            nodata=nodata,
        )

    def profile(self, count: int = 1, dtype: str = "float32", nodata: float | None = None) -> dict:
        return {
            "driver": "GTiff",
            "height": self.height,
            "width": self.width,
            "count": count,
            "dtype": dtype,
            "crs": self.crs,
            "transform": self.transform,
            "compress": "deflate",
            "predictor": 2 if dtype.startswith("float") else 1,
            "tiled": True,
            "blockxsize": min(512, max(16, (self.width // 16) * 16)),
            "blockysize": min(512, max(16, (self.height // 16) * 16)),
            "nodata": self.nodata if nodata is None else nodata,
        }


def write_raster(
    path: str | Path,
    array: np.ndarray,
    grid: GridSpec,
    *,
    nodata: float | None = None,
    descriptions: list[str] | None = None,
) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    data = array[np.newaxis, ...] if array.ndim == 2 else array
    dtype = str(data.dtype)
    effective_nodata = grid.nodata if nodata is None else nodata
    if np.issubdtype(data.dtype, np.floating):
        data_to_write = np.where(np.isfinite(data), data, effective_nodata).astype(data.dtype)
    else:
        data_to_write = data
    profile = grid.profile(count=data.shape[0], dtype=dtype, nodata=effective_nodata)
    with rasterio.open(out, "w", **profile) as dataset:
        dataset.write(data_to_write)
        if descriptions:
            for index, description in enumerate(descriptions, start=1):
                dataset.set_band_description(index, description)
    return out


def read_raster(path: str | Path, masked: bool = True) -> tuple[np.ndarray, dict]:
    with rasterio.open(path) as dataset:
        data = dataset.read(masked=masked)
        profile = dataset.profile.copy()
    return data, profile
