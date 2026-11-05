"""Small-area geospatial geometry used by GeoZigzag.

Coordinates at public boundaries are ``(latitude, longitude)`` in WGS84.
Planar calculations use a local equirectangular ENU approximation: x points
east and y points north.  This is intentionally limited to field-scale routes.
"""

from __future__ import annotations

import math
from typing import Iterable

EARTH_RADIUS_M = 6_378_137.0
PointLL = tuple[float, float]
PointXY = tuple[float, float]


def normalize_angle(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


def local_origin(points: Iterable[PointLL]) -> PointLL:
    values = list(points)
    if not values:
        raise ValueError("At least one coordinate is required.")
    return (
        sum(point[0] for point in values) / len(values),
        sum(point[1] for point in values) / len(values),
    )


def ll_to_xy(lat: float, lon: float, origin_lat: float, origin_lon: float) -> PointXY:
    scale = math.cos(math.radians(origin_lat))
    if abs(scale) < 1e-12:
        raise ValueError("The local projection is undefined at the poles.")
    x = math.radians(lon - origin_lon) * EARTH_RADIUS_M * scale
    y = math.radians(lat - origin_lat) * EARTH_RADIUS_M
    return x, y


def xy_to_ll(x: float, y: float, origin_lat: float, origin_lon: float) -> PointLL:
    scale = math.cos(math.radians(origin_lat))
    if abs(scale) < 1e-12:
        raise ValueError("The local projection is undefined at the poles.")
    return (
        origin_lat + math.degrees(y / EARTH_RADIUS_M),
        origin_lon + math.degrees(x / (EARTH_RADIUS_M * scale)),
    )


def yaw_to_quaternion(yaw: float) -> tuple[float, float, float, float]:
    half = yaw / 2.0
    return 0.0, 0.0, math.sin(half), math.cos(half)
