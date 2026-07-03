"""Route-quality and validation metrics."""

from __future__ import annotations

import math
from typing import Any, Sequence

from .geometry import (
    PointLL,
    ll_to_xy,
    local_origin,
    normalize_angle,
    polygon_area_m2,
    route_distance_m,
    segment_intersects_polygon_xy,
)


def count_turns(waypoints: Sequence[dict[str, float]], threshold_deg: float = 30.0) -> int:
    """Count polyline heading changes larger than ``threshold_deg``.

    This measures discrete path corners, not dynamically feasible steering
    manoeuvres. Two corners in a rectangular row connector therefore count as
    two turns.
    """
    if threshold_deg <= 0 or threshold_deg > 180:
        raise ValueError("Turn threshold must be in (0, 180] degrees.")
    headings: list[float] = []
    for first, second in zip(waypoints, waypoints[1:]):
        x, y = ll_to_xy(
            second["latitude"], second["longitude"], first["latitude"], first["longitude"]
        )
        if math.hypot(x, y) > 1e-6:
            headings.append(math.atan2(y, x))
    return sum(
        abs(math.degrees(normalize_angle(second - first))) >= threshold_deg
        for first, second in zip(headings, headings[1:])
    )


def forbidden_zone_intersections(
    waypoints: Sequence[dict[str, float]], forbidden_zones: Sequence[Sequence[PointLL]]
) -> int:
    """Count route segments that touch or cross at least one forbidden zone."""
    if len(waypoints) < 2 or not forbidden_zones:
        return 0
    all_points = [
        (waypoint["latitude"], waypoint["longitude"]) for waypoint in waypoints
    ] + [point for zone in forbidden_zones for point in zone]
    origin = local_origin(all_points)
    zones_xy = [[ll_to_xy(*point, *origin) for point in zone] for zone in forbidden_zones]
    intersections = 0
    for first, second in zip(waypoints, waypoints[1:]):
        segment_start = ll_to_xy(first["latitude"], first["longitude"], *origin)
        segment_end = ll_to_xy(second["latitude"], second["longitude"], *origin)
        if any(segment_intersects_polygon_xy(segment_start, segment_end, zone) for zone in zones_xy):
            intersections += 1
    return intersections


def summarize_route(
    waypoints: Sequence[dict[str, float]],
    *,
    forbidden_zones: Sequence[Sequence[PointLL]] = (),
    computation_time_ms: float | None = None,
    success: bool = True,
    rows: int | None = None,
    area_m2: float | None = None,
    average_row_length_m: float | None = None,
    row_spacing_m: float | None = None,
    waypoint_spacing_m: float | None = None,
    max_snap_distance_m: float | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "success": success,
        "waypoints": len(waypoints),
        "distance_m": route_distance_m(waypoints),
        "turns": count_turns(waypoints),
        "forbidden_zone_intersections": forbidden_zone_intersections(waypoints, forbidden_zones),
    }
    optional = {
        "computation_time_ms": computation_time_ms,
        "rows": rows,
        "area_m2": area_m2,
        "average_row_length_m": average_row_length_m,
        "row_spacing_m": row_spacing_m,
        "waypoint_spacing_m": waypoint_spacing_m,
        "max_snap_distance_m": max_snap_distance_m,
    }
    result.update({key: value for key, value in optional.items() if value is not None})
    return result


def area_from_polygon(vertices: Sequence[PointLL]) -> float:
    return polygon_area_m2(vertices)
