"""DEM-derived traversability layer for the local semantic costmap."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Callable

from .elevation import ElevationModel
from .geometry import PointLL


@dataclass(frozen=True)
class ElevationCostConfig:
    """Robot-specific conversion from terrain slope to planning cost.

    Absolute altitude is intentionally absent: a flat plateau is traversable.
    ``preferred_slope_pct`` defines the scale of the soft quadratic penalty,
    while ``max_slope_pct`` is a hard traversability boundary.
    """

    preferred_slope_pct: float = 5.0
    max_slope_pct: float = 18.0
    slope_cost_multiplier: float = 20.0

    def __post_init__(self) -> None:
        if self.preferred_slope_pct <= 0:
            raise ValueError("Preferred slope must be positive.")
        if self.max_slope_pct <= 0:
            raise ValueError("Maximum slope must be positive.")
        if self.max_slope_pct < self.preferred_slope_pct:
            raise ValueError("Maximum slope cannot be lower than preferred slope.")
        if self.slope_cost_multiplier < 0:
            raise ValueError("Slope cost multiplier cannot be negative.")


@dataclass
class TerrainCostLayer:
    elevations_m: list[list[float]]
    slopes_pct: list[list[float]]
    penalties: list[list[float]]
    blocked: list[list[bool]]
    resolution_m: float
    source: dict[str, object]
    config: ElevationCostConfig

    def summary(self) -> dict[str, object]:
        elevations = [value for row in self.elevations_m for value in row]
        slopes = [value for row in self.slopes_pct for value in row]
        blocked_count = sum(value for row in self.blocked for value in row)
        cell_count = sum(len(row) for row in self.blocked)
        return {
            "source": self.source,
            "config": asdict(self.config),
            "resolution_m": self.resolution_m,
            "minimum_elevation_m": min(elevations),
            "maximum_elevation_m": max(elevations),
            "maximum_slope_pct": max(slopes),
            "blocked_cells": blocked_count,
            "cell_count": cell_count,
            "blocked_fraction": blocked_count / cell_count if cell_count else 0.0,
        }


def build_terrain_cost_layer(
    elevation_model: ElevationModel,
    width: int,
    height: int,
    resolution_m: float,
    cell_to_ll: Callable[[tuple[int, int]], PointLL],
    config: ElevationCostConfig | None = None,
) -> TerrainCostLayer:
    """Sample a DEM grid and derive slope magnitude using finite differences."""
    if width < 2 or height < 2:
        raise ValueError("Terrain costmap needs at least two rows and columns.")
    if resolution_m <= 0:
        raise ValueError("Terrain costmap resolution must be positive.")
    config = config or ElevationCostConfig()
    elevations: list[list[float]] = []
    for row in range(height):
        elevation_row: list[float] = []
        for col in range(width):
            latitude, longitude = cell_to_ll((row, col))
            value = float(elevation_model.elevation_m(latitude, longitude))
            if not math.isfinite(value):
                raise ValueError(f"DEM returned a non-finite value at grid cell ({row}, {col}).")
            elevation_row.append(value)
        elevations.append(elevation_row)

    slopes: list[list[float]] = []
    penalties: list[list[float]] = []
    blocked: list[list[bool]] = []
    for row in range(height):
        slope_row: list[float] = []
        penalty_row: list[float] = []
        blocked_row: list[bool] = []
        north = min(height - 1, row + 1)
        south = max(0, row - 1)
        y_span = (north - south) * resolution_m
        for col in range(width):
            east = min(width - 1, col + 1)
            west = max(0, col - 1)
            x_span = (east - west) * resolution_m
            dz_dx = (elevations[row][east] - elevations[row][west]) / x_span
            dz_dy = (elevations[north][col] - elevations[south][col]) / y_span
            slope_pct = 100.0 * math.hypot(dz_dx, dz_dy)
            is_blocked = slope_pct > config.max_slope_pct
            penalty = config.slope_cost_multiplier * (
                slope_pct / config.preferred_slope_pct
            ) ** 2
            slope_row.append(slope_pct)
            penalty_row.append(penalty)
            blocked_row.append(is_blocked)
        slopes.append(slope_row)
        penalties.append(penalty_row)
        blocked.append(blocked_row)
    return TerrainCostLayer(
        elevations,
        slopes,
        penalties,
        blocked,
        resolution_m,
        elevation_model.provenance(),
        config,
    )
