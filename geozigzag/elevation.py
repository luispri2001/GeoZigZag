"""Elevation sources used by the semantic terrain-cost layer.

The public contract deliberately returns elevation in metres.  It keeps DEM
decoding separate from routing geometry, so a GeoTIFF/LiDAR implementation can
be added without changing the route planner.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .geometry import ll_to_xy


class ElevationModel(Protocol):
    """Minimal elevation source required by the planner."""

    def elevation_m(self, latitude: float, longitude: float) -> float:
        """Return elevation in metres for one WGS84 coordinate."""

    def provenance(self) -> dict[str, object]:
        """Return serialisable source metadata for experiment records."""


@dataclass(frozen=True)
class SyntheticGaussianHillElevation:
    """Smooth isolated hill used to demonstrate terrain-cost avoidance."""

    origin_latitude: float
    origin_longitude: float
    amplitude_m: float = 10.0
    sigma_m: float = 8.0
    base_elevation_m: float = 800.0

    def __post_init__(self) -> None:
        if self.sigma_m <= 0:
            raise ValueError("Gaussian hill sigma must be positive.")

    def elevation_m(self, latitude: float, longitude: float) -> float:
        east_m, north_m = ll_to_xy(
            latitude,
            longitude,
            self.origin_latitude,
            self.origin_longitude,
        )
        radius_squared = east_m * east_m + north_m * north_m
        return self.base_elevation_m + self.amplitude_m * math.exp(
            -radius_squared / (2.0 * self.sigma_m * self.sigma_m)
        )

    def provenance(self) -> dict[str, object]:
        return {
            "type": "synthetic_gaussian_hill",
            "purpose": "offline_test_only",
            "origin_wgs84": [self.origin_latitude, self.origin_longitude],
            "base_elevation_m": self.base_elevation_m,
            "amplitude_m": self.amplitude_m,
            "sigma_m": self.sigma_m,
        }


class MapboxTerrainRgbDirectory:
    """Read raw Mapbox Terrain-RGB tiles saved by gazebo_terrain_generator.

    The expected filenames are ``[zoom,tile_y,tile_x].png``.  This is the
    working DEM directory used by the upstream terrain generator, not its
    normalised Gazebo heightmap.  Keeping the raw tiles preserves elevations
    in metres and avoids trying to reverse a display-oriented normalisation.
    """

    _TILE_RE = re.compile(r"^\[(\d+),(\d+),(\d+)\]\.png$")

    def __init__(self, dem_directory: str | Path, zoom: int | None = None):
        self.dem_directory = Path(dem_directory)
        if not self.dem_directory.is_dir():
            raise ValueError(f"DEM directory does not exist: {self.dem_directory}")
        available_zooms = {
            int(match.group(1))
            for path in self.dem_directory.iterdir()
            if (match := self._TILE_RE.match(path.name))
        }
        if not available_zooms:
            raise ValueError(f"No Terrain-RGB tiles found in {self.dem_directory}")
        if zoom is None:
            if len(available_zooms) != 1:
                raise ValueError(
                    "DEM directory contains several zoom levels; specify zoom explicitly."
                )
            zoom = next(iter(available_zooms))
        if zoom not in available_zooms:
            raise ValueError(f"No Terrain-RGB tiles found for zoom {zoom}")
        self.zoom = int(zoom)
        self._images: dict[tuple[int, int], object] = {}

    @classmethod
    def from_terrain_world(cls, world_directory: str | Path) -> "MapboxTerrainRgbDirectory":
        """Build the source from a gazebo_terrain_generator working directory."""
        world = Path(world_directory)
        metadata_path = world / "metadata.json"
        if not metadata_path.is_file():
            raise ValueError(f"Terrain metadata does not exist: {metadata_path}")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if "dem_resolution" not in metadata:
            raise ValueError("Terrain metadata has no dem_resolution field.")
        return cls(world / "dem", zoom=int(metadata["dem_resolution"]))

    @staticmethod
    def _tile_position(latitude: float, longitude: float, zoom: int) -> tuple[float, float]:
        if not (-85.05112878 <= latitude <= 85.05112878):
            raise ValueError("Web Mercator DEM sampling is limited to ±85.05112878 degrees.")
        if not (-180.0 <= longitude <= 180.0):
            raise ValueError("Longitude must be between -180 and 180 degrees.")
        scale = 2**zoom
        x = (longitude + 180.0) / 360.0 * scale
        latitude_rad = math.radians(latitude)
        y = (1.0 - math.asinh(math.tan(latitude_rad)) / math.pi) / 2.0 * scale
        return x, y

    def _load_tile(self, tile_x: int, tile_y: int):
        key = (tile_x, tile_y)
        if key not in self._images:
            try:
                from PIL import Image
            except ImportError as error:
                raise RuntimeError(
                    "Pillow is required for Terrain-RGB tiles; install requirements-dem.txt."
                ) from error
            path = self.dem_directory / f"[{self.zoom},{tile_y},{tile_x}].png"
            if not path.is_file():
                raise ValueError(
                    f"DEM tile {path.name} is missing; the selected polygon is outside the downloaded area."
                )
            self._images[key] = Image.open(path).convert("RGB")
        return self._images[key]

    def elevation_m(self, latitude: float, longitude: float) -> float:
        tile_x_float, tile_y_float = self._tile_position(latitude, longitude, self.zoom)
        tile_x = math.floor(tile_x_float)
        tile_y = math.floor(tile_y_float)
        image = self._load_tile(tile_x, tile_y)
        width, height = image.size
        pixel_x = min(width - 1.0, max(0.0, (tile_x_float - tile_x) * width))
        pixel_y = min(height - 1.0, max(0.0, (tile_y_float - tile_y) * height))
        x0, y0 = math.floor(pixel_x), math.floor(pixel_y)
        x1, y1 = min(width - 1, x0 + 1), min(height - 1, y0 + 1)
        dx, dy = pixel_x - x0, pixel_y - y0

        def decode(x: int, y: int) -> float:
            red, green, blue = image.getpixel((x, y))
            return -10000.0 + (red * 256 * 256 + green * 256 + blue) * 0.1

        north = decode(x0, y0) * (1.0 - dx) + decode(x1, y0) * dx
        south = decode(x0, y1) * (1.0 - dx) + decode(x1, y1) * dx
        return north * (1.0 - dy) + south * dy

    def provenance(self) -> dict[str, object]:
        return {
            "type": "mapbox_terrain_rgb_directory",
            "dem_directory": str(self.dem_directory),
            "zoom": self.zoom,
            "sampling": "bilinear_within_tile",
            "vertical_units": "metres",
        }
