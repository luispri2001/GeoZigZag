from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from scipy import ndimage

from .grid import GridSpec, write_raster


@dataclass
class TerrainLayers:
    elevation: np.ndarray
    slope: np.ndarray
    aspect: np.ndarray
    roughness: np.ndarray
    local_relief: np.ndarray
    max_neighbor_step: np.ndarray


def _nearest_fill(data: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    valid = np.isfinite(data)
    if not valid.any():
        raise ValueError("El ráster de elevación no contiene ninguna celda válida")
    if valid.all():
        return data.astype(np.float32, copy=True), valid
    indices = ndimage.distance_transform_edt(~valid, return_distances=False, return_indices=True)
    filled = data[tuple(indices)]
    return filled.astype(np.float32), valid


def _window_cells(window_m: float, resolution: float) -> int:
    cells = max(3, int(round(window_m / resolution)))
    if cells % 2 == 0:
        cells += 1
    return cells


def derive_terrain_layers(
    elevation: np.ndarray,
    resolution_m: float,
    roughness_window_m: float,
    relief_window_m: float,
) -> TerrainLayers:
    z, original_valid = _nearest_fill(elevation.astype(np.float32))
    dz_dy, dz_dx = np.gradient(z, resolution_m, resolution_m)
    slope = np.degrees(np.arctan(np.hypot(dz_dx, dz_dy))).astype(np.float32)
    aspect = (90.0 - np.degrees(np.arctan2(-dz_dy, dz_dx))) % 360.0
    aspect = aspect.astype(np.float32)

    roughness_cells = _window_cells(roughness_window_m, resolution_m)
    mean = ndimage.uniform_filter(z, size=roughness_cells, mode="nearest")
    mean_sq = ndimage.uniform_filter(z * z, size=roughness_cells, mode="nearest")
    roughness = np.sqrt(np.maximum(mean_sq - mean * mean, 0.0)).astype(np.float32)

    relief_cells = _window_cells(relief_window_m, resolution_m)
    local_max = ndimage.maximum_filter(z, size=relief_cells, mode="nearest")
    local_min = ndimage.minimum_filter(z, size=relief_cells, mode="nearest")
    local_relief = (local_max - local_min).astype(np.float32)

    steps: list[np.ndarray] = []
    for dy, dx in [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)]:
        shifted = np.roll(np.roll(z, dy, axis=0), dx, axis=1)
        steps.append(np.abs(z - shifted))
    max_step = np.max(np.stack(steps, axis=0), axis=0).astype(np.float32)
    max_step[[0, -1], :] = np.nan
    max_step[:, [0, -1]] = np.nan

    for layer in (slope, aspect, roughness, local_relief, max_step):
        layer[~original_valid] = np.nan
    z[~original_valid] = np.nan
    return TerrainLayers(z, slope, aspect, roughness, local_relief, max_step)


def load_elevation(path: str | Path) -> np.ndarray:
    with rasterio.open(path) as dataset:
        array = dataset.read(1).astype(np.float32)
        if dataset.nodata is not None:
            array[array == dataset.nodata] = np.nan
        array[~np.isfinite(array)] = np.nan
    return array


def save_terrain_layers(layers: TerrainLayers, grid: GridSpec, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "elevation": write_raster(output_dir / "elevation.tif", layers.elevation, grid),
        "slope": write_raster(output_dir / "slope_degrees.tif", layers.slope, grid),
        "aspect": write_raster(output_dir / "aspect_degrees.tif", layers.aspect, grid),
        "roughness": write_raster(output_dir / "roughness.tif", layers.roughness, grid),
        "local_relief": write_raster(output_dir / "local_relief.tif", layers.local_relief, grid),
        "max_neighbor_step": write_raster(
            output_dir / "max_neighbor_step.tif", layers.max_neighbor_step, grid
        ),
    }
    return outputs
