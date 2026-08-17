"""Boustrophedon coverage planners for small agricultural fields."""

from __future__ import annotations

import math
from typing import Sequence

from .geometry import PointLL, ll_to_xy, points_to_waypoints, polygon_area_m2, positions, unit, xy_to_ll

DEFAULT_FIELD_CORNERS = {
    "nw": (42.614491711416974, -5.563585388952936),
    "ne": (42.61448202489248, -5.563453034080995),
    "sw": (42.61438408328242, -5.563612444921233),
    "se": (42.61436524833972, -5.563474971352587),
}


def _coverage_info(
    vertices: Sequence[PointLL], row_lengths: Sequence[float], length_m: float | None, width_m: float | None
) -> dict[str, float | int | None]:
    return {
        "length_m": length_m,
        "width_m": width_m,
        "area_m2": polygon_area_m2(vertices),
        "coverage_rows": len(row_lengths),
        "average_row_length_m": sum(row_lengths) / len(row_lengths) if row_lengths else 0.0,
    }


def generate_zigzag_rect(
    corners: dict[str, PointLL] | None = None,
    row_spacing_m: float = 0.75,
    point_spacing_m: float = 1.0,
    start_corner: str = "nw",
    row_direction_deg: float | None = None,
) -> tuple[list[dict[str, float]], dict[str, float | int | None]]:
    """Generate a sampled zigzag route for a quadrilateral field.

    The corner labels define two local axes from the selected start corner.
    This preserves the original GeoZigzag behavior for its rectangular field
    editor; arbitrary field shapes should use :func:`generate_zigzag_polygon`.
    """
    if row_spacing_m <= 0 or point_spacing_m <= 0:
        raise ValueError("Row spacing and waypoint spacing must be positive.")
    corners = corners or DEFAULT_FIELD_CORNERS
    start_corner = (start_corner or "nw").lower()
    corner_map = {
        "nw": ("nw", "ne", "sw"),
        "ne": ("ne", "nw", "se"),
        "sw": ("sw", "se", "nw"),
        "se": ("se", "sw", "ne"),
    }
    if start_corner not in corner_map:
        raise ValueError("start_corner must be one of nw, ne, sw or se.")
    missing = set(corner_map) - set(corners)
    if missing:
        raise ValueError(f"Missing field corners: {', '.join(sorted(missing))}.")

    origin_key, width_key, length_key = corner_map[start_corner]
    origin = corners[origin_key]
    width_xy = ll_to_xy(*corners[width_key], *origin)
    length_xy = ll_to_xy(*corners[length_key], *origin)
    width_m = math.hypot(*width_xy)
    length_m = math.hypot(*length_xy)
    width_unit = unit(width_xy)
    length_unit = unit(length_xy)

    rows_along_width = False
    if row_direction_deg is not None:
        def axis_azimuth(vector: tuple[float, float]) -> float:
            return math.degrees(math.atan2(vector[0], vector[1])) % 180.0

        def angle_difference(first: float, second: float) -> float:
            raw = abs((first - second) % 180.0)
            return min(raw, 180.0 - raw)

        desired = row_direction_deg % 180.0
        rows_along_width = angle_difference(desired, axis_azimuth(width_xy)) < angle_difference(
            desired, axis_azimuth(length_xy)
        )

    if rows_along_width:
        sweep_unit, row_unit = length_unit, width_unit
        sweep_m, row_m = length_m, width_m
    else:
        sweep_unit, row_unit = width_unit, length_unit
        sweep_m, row_m = width_m, length_m

    def field_point(sweep: float, row: float) -> PointLL:
        x = sweep_unit[0] * sweep + row_unit[0] * row
        y = sweep_unit[1] * sweep + row_unit[1] * row
        return xy_to_ll(x, y, *origin)

    sweep_positions = positions(0.0, sweep_m, row_spacing_m)
    points: list[PointLL] = []
    forward = True
    for index, sweep in enumerate(sweep_positions):
        row_values = positions(0.0, row_m, point_spacing_m)
        if not forward:
            row_values.reverse()
        points.extend(field_point(sweep, row) for row in row_values)
        if index < len(sweep_positions) - 1:
            connector = positions(sweep, sweep_positions[index + 1], point_spacing_m)[1:]
            points.extend(field_point(value, row_values[-1]) for value in connector)
        forward = not forward

    vertices = [corners["nw"], corners["ne"], corners["se"], corners["sw"]]
    return points_to_waypoints(points), _coverage_info(
        vertices, [row_m] * len(sweep_positions), max(width_m, length_m), min(width_m, length_m)
    )


def generate_zigzag_polygon(
    vertices: list[PointLL],
    row_spacing_m: float = 0.75,
    point_spacing_m: float = 1.0,
    row_direction_deg: float | None = None,
) -> tuple[list[dict[str, float]], dict[str, float | int | None]]:
    """Generate parallel sweep segments clipped to an arbitrary simple polygon.

    Concave polygons may yield multiple disjoint intervals on one sweep line.
    Each interval is sampled and alternated; connectors are straight planning
    segments and must be checked separately if strict in-polygon motion is
    required.
    """
    if len(vertices) < 3:
        raise ValueError("At least three polygon vertices are required.")
    if row_spacing_m <= 0 or point_spacing_m <= 0:
        raise ValueError("Row spacing and waypoint spacing must be positive.")
    if polygon_area_m2(vertices) < 1e-6:
        raise ValueError("The field polygon is degenerate.")

    origin = vertices[0]
    polygon_xy = [ll_to_xy(*point, *origin) for point in vertices]
    if row_direction_deg is None:
        longest = max(
            zip(polygon_xy, polygon_xy[1:] + polygon_xy[:1]),
            key=lambda edge: math.dist(*edge),
        )
        row_direction_deg = math.degrees(
            math.atan2(longest[1][0] - longest[0][0], longest[1][1] - longest[0][1])
        ) % 180.0

    row_direction = math.radians(row_direction_deg % 180.0)
    direction_sin = math.sin(row_direction)
    direction_cos = math.cos(row_direction)

    def project_row(x: float, y: float) -> float:
        return x * direction_sin + y * direction_cos

    def project_sweep(x: float, y: float) -> float:
        return x * direction_cos - y * direction_sin

    def sweep_to_xy(sweep: float, row: float) -> tuple[float, float]:
        return sweep * direction_cos + row * direction_sin, -sweep * direction_sin + row * direction_cos

    sweep_values = [project_sweep(*point) for point in polygon_xy]
    sweep_positions = positions(min(sweep_values), max(sweep_values), row_spacing_m)

    def intersections(sweep_value: float) -> list[float]:
        found: list[float] = []
        for start, end in zip(polygon_xy, polygon_xy[1:] + polygon_xy[:1]):
            start_projection = project_sweep(*start)
            end_projection = project_sweep(*end)
            # Half-open edge handling avoids counting a shared vertex twice.
            if abs(end_projection - start_projection) < 1e-9:
                continue
            low, high = sorted((start_projection, end_projection))
            if not (low <= sweep_value < high or abs(sweep_value - max(sweep_values)) < 1e-9 and low < sweep_value <= high):
                continue
            fraction = (sweep_value - start_projection) / (end_projection - start_projection)
            x = start[0] + fraction * (end[0] - start[0])
            y = start[1] + fraction * (end[1] - start[1])
            found.append(project_row(x, y))
        found.sort()
        return found

    points: list[PointLL] = []
    row_lengths: list[float] = []
    forward = True
    for sweep in sweep_positions:
        row_intersections = intersections(sweep)
        intervals = list(zip(row_intersections[0::2], row_intersections[1::2]))
        if not intervals:
            continue
        if not forward:
            intervals.reverse()
        for low, high in intervals:
            start, end = (low, high) if forward else (high, low)
            row_values = positions(start, end, point_spacing_m)
            points.extend(xy_to_ll(*sweep_to_xy(sweep, row), *origin) for row in row_values)
            row_lengths.append(abs(end - start))
        forward = not forward

    if not points:
        raise ValueError("No valid sweep segment intersects the polygon.")
    return points_to_waypoints(points), _coverage_info(vertices, row_lengths, None, None)
