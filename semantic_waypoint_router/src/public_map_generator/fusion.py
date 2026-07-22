from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import ndimage

from .config import TerrainConfig, WeightsConfig
from .grid import GridSpec, write_raster
from .terrain import TerrainLayers


@dataclass
class FusedLayers:
    surface_height: np.ndarray
    wetness_prior: np.ndarray
    vegetation_prior: np.ndarray
    mud_risk: np.ndarray
    water_accumulation_risk: np.ndarray
    obstacle_probability: np.ndarray
    traversability_prior: np.ndarray
    confidence: np.ndarray


def _ramp(values: np.ndarray, warning: float, blocked: float) -> np.ndarray:
    if blocked <= warning:
        raise ValueError("blocked debe ser mayor que warning")
    result = (values - warning) / (blocked - warning)
    return np.clip(result, 0.0, 1.0).astype(np.float32)


def _finite_or_zero(values: np.ndarray) -> np.ndarray:
    return np.where(np.isfinite(values), values, 0.0).astype(np.float32)


def calculate_surface_height(mds: np.ndarray | None, dtm: np.ndarray) -> np.ndarray:
    if mds is None:
        return np.full_like(dtm, np.nan, dtype=np.float32)
    output = np.maximum(mds - dtm, 0.0).astype(np.float32)
    output[~np.isfinite(mds) | ~np.isfinite(dtm)] = np.nan
    return output


def fuse_layers(
    terrain: TerrainLayers,
    osm: dict[str, np.ndarray],
    terrain_config: TerrainConfig,
    weights: WeightsConfig,
    resolution_m: float,
    *,
    mds: np.ndarray | None = None,
    ndvi: np.ndarray | None = None,
    ndmi: np.ndarray | None = None,
    source_flags: dict[str, bool] | None = None,
) -> FusedLayers:
    source_flags = source_flags or {}
    shape = terrain.elevation.shape
    zeros = np.zeros(shape, dtype=np.float32)
    osm_layer = lambda name: osm.get(name, zeros)  # noqa: E731

    surface_height = calculate_surface_height(mds, terrain.elevation)
    slope_cost = _ramp(
        _finite_or_zero(terrain.slope),
        terrain_config.slope_warning_deg,
        terrain_config.slope_blocked_deg,
    )
    roughness_cost = _ramp(
        _finite_or_zero(terrain.roughness),
        terrain_config.roughness_warning_m,
        terrain_config.roughness_blocked_m,
    )
    step_cost = _ramp(
        _finite_or_zero(terrain.max_neighbor_step),
        terrain_config.max_step_warning_m,
        terrain_config.max_step_blocked_m,
    )
    height_cost = _ramp(
        _finite_or_zero(surface_height),
        terrain_config.surface_height_warning_m,
        terrain_config.surface_height_blocked_m,
    )

    permanent_water = np.maximum(osm_layer("water"), osm_layer("waterways"))
    vegetation_osm = np.maximum.reduce(
        [osm_layer("forest"), 0.65 * osm_layer("scrub"), 0.20 * osm_layer("grass")]
    )
    if ndvi is not None:
        ndvi_prior = np.clip((ndvi - 0.35) / 0.45, 0.0, 1.0)
        vegetation_prior = np.maximum(vegetation_osm, np.nan_to_num(ndvi_prior, nan=0.0))
    else:
        vegetation_prior = vegetation_osm
    vegetation_prior = vegetation_prior.astype(np.float32)

    if np.any(permanent_water >= 0.5):
        distance_to_water_px = ndimage.distance_transform_edt(permanent_water < 0.5)
        distance_to_water_m = distance_to_water_px * resolution_m
        water_proximity = np.exp(
            -distance_to_water_m / max(terrain_config.water_proximity_m, 1.0)
        )
    else:
        water_proximity = np.zeros(shape, dtype=np.float32)
    low_slope = 1.0 - np.clip(_finite_or_zero(terrain.slope) / 15.0, 0.0, 1.0)
    low_relief = 1.0 - np.clip(_finite_or_zero(terrain.local_relief) / 2.0, 0.0, 1.0)
    wetness_prior = 0.45 * water_proximity + 0.25 * low_slope + 0.15 * low_relief
    wetness_prior += 0.15 * np.maximum(osm_layer("wetlands"), permanent_water)
    if ndmi is not None:
        ndmi_prior = np.clip((ndmi + 0.05) / 0.55, 0.0, 1.0)
        wetness_prior = 0.65 * wetness_prior + 0.35 * np.nan_to_num(ndmi_prior, nan=0.0)
    wetness_prior = np.clip(wetness_prior, 0.0, 1.0).astype(np.float32)

    soft_ground = np.maximum.reduce(
        [osm_layer("farmland"), osm_layer("grass"), 0.7 * vegetation_prior]
    )
    mud_risk = np.clip(
        wetness_prior * (0.55 + 0.45 * soft_ground) * (0.65 + 0.35 * low_slope),
        0.0,
        1.0,
    ).astype(np.float32)
    water_accumulation_risk = np.clip(
        wetness_prior * (0.55 * low_slope + 0.45 * low_relief), 0.0, 1.0
    )
    water_accumulation_risk = np.maximum(
        water_accumulation_risk, 0.95 * permanent_water
    ).astype(np.float32)

    static_osm_obstacles = np.maximum.reduce(
        [
            osm_layer("buildings"),
            0.98 * permanent_water,
            0.90 * osm_layer("barriers"),
            0.55 * osm_layer("forest"),
            0.35 * osm_layer("scrub"),
        ]
    )
    obstacle_probability = np.maximum.reduce(
        [
            static_osm_obstacles,
            height_cost,
            0.85 * slope_cost,
            0.65 * roughness_cost,
            0.75 * step_cost,
        ]
    ).astype(np.float32)

    raw_cost = (
        weights.slope * slope_cost
        + weights.roughness * roughness_cost
        + weights.max_step * step_cost
        + weights.obstacles * obstacle_probability
        + weights.wetness * wetness_prior
    )
    road_bonus = weights.road_bonus * osm_layer("roads")
    traversability = np.clip(raw_cost - road_bonus, 0.0, 1.0)
    traversability[obstacle_probability >= 0.95] = 1.0
    traversability[~np.isfinite(terrain.elevation)] = np.nan
    traversability = traversability.astype(np.float32)

    confidence = np.full(shape, 0.15, dtype=np.float32)
    if source_flags.get("mdt", True):
        confidence += 0.45
    if source_flags.get("osm", False):
        confidence += 0.15
    if source_flags.get("orthophoto", False):
        confidence += 0.05
    if source_flags.get("mds", False):
        confidence += 0.15
    if source_flags.get("sentinel2", False):
        confidence += 0.05
    confidence = np.clip(confidence, 0.0, 1.0)
    confidence[~np.isfinite(terrain.elevation)] = 0.0

    return FusedLayers(
        surface_height=surface_height,
        wetness_prior=wetness_prior,
        vegetation_prior=vegetation_prior,
        mud_risk=mud_risk,
        water_accumulation_risk=water_accumulation_risk,
        obstacle_probability=obstacle_probability,
        traversability_prior=traversability,
        confidence=confidence,
    )


def save_fused_layers(layers: FusedLayers, grid: GridSpec, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    return {
        "surface_height": write_raster(
            output_dir / "surface_height.tif", layers.surface_height, grid
        ),
        "wetness_prior": write_raster(output_dir / "wetness_prior.tif", layers.wetness_prior, grid),
        "vegetation_prior": write_raster(
            output_dir / "vegetation_prior.tif", layers.vegetation_prior, grid
        ),
        "mud_risk": write_raster(output_dir / "mud_risk.tif", layers.mud_risk, grid),
        "water_accumulation_risk": write_raster(
            output_dir / "water_accumulation_risk.tif",
            layers.water_accumulation_risk,
            grid,
        ),
        "obstacle_probability": write_raster(
            output_dir / "obstacle_probability.tif", layers.obstacle_probability, grid
        ),
        "traversability_prior": write_raster(
            output_dir / "traversability_prior.tif", layers.traversability_prior, grid
        ),
        "confidence": write_raster(output_dir / "confidence.tif", layers.confidence, grid),
    }
