"""Integrated route and coverage planning primitives.

The module merges the two source projects at the algorithmic level:
- zigzag coverage planning from field boundaries.
- point-to-point georouting from semantic GeoJSON waypoints.

It deliberately uses only the Python standard library so the tool can run on a
clean machine.
"""

from __future__ import annotations

import csv
import heapq
import json
import math
from pathlib import Path
from typing import Iterable

EARTH_RADIUS_M = 6_378_137.0

DEFAULT_FIELD_CORNERS = {
    "nw": (42.614491711416974, -5.563585388952936),
    "ne": (42.61448202489248, -5.563453034080995),
    "sw": (42.61438408328242, -5.563612444921233),
    "se": (42.61436524833972, -5.563474971352587),
}

LANDCOVER_COST = {
    "road": 1.0,
    "track": 1.5,
    "pastizal": 10.0,
    "arbustivo": 30.0,
    "matorral": 80.0,
    "water": 1000.0,
}


def _normalize_angle(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


def _local_origin(points: Iterable[tuple[float, float]]) -> tuple[float, float]:
    pts = list(points)
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def _ll_to_xy(
    lat: float,
    lon: float,
    origin_lat: float,
    origin_lon: float,
) -> tuple[float, float]:
    scale = math.cos(math.radians(origin_lat))
    x = math.radians(lon - origin_lon) * EARTH_RADIUS_M * scale
    y = math.radians(lat - origin_lat) * EARTH_RADIUS_M
    return x, y


def _xy_to_ll(
    x: float,
    y: float,
    origin_lat: float,
    origin_lon: float,
) -> tuple[float, float]:
    scale = math.cos(math.radians(origin_lat))
    lat = origin_lat + math.degrees(y / EARTH_RADIUS_M)
    lon = origin_lon + math.degrees(x / (EARTH_RADIUS_M * scale))
    return lat, lon


def _unit(vec: tuple[float, float]) -> tuple[float, float]:
    length = math.hypot(vec[0], vec[1])
    if length < 1e-9:
        raise ValueError("Degenerate geometry: repeated coordinates.")
    return vec[0] / length, vec[1] / length


def _positions(start: float, end: float, spacing: float) -> list[float]:
    if spacing <= 0:
        raise ValueError("Spacing must be positive.")
    direction = 1.0 if end >= start else -1.0
    values = []
    value = start
    while (direction > 0 and value < end) or (direction < 0 and value > end):
        values.append(value)
        value += direction * spacing
    if not values or abs(values[-1] - end) > 1e-6:
        values.append(end)
    return values


def _dedupe(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not points:
        return []
    cleaned = [points[0]]
    for point in points[1:]:
        if abs(point[0] - cleaned[-1][0]) > 1e-11 or abs(point[1] - cleaned[-1][1]) > 1e-11:
            cleaned.append(point)
    return cleaned


def _yaw_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    origin_lat, origin_lon = a
    bx, by = _ll_to_xy(b[0], b[1], origin_lat, origin_lon)
    return _normalize_angle(math.atan2(by, bx))


def points_to_waypoints(points: list[tuple[float, float]]) -> list[dict[str, float]]:
    """Attach ROS ENU yaw to latitude/longitude points."""
    cleaned = _dedupe(points)
    waypoints = []
    for index, point in enumerate(cleaned):
        if index < len(cleaned) - 1:
            yaw = _yaw_between(point, cleaned[index + 1])
        elif waypoints:
            yaw = waypoints[-1]["yaw"]
        else:
            yaw = 0.0
        waypoints.append(
            {
                "latitude": float(point[0]),
                "longitude": float(point[1]),
                "yaw": float(yaw),
            }
        )
    return waypoints


def yaw_to_quaternion(yaw: float) -> tuple[float, float, float, float]:
    half = yaw / 2.0
    return 0.0, 0.0, math.sin(half), math.cos(half)


def _segment_points_xy(
    start_xy: tuple[float, float],
    end_xy: tuple[float, float],
    spacing: float,
) -> list[tuple[float, float]]:
    distance = math.hypot(end_xy[0] - start_xy[0], end_xy[1] - start_xy[1])
    if distance <= spacing:
        return [start_xy, end_xy]
    steps = max(1, int(math.ceil(distance / spacing)))
    return [
        (
            start_xy[0] + (end_xy[0] - start_xy[0]) * i / steps,
            start_xy[1] + (end_xy[1] - start_xy[1]) * i / steps,
        )
        for i in range(steps + 1)
    ]


def generate_zigzag_rect(
    corners: dict[str, tuple[float, float]] | None = None,
    row_spacing_m: float = 0.75,
    point_spacing_m: float = 1.0,
    start_corner: str = "nw",
    row_direction_deg: float | None = None,
) -> tuple[list[dict[str, float]], dict[str, float]]:
    """Generate a boustrophedon coverage route for a quadrilateral field."""
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

    origin_key, width_key, length_key = corner_map[start_corner]
    origin = corners[origin_key]
    origin_lat, origin_lon = origin
    width_xy = _ll_to_xy(*corners[width_key], origin_lat, origin_lon)
    length_xy = _ll_to_xy(*corners[length_key], origin_lat, origin_lon)

    width_m = math.hypot(*width_xy)
    length_m = math.hypot(*length_xy)
    width_unit = _unit(width_xy)
    length_unit = _unit(length_xy)

    rows_along_width = False
    if row_direction_deg is not None:
        def axis_azimuth(vec: tuple[float, float]) -> float:
            return math.degrees(math.atan2(vec[0], vec[1])) % 180.0

        def angle_diff(a: float, b: float) -> float:
            raw = abs((a - b) % 180.0)
            return min(raw, 180.0 - raw)

        desired = row_direction_deg % 180.0
        rows_along_width = angle_diff(desired, axis_azimuth(width_xy)) < angle_diff(desired, axis_azimuth(length_xy))

    if rows_along_width:
        sweep_unit, row_unit = length_unit, width_unit
        sweep_m, row_m = length_m, width_m
    else:
        sweep_unit, row_unit = width_unit, length_unit
        sweep_m, row_m = width_m, length_m

    def field_point(sweep: float, row: float) -> tuple[float, float]:
        x = sweep_unit[0] * sweep + row_unit[0] * row
        y = sweep_unit[1] * sweep + row_unit[1] * row
        return _xy_to_ll(x, y, origin_lat, origin_lon)

    points = []
    forward = True
    for index, sweep in enumerate(_positions(0.0, sweep_m, row_spacing_m)):
        row_values = _positions(0.0, row_m, point_spacing_m)
        if not forward:
            row_values = list(reversed(row_values))
        points.extend(field_point(sweep, row) for row in row_values)

        next_sweeps = _positions(0.0, sweep_m, row_spacing_m)
        if index < len(next_sweeps) - 1:
            next_sweep = next_sweeps[index + 1]
            end_row = row_values[-1]
            bridge = _positions(sweep, next_sweep, point_spacing_m)[1:]
            points.extend(field_point(value, end_row) for value in bridge)
        forward = not forward

    return points_to_waypoints(points), {
        "length_m": max(width_m, length_m),
        "width_m": min(width_m, length_m),
        "coverage_rows": len(_positions(0.0, sweep_m, row_spacing_m)),
    }


def generate_zigzag_polygon(
    vertices: list[tuple[float, float]],
    row_spacing_m: float = 0.75,
    point_spacing_m: float = 1.0,
    row_direction_deg: float | None = None,
) -> tuple[list[dict[str, float]], dict[str, float | None]]:
    """Generate zigzag coverage for an arbitrary polygon."""
    if len(vertices) < 3:
        raise ValueError("At least three polygon vertices are required.")

    origin_lat, origin_lon = vertices[0]
    poly_xy = [_ll_to_xy(lat, lon, origin_lat, origin_lon) for lat, lon in vertices]

    if row_direction_deg is None:
        best_len = 0.0
        best_ang = 0.0
        for index, start in enumerate(poly_xy):
            end = poly_xy[(index + 1) % len(poly_xy)]
            length = math.hypot(end[0] - start[0], end[1] - start[1])
            if length > best_len:
                best_len = length
                best_ang = math.degrees(math.atan2(end[0] - start[0], end[1] - start[1])) % 180.0
        row_dir = math.radians(best_ang)
    else:
        row_dir = math.radians(row_direction_deg % 180.0)

    dir_sin = math.sin(row_dir)
    dir_cos = math.cos(row_dir)

    def proj_row(x: float, y: float) -> float:
        return x * dir_sin + y * dir_cos

    def proj_sweep(x: float, y: float) -> float:
        return x * dir_cos - y * dir_sin

    def sweep_to_xy(sweep: float, row: float) -> tuple[float, float]:
        return sweep * dir_cos + row * dir_sin, -sweep * dir_sin + row * dir_cos

    sweep_vals = [proj_sweep(x, y) for x, y in poly_xy]
    sweep_positions = _positions(min(sweep_vals), max(sweep_vals), row_spacing_m)

    def intersections(sweep_value: float) -> list[float]:
        found = []
        for index, start in enumerate(poly_xy):
            end = poly_xy[(index + 1) % len(poly_xy)]
            pa = proj_sweep(*start)
            pb = proj_sweep(*end)
            if abs(pb - pa) < 1e-9:
                continue
            t = (sweep_value - pa) / (pb - pa)
            if 0.0 <= t <= 1.0:
                x = start[0] + t * (end[0] - start[0])
                y = start[1] + t * (end[1] - start[1])
                found.append(proj_row(x, y))
        return sorted(found)

    points = []
    forward = True
    previous_row = 0.0
    used_rows = 0
    for index, sweep in enumerate(sweep_positions):
        row_intersections = intersections(sweep)
        if len(row_intersections) < 2:
            continue

        row_start = row_intersections[0] if forward else row_intersections[-1]
        row_end = row_intersections[-1] if forward else row_intersections[0]
        row_values = _positions(row_start, row_end, point_spacing_m)
        for row in row_values:
            points.append(_xy_to_ll(*sweep_to_xy(sweep, row), origin_lat, origin_lon))
        used_rows += 1

        if index < len(sweep_positions) - 1:
            bridge_values = _positions(sweep, sweep_positions[index + 1], point_spacing_m)[1:]
            previous_row = row_values[-1]
            for next_sweep in bridge_values:
                points.append(_xy_to_ll(*sweep_to_xy(next_sweep, previous_row), origin_lat, origin_lon))
        forward = not forward

    return points_to_waypoints(points), {
        "length_m": None,
        "width_m": None,
        "coverage_rows": used_rows,
    }


def load_geojson(path: str | Path) -> dict:
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def _feature_by_id(geojson: dict, feature_id: str) -> tuple[float, float]:
    for feature in geojson.get("features", []):
        if feature.get("id") == feature_id:
            lon, lat = feature["geometry"]["coordinates"]
            return lat, lon
    raise KeyError(f"Unknown feature id: {feature_id}")


def generate_direct_route(
    waypoints: list[tuple[float, float]],
    interval_m: float = 10.0,
) -> list[tuple[float, float]]:
    if len(waypoints) < 2:
        return waypoints
    origin_lat, origin_lon = _local_origin(waypoints)
    xy_points = [_ll_to_xy(lat, lon, origin_lat, origin_lon) for lat, lon in waypoints]
    route_xy = []
    for index in range(len(xy_points) - 1):
        segment = _segment_points_xy(xy_points[index], xy_points[index + 1], interval_m)
        route_xy.extend(segment if index == 0 else segment[1:])
    return [_xy_to_ll(x, y, origin_lat, origin_lon) for x, y in route_xy]


def generate_direct_route_from_ids(
    geojson: dict,
    feature_ids: list[str],
    interval_m: float = 10.0,
) -> list[dict[str, float]]:
    waypoints = [_feature_by_id(geojson, feature_id) for feature_id in feature_ids]
    return points_to_waypoints(generate_direct_route(waypoints, interval_m))


def _astar(grid: list[list[float]], start: tuple[int, int], goal: tuple[int, int]) -> list[tuple[int, int]]:
    height = len(grid)
    width = len(grid[0])
    neighbors = [
        (-1, 0), (1, 0), (0, -1), (0, 1),
        (-1, -1), (-1, 1), (1, -1), (1, 1),
    ]
    frontier = [(0.0, start)]
    came_from: dict[tuple[int, int], tuple[int, int]] = {}
    cost_so_far = {start: 0.0}

    while frontier:
        _, current = heapq.heappop(frontier)
        if current == goal:
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            return list(reversed(path))

        for dy, dx in neighbors:
            row = current[0] + dy
            col = current[1] + dx
            if not (0 <= row < height and 0 <= col < width):
                continue
            step = math.sqrt(2.0) if dy and dx else 1.0
            next_cost = cost_so_far[current] + grid[row][col] * step
            next_cell = (row, col)
            if next_cost < cost_so_far.get(next_cell, float("inf")):
                cost_so_far[next_cell] = next_cost
                heuristic = math.hypot(goal[0] - row, goal[1] - col)
                heapq.heappush(frontier, (next_cost + heuristic, next_cell))
                came_from[next_cell] = current
    return []


def generate_cost_route(
    geojson: dict,
    feature_ids: list[str],
    resolution_m: float = 5.0,
) -> list[dict[str, float]]:
    """Generate a cost-aware route over a small local grid.

    Semantic point labels from GeoJSON alter nearby cell traversal costs. This is
    a lightweight, deterministic counterpart of the OSMnx/costmap path from the
    original georoute-planner repository.
    """
    waypoints = [_feature_by_id(geojson, feature_id) for feature_id in feature_ids]
    features = []
    for feature in geojson.get("features", []):
        lon, lat = feature["geometry"]["coordinates"]
        features.append((lat, lon, feature.get("properties", {}).get("landcover", "pastizal")))

    all_points = waypoints + [(lat, lon) for lat, lon, _ in features]
    origin_lat, origin_lon = _local_origin(all_points)
    xy_points = [_ll_to_xy(lat, lon, origin_lat, origin_lon) for lat, lon in all_points]
    min_x = min(x for x, _ in xy_points) - 50.0
    max_x = max(x for x, _ in xy_points) + 50.0
    min_y = min(y for _, y in xy_points) - 50.0
    max_y = max(y for _, y in xy_points) + 50.0
    width = max(3, int(math.ceil((max_x - min_x) / resolution_m)))
    height = max(3, int(math.ceil((max_y - min_y) / resolution_m)))
    grid = [[10.0 for _ in range(width)] for _ in range(height)]

    def to_cell(lat: float, lon: float) -> tuple[int, int]:
        x, y = _ll_to_xy(lat, lon, origin_lat, origin_lon)
        row = min(height - 1, max(0, int((y - min_y) / resolution_m)))
        col = min(width - 1, max(0, int((x - min_x) / resolution_m)))
        return row, col

    def to_ll(row: int, col: int) -> tuple[float, float]:
        x = min_x + col * resolution_m
        y = min_y + row * resolution_m
        return _xy_to_ll(x, y, origin_lat, origin_lon)

    for lat, lon, landcover in features:
        row, col = to_cell(lat, lon)
        radius = 2 if landcover != "water" else 4
        for rr in range(max(0, row - radius), min(height, row + radius + 1)):
            for cc in range(max(0, col - radius), min(width, col + radius + 1)):
                grid[rr][cc] = max(grid[rr][cc], LANDCOVER_COST.get(landcover, 10.0))

    route = []
    for index in range(len(waypoints) - 1):
        path = _astar(grid, to_cell(*waypoints[index]), to_cell(*waypoints[index + 1]))
        if not path:
            segment = generate_direct_route([waypoints[index], waypoints[index + 1]], resolution_m)
        else:
            segment = [to_ll(row, col) for row, col in path]
        route.extend(segment if index == 0 else segment[1:])
    return points_to_waypoints(route)


def summarize_route(waypoints: list[dict[str, float]]) -> dict[str, float | int]:
    total = 0.0
    for index in range(len(waypoints) - 1):
        a = waypoints[index]
        b = waypoints[index + 1]
        bx, by = _ll_to_xy(b["latitude"], b["longitude"], a["latitude"], a["longitude"])
        total += math.hypot(bx, by)
    return {"points": len(waypoints), "distance_m": total}


def export_csv(waypoints: list[dict[str, float]], path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["latitude", "longitude", "yaw", "qx", "qy", "qz", "qw"])
        writer.writeheader()
        for waypoint in waypoints:
            qx, qy, qz, qw = yaw_to_quaternion(waypoint["yaw"])
            writer.writerow({**waypoint, "qx": qx, "qy": qy, "qz": qz, "qw": qw})
    return output


def export_ros_yaml(waypoints: list[dict[str, float]], path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = ["waypoints:"]
    for waypoint in waypoints:
        qx, qy, qz, qw = yaw_to_quaternion(waypoint["yaw"])
        lines.extend(
            [
                f"  - latitude: {waypoint['latitude']:.12f}",
                f"    longitude: {waypoint['longitude']:.12f}",
                f"    yaw: {waypoint['yaw']:.12f}",
                f"    orientation: {{qx: {qx:.12f}, qy: {qy:.12f}, qz: {qz:.12f}, qw: {qw:.12f}}}",
            ]
        )
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output
