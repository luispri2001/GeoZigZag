"""Direct, semantic-cost, and optional OSRM mission routing."""

from __future__ import annotations

import heapq
import json
import math
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

from .geometry import (
    PointLL,
    ll_to_xy,
    local_origin,
    point_in_polygon_xy,
    points_to_waypoints,
    segment_points_xy,
    xy_to_ll,
)

LANDCOVER_COST = {
    "road": 1.0,
    "track": 1.5,
    "pastizal": 10.0,
    "arbustivo": 30.0,
    "matorral": 80.0,
    "water": 1000.0,
}

SEMANTIC_ZONE_COST = {
    "building": float("inf"),
    "water": float("inf"),
    "forest": 80.0,
    "scrub": 45.0,
}


class RouteNotFound(RuntimeError):
    """Raised when no admissible path exists."""


class ExternalRoutingError(RuntimeError):
    """Raised when an optional external routing service cannot return a route."""


@dataclass(frozen=True)
class RoutedPath:
    points: list[PointLL]
    snap_distances_m: list[float]
    source: str


class RouteProvider(Protocol):
    def route(self, points: Sequence[PointLL], profile: str = "driving", route_key: str | None = None) -> RoutedPath:
        ...


def load_geojson(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def feature_by_id(geojson: dict[str, Any], feature_id: str) -> PointLL:
    for feature in geojson.get("features", []):
        if feature.get("id") == feature_id:
            lon, lat = feature["geometry"]["coordinates"]
            return float(lat), float(lon)
    raise KeyError(f"Unknown feature id: {feature_id}")


def generate_direct_route(waypoints: list[PointLL], interval_m: float = 10.0) -> list[PointLL]:
    if interval_m <= 0:
        raise ValueError("Route interval must be positive.")
    if len(waypoints) < 2:
        return waypoints
    origin = local_origin(waypoints)
    xy_points = [ll_to_xy(*point, *origin) for point in waypoints]
    route_xy: list[tuple[float, float]] = []
    for index in range(len(xy_points) - 1):
        segment = segment_points_xy(xy_points[index], xy_points[index + 1], interval_m)
        route_xy.extend(segment if index == 0 else segment[1:])
    return [xy_to_ll(x, y, *origin) for x, y in route_xy]


def generate_direct_route_from_ids(
    geojson: dict[str, Any], feature_ids: list[str], interval_m: float = 10.0
) -> list[dict[str, float]]:
    points = [feature_by_id(geojson, feature_id) for feature_id in feature_ids]
    return points_to_waypoints(generate_direct_route(points, interval_m))


def _astar(
    grid: list[list[float]], start: tuple[int, int], goal: tuple[int, int]
) -> list[tuple[int, int]]:
    if not grid or not grid[0] or math.isinf(grid[start[0]][start[1]]) or math.isinf(grid[goal[0]][goal[1]]):
        return []
    height = len(grid)
    width = len(grid[0])
    neighbors = ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1))
    traversable_costs = [value for row in grid for value in row if not math.isinf(value)]
    minimum_cost = min(traversable_costs, default=1.0)
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
        for delta_row, delta_col in neighbors:
            row = current[0] + delta_row
            col = current[1] + delta_col
            if not (0 <= row < height and 0 <= col < width) or math.isinf(grid[row][col]):
                continue
            diagonal = bool(delta_row and delta_col)
            if diagonal and (
                math.isinf(grid[current[0]][col]) or math.isinf(grid[row][current[1]])
            ):
                continue
            step = math.sqrt(2.0) if diagonal else 1.0
            next_cost = cost_so_far[current] + grid[row][col] * step
            next_cell = (row, col)
            if next_cost < cost_so_far.get(next_cell, float("inf")):
                cost_so_far[next_cell] = next_cost
                heuristic = minimum_cost * math.hypot(goal[0] - row, goal[1] - col)
                heapq.heappush(frontier, (next_cost + heuristic, next_cell))
                came_from[next_cell] = current
    return []


def generate_cost_route(
    geojson: dict[str, Any],
    feature_ids: list[str],
    resolution_m: float = 5.0,
    forbidden_zones: Sequence[Sequence[PointLL]] | None = None,
    padding_m: float = 50.0,
    semantic_zones: Sequence[dict[str, Any]] | None = None,
) -> list[dict[str, float]]:
    """Route ordered semantic targets on a local 8-connected cost grid.

    Point features influence circular neighborhoods: two cells for ordinary
    land-cover labels and four for water. Legacy forbidden polygons and
    building/water semantic zones are impassable; forest zones carry a high
    traversal cost. The objective is the sum of cell cost multiplied by
    cardinal/diagonal step length; it is a planning proxy, not a learned
    traversability model.
    """
    if resolution_m <= 0:
        raise ValueError("Grid resolution must be positive.")
    if len(feature_ids) < 2:
        raise ValueError("At least two mission targets are required.")
    waypoints = [feature_by_id(geojson, feature_id) for feature_id in feature_ids]
    features: list[tuple[float, float, str]] = []
    for feature in geojson.get("features", []):
        if feature.get("geometry", {}).get("type", "Point") != "Point":
            continue
        lon, lat = feature["geometry"]["coordinates"]
        label = str(feature.get("properties", {}).get("landcover", "pastizal"))
        features.append((float(lat), float(lon), label))

    normalized_semantic_zones: list[tuple[str, Sequence[PointLL]]] = []
    for zone in semantic_zones or []:
        kind = str(zone.get("kind", "building")).lower()
        if kind not in SEMANTIC_ZONE_COST:
            raise ValueError(
                f"Unknown semantic zone kind {kind!r}; expected building, water, forest or scrub."
            )
        ring = zone.get("ring")
        if not isinstance(ring, Sequence) or isinstance(ring, (str, bytes)) or len(ring) < 3:
            raise ValueError("A semantic zone ring needs at least three coordinates.")
        normalized_semantic_zones.append((kind, ring))

    zone_points = [point for zone in forbidden_zones or [] for point in zone]
    zone_points.extend(
        point for _, ring in normalized_semantic_zones for point in ring
    )
    all_points = waypoints + [(lat, lon) for lat, lon, _ in features] + zone_points
    origin = local_origin(all_points)
    xy_points = [ll_to_xy(*point, *origin) for point in all_points]
    min_x = min(x for x, _ in xy_points) - padding_m
    max_x = max(x for x, _ in xy_points) + padding_m
    min_y = min(y for _, y in xy_points) - padding_m
    max_y = max(y for _, y in xy_points) + padding_m
    width = max(3, int(math.ceil((max_x - min_x) / resolution_m)) + 1)
    height = max(3, int(math.ceil((max_y - min_y) / resolution_m)) + 1)
    grid = [[LANDCOVER_COST["pastizal"] for _ in range(width)] for _ in range(height)]

    def to_cell(point: PointLL) -> tuple[int, int]:
        x, y = ll_to_xy(*point, *origin)
        return (
            min(height - 1, max(0, int(round((y - min_y) / resolution_m)))),
            min(width - 1, max(0, int(round((x - min_x) / resolution_m)))),
        )

    def to_ll(cell: tuple[int, int]) -> PointLL:
        row, col = cell
        return xy_to_ll(min_x + col * resolution_m, min_y + row * resolution_m, *origin)

    influence: dict[tuple[int, int], list[float]] = {}
    for lat, lon, landcover in features:
        row, col = to_cell((lat, lon))
        radius = 4 if landcover == "water" else 2
        value = LANDCOVER_COST.get(landcover, LANDCOVER_COST["pastizal"])
        for grid_row in range(max(0, row - radius), min(height, row + radius + 1)):
            for grid_col in range(max(0, col - radius), min(width, col + radius + 1)):
                if math.hypot(grid_row - row, grid_col - col) <= radius:
                    influence.setdefault((grid_row, grid_col), []).append(value)
    for (row, col), values in influence.items():
        grid[row][col] = max(values)

    zones_xy = [[ll_to_xy(*point, *origin) for point in zone] for zone in forbidden_zones or []]
    semantic_zones_xy = [
        (kind, [ll_to_xy(*point, *origin) for point in ring])
        for kind, ring in normalized_semantic_zones
    ]
    for row in range(height):
        for col in range(width):
            point = (min_x + col * resolution_m, min_y + row * resolution_m)
            if any(point_in_polygon_xy(point, zone) for zone in zones_xy):
                grid[row][col] = float("inf")
                continue
            for kind, zone in semantic_zones_xy:
                if point_in_polygon_xy(point, zone):
                    grid[row][col] = max(grid[row][col], SEMANTIC_ZONE_COST[kind])

    route: list[PointLL] = []
    for first, second in zip(waypoints, waypoints[1:]):
        path = _astar(grid, to_cell(first), to_cell(second))
        if not path:
            raise RouteNotFound("No cost-aware path avoids the configured forbidden zones.")
        segment = [to_ll(cell) for cell in path]
        # Preserve exact semantic target coordinates while retaining grid geometry.
        segment[0] = first
        segment[-1] = second
        route.extend(segment if not route else segment[1:])
    return points_to_waypoints(route)


class OSRMClient:
    """Minimal OSRM route-service client with injectable transport for tests."""

    def __init__(
        self,
        base_url: str = "https://router.project-osrm.org",
        timeout_s: float = 12.0,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self._opener = opener or urllib.request.urlopen

    def route(self, points: Sequence[PointLL], profile: str = "driving", route_key: str | None = None) -> RoutedPath:
        if len(points) < 2:
            raise ValueError("OSRM routing needs at least two points.")
        coordinates = ";".join(f"{lon:.8f},{lat:.8f}" for lat, lon in points)
        query = urllib.parse.urlencode({"overview": "full", "geometries": "geojson"})
        url = f"{self.base_url}/route/v1/{urllib.parse.quote(profile)}/{coordinates}?{query}"
        try:
            with self._opener(url, timeout=self.timeout_s) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise ExternalRoutingError(f"OSRM request failed: {error}") from error
        if payload.get("code") != "Ok" or not payload.get("routes"):
            raise ExternalRoutingError(payload.get("message") or payload.get("code") or "OSRM returned no route.")
        coordinates_out = payload["routes"][0]["geometry"]["coordinates"]
        route_points = [(float(lat), float(lon)) for lon, lat in coordinates_out]
        snap = [float(item.get("distance", 0.0)) for item in payload.get("waypoints", [])]
        return RoutedPath(route_points, snap, "osrm-live")


class CachedOSRMClient:
    """Read versioned OSRM-like routes for deterministic offline evaluation."""

    def __init__(self, fixture_path: str | Path) -> None:
        self.fixture_path = Path(fixture_path)
        self.data = json.loads(self.fixture_path.read_text(encoding="utf-8"))

    def route(self, points: Sequence[PointLL], profile: str = "driving", route_key: str | None = None) -> RoutedPath:
        if not route_key or route_key not in self.data.get("routes", {}):
            raise ExternalRoutingError(f"No cached OSRM route named {route_key!r}.")
        record = self.data["routes"][route_key]
        route_points = [(float(point[0]), float(point[1])) for point in record["points"]]
        return RoutedPath(
            route_points,
            [float(value) for value in record.get("snap_distances_m", [])],
            f"osrm-cache:{self.data.get('metadata', {}).get('version', 'unknown')}",
        )


def generate_osrm_route(
    points: Sequence[PointLL],
    provider: RouteProvider,
    profile: str = "driving",
    route_key: str | None = None,
    max_snap_m: float | None = None,
) -> tuple[list[dict[str, float]], dict[str, Any]]:
    routed = provider.route(points, profile=profile, route_key=route_key)
    worst_snap = max(routed.snap_distances_m, default=0.0)
    if max_snap_m is not None and worst_snap > max_snap_m:
        raise ExternalRoutingError(
            f"OSRM snap distance {worst_snap:.2f} m exceeds the {max_snap_m:.2f} m limit."
        )
    return points_to_waypoints(routed.points), {
        "source": routed.source,
        "snap_distances_m": routed.snap_distances_m,
        "max_snap_distance_m": worst_snap,
    }
