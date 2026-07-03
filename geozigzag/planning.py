"""Backward-compatible GeoZigzag planning API.

New code should import the focused ``coverage``, ``routing``, ``metrics`` and
``export`` modules directly.  This facade preserves the original CLI and
downstream imports while the implementation remains modular.
"""

from __future__ import annotations

from typing import Sequence

from .coverage import DEFAULT_FIELD_CORNERS, generate_zigzag_polygon, generate_zigzag_rect
from .export import export_csv, export_ros_yaml
from .geometry import points_to_waypoints, route_distance_m, yaw_to_quaternion
from .routing import (
    LANDCOVER_COST,
    generate_cost_route,
    generate_direct_route,
    generate_direct_route_from_ids,
    load_geojson,
)


def summarize_route(waypoints: Sequence[dict[str, float]]) -> dict[str, float | int]:
    """Return the original compact summary used by :mod:`geozigzag.cli`."""
    return {"points": len(waypoints), "distance_m": route_distance_m(waypoints)}


__all__ = [
    "DEFAULT_FIELD_CORNERS",
    "LANDCOVER_COST",
    "export_csv",
    "export_ros_yaml",
    "generate_cost_route",
    "generate_direct_route",
    "generate_direct_route_from_ids",
    "generate_zigzag_polygon",
    "generate_zigzag_rect",
    "load_geojson",
    "points_to_waypoints",
    "summarize_route",
    "yaw_to_quaternion",
]
