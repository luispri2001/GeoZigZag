"""Small-area geospatial geometry used by GeoZigzag.

Coordinates at public boundaries are ``(latitude, longitude)`` in WGS84.
Planar calculations use a local equirectangular ENU approximation: x points
east and y points north.  This is intentionally limited to field-scale routes.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

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


def unit(vector: PointXY) -> PointXY:
    length = math.hypot(*vector)
    if length < 1e-9:
        raise ValueError("Degenerate geometry: repeated coordinates.")
    return vector[0] / length, vector[1] / length


def positions(start: float, end: float, spacing: float) -> list[float]:
    if spacing <= 0:
        raise ValueError("Spacing must be positive.")
    direction = 1.0 if end >= start else -1.0
    values: list[float] = []
    value = start
    while (direction > 0 and value < end) or (direction < 0 and value > end):
        values.append(value)
        value += direction * spacing
    if not values or abs(values[-1] - end) > 1e-6:
        values.append(end)
    return values


def dedupe(points: Sequence[PointLL]) -> list[PointLL]:
    if not points:
        return []
    cleaned = [points[0]]
    for point in points[1:]:
        if (
            abs(point[0] - cleaned[-1][0]) > 1e-11
            or abs(point[1] - cleaned[-1][1]) > 1e-11
        ):
            cleaned.append(point)
    return cleaned


def yaw_between(a: PointLL, b: PointLL) -> float:
    bx, by = ll_to_xy(b[0], b[1], a[0], a[1])
    return normalize_angle(math.atan2(by, bx))


def points_to_waypoints(points: Sequence[PointLL]) -> list[dict[str, float]]:
    """Attach ENU yaw to WGS84 points.

    Yaw is measured counter-clockwise from east, matching ROS REP-103.  The
    final waypoint reuses the last segment heading.
    """
    cleaned = dedupe(points)
    waypoints: list[dict[str, float]] = []
    for index, point in enumerate(cleaned):
        if index < len(cleaned) - 1:
            yaw = yaw_between(point, cleaned[index + 1])
        elif waypoints:
            yaw = waypoints[-1]["yaw"]
        else:
            yaw = 0.0
        waypoints.append(
            {"latitude": float(point[0]), "longitude": float(point[1]), "yaw": float(yaw)}
        )
    return waypoints


def yaw_to_quaternion(yaw: float) -> tuple[float, float, float, float]:
    half = yaw / 2.0
    return 0.0, 0.0, math.sin(half), math.cos(half)


def segment_points_xy(start: PointXY, end: PointXY, spacing: float) -> list[PointXY]:
    if spacing <= 0:
        raise ValueError("Spacing must be positive.")
    distance = math.hypot(end[0] - start[0], end[1] - start[1])
    if distance <= spacing:
        return [start, end]
    steps = max(1, int(math.ceil(distance / spacing)))
    return [
        (
            start[0] + (end[0] - start[0]) * index / steps,
            start[1] + (end[1] - start[1]) * index / steps,
        )
        for index in range(steps + 1)
    ]


def route_distance_m(waypoints: Sequence[dict[str, float]]) -> float:
    total = 0.0
    for first, second in zip(waypoints, waypoints[1:]):
        x, y = ll_to_xy(
            second["latitude"], second["longitude"], first["latitude"], first["longitude"]
        )
        total += math.hypot(x, y)
    return total


def polygon_area_m2(vertices: Sequence[PointLL]) -> float:
    if len(vertices) < 3:
        return 0.0
    origin = local_origin(vertices)
    xy = [ll_to_xy(*point, *origin) for point in vertices]
    doubled = sum(
        first[0] * second[1] - second[0] * first[1]
        for first, second in zip(xy, xy[1:] + xy[:1])
    )
    return abs(doubled) / 2.0


def point_in_polygon_xy(point: PointXY, polygon: Sequence[PointXY]) -> bool:
    """Return True for points inside or on a polygon boundary."""
    if len(polygon) < 3:
        return False
    x, y = point
    inside = False
    for first, second in zip(polygon, list(polygon[1:]) + [polygon[0]]):
        ax, ay = first
        bx, by = second
        cross = (x - ax) * (by - ay) - (y - ay) * (bx - ax)
        if abs(cross) < 1e-9 and min(ax, bx) - 1e-9 <= x <= max(ax, bx) + 1e-9 and min(
            ay, by
        ) - 1e-9 <= y <= max(ay, by) + 1e-9:
            return True
        if (ay > y) != (by > y):
            crossing_x = (bx - ax) * (y - ay) / (by - ay) + ax
            if x < crossing_x:
                inside = not inside
    return inside


def segments_intersect_xy(a: PointXY, b: PointXY, c: PointXY, d: PointXY) -> bool:
    def orientation(p: PointXY, q: PointXY, r: PointXY) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    def on_segment(p: PointXY, q: PointXY, r: PointXY) -> bool:
        return (
            min(p[0], r[0]) - 1e-9 <= q[0] <= max(p[0], r[0]) + 1e-9
            and min(p[1], r[1]) - 1e-9 <= q[1] <= max(p[1], r[1]) + 1e-9
        )

    values = (orientation(a, b, c), orientation(a, b, d), orientation(c, d, a), orientation(c, d, b))
    if values[0] * values[1] < 0 and values[2] * values[3] < 0:
        return True
    return any(
        abs(value) < 1e-9 and on_segment(*triple)
        for value, triple in zip(values, ((a, c, b), (a, d, b), (c, a, d), (c, b, d)))
    )


def segment_intersects_polygon_xy(start: PointXY, end: PointXY, polygon: Sequence[PointXY]) -> bool:
    if point_in_polygon_xy(start, polygon) or point_in_polygon_xy(end, polygon):
        return True
    return any(
        segments_intersect_xy(start, end, first, second)
        for first, second in zip(polygon, list(polygon[1:]) + [polygon[0]])
    )
