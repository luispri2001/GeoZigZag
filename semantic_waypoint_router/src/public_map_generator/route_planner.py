"""Semantic route planning over generated public map layers.

The coverage sweep and eight-connected A* structure are adapted from Luis
Prieto's GeoZigZag project.  This version replaces its synthetic point costs
with the aligned raster layers produced by public-map-generator.
"""

from __future__ import annotations

import csv
import heapq
import json
import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
import yaml
from pyproj import Transformer
from rasterio.features import geometry_mask
from scipy import ndimage
from shapely import affinity
from shapely.geometry import LineString, MultiLineString, Polygon, shape
from shapely.ops import transform as transform_geometry


@dataclass(frozen=True)
class PlanningProfile:
    """Small, explainable set of layers used by the default planner."""

    clearance_m: float = 1.5
    max_slope_deg: float = 25.0
    max_step_m: float = 1.0
    road_preference: float = 0.45
    slope_weight: float = 3.0
    step_weight: float = 2.0
    obstacle_weight: float = 4.0

    @property
    def relevant_layers(self) -> list[str]:
        return [
            "buildings",
            "water",
            "waterways",
            "wetlands",
            "barriers",
            "slope_degrees",
            "max_neighbor_step",
            "obstacle_probability",
            "roads",
        ]

    @property
    def ignored_by_default(self) -> list[str]:
        return [
            "scrub",
            "forest",
            "farmland",
            "grass",
            "ndvi",
            "ndmi",
            "vegetation_prior",
            "wetness_prior",
            "mud_risk",
            "water_accumulation_risk",
        ]


@dataclass
class RoutePlan:
    name: str
    mode: str
    waypoints: list[dict[str, float]]
    metrics: dict[str, Any]
    profile: dict[str, Any]
    targets: list[dict[str, Any]] = field(default_factory=list)

    def geojson(self) -> dict[str, Any]:
        coordinates = [[point["longitude"], point["latitude"]] for point in self.waypoints]
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "name": self.name,
                        "mode": self.mode,
                        **self.metrics,
                    },
                    "geometry": {"type": "LineString", "coordinates": coordinates},
                },
                *[
                    {
                        "type": "Feature",
                        "properties": {
                            "sequence": index,
                            "waypoint_type": "navigation",
                            **point,
                        },
                        "geometry": {
                            "type": "Point",
                            "coordinates": [point["longitude"], point["latitude"]],
                        },
                    }
                    for index, point in enumerate(self.waypoints)
                ],
                *[
                    {
                        "type": "Feature",
                        "properties": {
                            "sequence": index,
                            "waypoint_type": "mission_target",
                            **target,
                        },
                        "geometry": {
                            "type": "Point",
                            "coordinates": [target["longitude"], target["latitude"]],
                        },
                    }
                    for index, target in enumerate(self.targets)
                ],
            ],
        }

    def csv_text(self) -> str:
        buffer = StringIO()
        fields = ["sequence", "latitude", "longitude", "yaw", "qx", "qy", "qz", "qw"]
        writer = csv.DictWriter(buffer, fieldnames=fields)
        writer.writeheader()
        for index, point in enumerate(self.waypoints):
            writer.writerow({"sequence": index, **point})
        return buffer.getvalue()

    def yaml_text(self) -> str:
        return yaml.safe_dump(
            {
                "route": {
                    "name": self.name,
                    "mode": self.mode,
                    "frame": "wgs84",
                    "metrics": self.metrics,
                    "profile": self.profile,
                    "targets": self.targets,
                    "waypoints": self.waypoints,
                }
            },
            sort_keys=False,
            allow_unicode=True,
        )


def _layer_paths(output_dir: Path) -> dict[str, Path]:
    return {path.stem: path for path in (output_dir / "layers").glob("**/*.tif")}


def _read_aligned(path: Path, reference: rasterio.io.DatasetReader) -> np.ndarray:
    with rasterio.open(path) as source:
        if (
            source.shape != reference.shape
            or source.crs != reference.crs
            or source.transform != reference.transform
        ):
            raise ValueError(f"La capa {path.name} no está alineada con la rejilla del proyecto.")
        return source.read(1, masked=True).filled(np.nan).astype(np.float32)


def _project_geometry(geometry: dict[str, Any], target_crs: Any):
    transformer = Transformer.from_crs("EPSG:4326", target_crs, always_xy=True)
    return transform_geometry(transformer.transform, shape(geometry))


def build_semantic_costmap(
    output_dir: str | Path,
    profile: PlanningProfile | None = None,
    constraint_geometry: dict[str, Any] | None = None,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Build traversal costs and hard blocks from only navigation-relevant layers."""
    root = Path(output_dir)
    profile = profile or PlanningProfile()
    paths = _layer_paths(root)
    reference_path = paths.get("elevation")
    if reference_path is None:
        raise FileNotFoundError("El proyecto no contiene la capa de elevación de referencia.")

    with rasterio.open(reference_path) as reference:
        elevation = reference.read(1, masked=True).filled(np.nan)
        costs = np.ones(reference.shape, dtype=np.float32)
        blocked = ~np.isfinite(elevation)
        used: list[str] = ["elevation"]

        def optional(name: str) -> np.ndarray | None:
            path = paths.get(name)
            if path is None:
                return None
            used.append(name)
            return _read_aligned(path, reference)

        for name in ("buildings", "water", "waterways", "wetlands", "barriers"):
            layer = optional(name)
            if layer is not None:
                blocked |= np.nan_to_num(layer, nan=0.0) > 0.5

        slope = optional("slope_degrees")
        if slope is not None:
            normalized = np.clip(
                np.nan_to_num(slope, nan=profile.max_slope_deg) / profile.max_slope_deg, 0, 1
            )
            costs += profile.slope_weight * normalized
            blocked |= np.nan_to_num(slope, nan=profile.max_slope_deg + 1) > profile.max_slope_deg

        step = optional("max_neighbor_step")
        if step is not None:
            normalized = np.clip(
                np.nan_to_num(step, nan=profile.max_step_m) / profile.max_step_m, 0, 1
            )
            costs += profile.step_weight * normalized
            blocked |= np.nan_to_num(step, nan=profile.max_step_m + 1) > profile.max_step_m

        obstacle = optional("obstacle_probability")
        if obstacle is not None:
            # Weak vegetation priors are deliberately ignored by default. Only
            # strong obstacle evidence changes the cost. Hard blocking is kept
            # for explicit mapped objects because this layer is an uncertain prior.
            strong = np.clip((np.nan_to_num(obstacle, nan=1.0) - 0.5) / 0.5, 0, 1)
            costs += profile.obstacle_weight * strong

        roads = optional("roads")
        if roads is not None:
            road_cells = np.nan_to_num(roads, nan=0.0) > 0.5
            costs[road_cells] *= max(0.1, 1.0 - profile.road_preference)

        if constraint_geometry:
            projected = _project_geometry(constraint_geometry, reference.crs)
            inside = geometry_mask(
                [projected.__geo_interface__],
                out_shape=reference.shape,
                transform=reference.transform,
                invert=True,
            )
            blocked |= ~inside

        pixel_size = max(abs(reference.transform.a), abs(reference.transform.e))
        clearance_cells = max(0, int(math.ceil(profile.clearance_m / pixel_size)))
        if clearance_cells:
            blocked = ndimage.distance_transform_edt(~blocked) <= clearance_cells
        costs[blocked] = np.inf
        metadata = {
            "crs": str(reference.crs),
            "transform": tuple(reference.transform)[:6],
            "shape": reference.shape,
            "resolution_m": pixel_size,
            "used_layers": used,
            "ignored_layers": profile.ignored_by_default,
        }
    return costs, blocked, metadata


def _neighbors(cell: tuple[int, int], shape_: tuple[int, int]) -> Iterable[tuple[int, int, float]]:
    row, col = cell
    for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)):
        nr, nc = row + dr, col + dc
        if 0 <= nr < shape_[0] and 0 <= nc < shape_[1]:
            yield nr, nc, math.sqrt(2.0) if dr and dc else 1.0


def astar(
    costs: np.ndarray, start: tuple[int, int], goal: tuple[int, int]
) -> list[tuple[int, int]]:
    """Eight-connected A* adapted from GeoZigZag's cost-route planner."""
    if not np.isfinite(costs[start]) or not np.isfinite(costs[goal]):
        return []
    frontier: list[tuple[float, tuple[int, int]]] = [(0.0, start)]
    came_from: dict[tuple[int, int], tuple[int, int]] = {}
    distance = {start: 0.0}
    visited: set[tuple[int, int]] = set()
    while frontier:
        _, current = heapq.heappop(frontier)
        if current in visited:
            continue
        visited.add(current)
        if current == goal:
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            return list(reversed(path))
        for nr, nc, step in _neighbors(current, costs.shape):
            if not np.isfinite(costs[nr, nc]):
                continue
            dr, dc = nr - current[0], nc - current[1]
            if (
                dr
                and dc
                and (
                    not np.isfinite(costs[current[0] + dr, current[1]])
                    or not np.isfinite(costs[current[0], current[1] + dc])
                )
            ):
                continue
            next_cell = (nr, nc)
            new_distance = distance[current] + step * float((costs[current] + costs[next_cell]) / 2)
            if new_distance < distance.get(next_cell, math.inf):
                distance[next_cell] = new_distance
                came_from[next_cell] = current
                heuristic = math.hypot(goal[0] - nr, goal[1] - nc)
                heapq.heappush(frontier, (new_distance + heuristic, next_cell))
    return []


def _nearest_free(
    blocked: np.ndarray, cell: tuple[int, int], max_radius: int = 30
) -> tuple[int, int]:
    row = min(max(cell[0], 0), blocked.shape[0] - 1)
    col = min(max(cell[1], 0), blocked.shape[1] - 1)
    if not blocked[row, col]:
        return row, col
    for radius in range(1, max_radius + 1):
        row_min, row_max = max(0, row - radius), min(blocked.shape[0], row + radius + 1)
        col_min, col_max = max(0, col - radius), min(blocked.shape[1], col + radius + 1)
        candidates = np.argwhere(~blocked[row_min:row_max, col_min:col_max])
        if candidates.size:
            candidates += np.array([row_min, col_min])
            index = np.argmin(np.sum((candidates - np.array([row, col])) ** 2, axis=1))
            return tuple(int(value) for value in candidates[index])
    raise ValueError("No hay una celda transitable cerca del punto solicitado.")


def _line_is_free(blocked: np.ndarray, start: tuple[int, int], end: tuple[int, int]) -> bool:
    cells = _line_cells(start, end)
    if any(blocked[cell] for cell in cells):
        return False
    for previous, current in zip(cells, cells[1:], strict=False):
        dr = current[0] - previous[0]
        dc = current[1] - previous[1]
        if (
            dr
            and dc
            and (blocked[previous[0] + dr, previous[1]] or blocked[previous[0], previous[1] + dc])
        ):
            return False
    return True


def _line_cells(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    # Oversampling makes this a conservative supercover for raster cells. It
    # catches cells touched between the integer centers, not just rounded nodes.
    count = 4 * max(abs(end[0] - start[0]), abs(end[1] - start[1])) + 1
    rows = np.rint(np.linspace(start[0], end[0], count)).astype(int)
    cols = np.rint(np.linspace(start[1], end[1], count)).astype(int)
    cells = list(dict.fromkeys(zip(rows.tolist(), cols.tolist(), strict=True)))
    return cells


def _simplify_cells(path: list[tuple[int, int]], blocked: np.ndarray) -> list[tuple[int, int]]:
    if len(path) < 3:
        return path
    result = [path[0]]
    anchor = 0
    while anchor < len(path) - 1:
        candidate = len(path) - 1
        while candidate > anchor + 1 and not _line_is_free(blocked, path[anchor], path[candidate]):
            candidate -= 1
        result.append(path[candidate])
        anchor = candidate
    return result


class SemanticRoutePlanner:
    def __init__(
        self,
        output_dir: str | Path,
        profile: PlanningProfile | None = None,
        constraint_geometry: dict[str, Any] | None = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.profile = profile or PlanningProfile()
        self.paths = _layer_paths(self.output_dir)
        self.reference_path = self.paths.get("elevation")
        if self.reference_path is None:
            raise FileNotFoundError("No se encuentra layers/terrain/elevation.tif.")
        self.costs, self.blocked, self.costmap_metadata = build_semantic_costmap(
            self.output_dir, self.profile, constraint_geometry
        )
        with rasterio.open(self.reference_path) as reference:
            self.transform = reference.transform
            self.crs = reference.crs
        self.to_map = Transformer.from_crs("EPSG:4326", self.crs, always_xy=True)
        self.to_wgs84 = Transformer.from_crs(self.crs, "EPSG:4326", always_xy=True)

    def _xy_to_cell(self, point: tuple[float, float]) -> tuple[int, int]:
        return tuple(int(value) for value in rasterio.transform.rowcol(self.transform, *point))

    def _cell_to_xy(self, cell: tuple[int, int]) -> tuple[float, float]:
        x, y = rasterio.transform.xy(self.transform, cell[0], cell[1])
        return float(x), float(y)

    def _wgs_to_xy(self, lon: float, lat: float) -> tuple[float, float]:
        return self.to_map.transform(lon, lat)

    def _route_cells(
        self,
        targets_xy: list[tuple[float, float]],
        *,
        direct_when_clear: bool = False,
    ) -> list[tuple[int, int]]:
        if len(targets_xy) < 2:
            raise ValueError("La ruta necesita al menos dos puntos.")
        requested_cells = [self._xy_to_cell(point) for point in targets_xy]
        if any(
            row < 0 or col < 0 or row >= self.blocked.shape[0] or col >= self.blocked.shape[1]
            for row, col in requested_cells
        ):
            raise ValueError("Uno de los objetivos queda fuera del proyecto generado.")
        target_cells = [_nearest_free(self.blocked, cell) for cell in requested_cells]
        route: list[tuple[int, int]] = []
        for index in range(len(target_cells) - 1):
            start, goal = target_cells[index], target_cells[index + 1]
            if direct_when_clear and _line_is_free(self.blocked, start, goal):
                segment = _line_cells(start, goal)
            else:
                segment = astar(self.costs, start, goal)
            if not segment:
                raise ValueError(
                    f"No existe paso transitable entre los objetivos {index + 1} y {index + 2}."
                )
            simplified = _simplify_cells(segment, self.blocked)
            route.extend(simplified if not route else simplified[1:])
        if len(route) < 2:
            raise ValueError("El inicio y el final corresponden a la misma celda de la rejilla.")
        return route

    def _waypoints(
        self, route_cells: list[tuple[int, int]], spacing_m: float
    ) -> list[dict[str, float]]:
        if spacing_m <= 0:
            raise ValueError("La separación entre waypoints debe ser positiva.")
        xy = [self._cell_to_xy(cell) for cell in route_cells]
        line = LineString(xy)
        distances = list(np.arange(0.0, line.length, spacing_m))
        if not distances or distances[-1] < line.length:
            distances.append(line.length)
        sampled = [line.interpolate(distance) for distance in distances]
        result = []
        for index, point in enumerate(sampled):
            if index < len(sampled) - 1:
                following = sampled[index + 1]
                yaw = math.atan2(following.y - point.y, following.x - point.x)
            elif result:
                yaw = result[-1]["yaw"]
            else:
                yaw = 0.0
            longitude, latitude = self.to_wgs84.transform(point.x, point.y)
            result.append(
                {
                    "latitude": float(latitude),
                    "longitude": float(longitude),
                    "yaw": float(yaw),
                    "qx": 0.0,
                    "qy": 0.0,
                    "qz": float(math.sin(yaw / 2.0)),
                    "qw": float(math.cos(yaw / 2.0)),
                }
            )
        return result

    def _plan(
        self,
        name: str,
        mode: str,
        targets_xy: list[tuple[float, float]],
        spacing_m: float,
        direct_when_clear: bool = False,
        mission_targets: list[dict[str, Any]] | None = None,
        **metrics: Any,
    ) -> RoutePlan:
        cells = self._route_cells(targets_xy, direct_when_clear=direct_when_clear)
        waypoints = self._waypoints(cells, spacing_m)
        line = LineString([self._cell_to_xy(cell) for cell in cells])
        metrics = {
            "distance_m": round(float(line.length), 2),
            "waypoint_count": len(waypoints),
            "target_count": len(targets_xy),
            **metrics,
        }
        profile = {
            **asdict(self.profile),
            "used_layers": self.costmap_metadata["used_layers"],
            "ignored_by_default": self.costmap_metadata["ignored_layers"],
        }
        return RoutePlan(
            name=name,
            mode=mode,
            waypoints=waypoints,
            metrics=metrics,
            profile=profile,
            targets=mission_targets or [],
        )

    def plan_line(
        self, coordinates: list[list[float]], waypoint_spacing_m: float = 2.0
    ) -> RoutePlan:
        targets = [self._wgs_to_xy(float(lon), float(lat)) for lon, lat in coordinates]
        return self._plan("semantic_route", "point_to_point", targets, waypoint_spacing_m)

    def _semantic_layer_targets(
        self,
        layer_names: list[str],
        minimum_area_m2: float,
        reachable_mask: np.ndarray | None = None,
    ) -> list[tuple[tuple[float, float], dict[str, Any]]]:
        pixel_area = abs(self.transform.a * self.transform.e)
        candidates: list[tuple[tuple[float, float], dict[str, Any]]] = []
        for layer_name in layer_names:
            layer_path = self.paths.get(layer_name)
            if layer_path is None:
                continue
            with rasterio.open(layer_path) as source:
                mask = np.nan_to_num(source.read(1, masked=True).filled(0), nan=0) > 0.5
            labels, count = ndimage.label(mask)
            for label_id in range(1, count + 1):
                cells = np.argwhere(labels == label_id)
                area_m2 = float(len(cells) * pixel_area)
                if area_m2 < minimum_area_m2:
                    continue
                usable = cells[~self.blocked[cells[:, 0], cells[:, 1]]]
                if reachable_mask is not None and len(usable):
                    usable = usable[reachable_mask[usable[:, 0], usable[:, 1]]]
                centroid = cells.mean(axis=0)
                target_relation = "inside_feature"
                distance_to_feature_m = 0.0
                if len(usable):
                    target_index = int(np.sum((usable - centroid) ** 2, axis=1).argmin())
                    target_cell = tuple(int(value) for value in usable[target_index])
                elif reachable_mask is not None and reachable_mask.any():
                    component_mask = labels == label_id
                    distance = ndimage.distance_transform_edt(~component_mask)
                    reachable_cells = np.argwhere(reachable_mask)
                    target_index = int(
                        distance[reachable_cells[:, 0], reachable_cells[:, 1]].argmin()
                    )
                    target_cell = tuple(int(value) for value in reachable_cells[target_index])
                    distance_to_feature_m = float(distance[target_cell] * math.sqrt(pixel_area))
                    if distance_to_feature_m > 250.0:
                        continue
                    target_relation = "approach_to_feature"
                else:
                    continue
                x, y = self._cell_to_xy(target_cell)
                longitude, latitude = self.to_wgs84.transform(x, y)
                candidates.append(
                    (
                        (x, y),
                        {
                            "name": f"{layer_name}_{label_id}",
                            "source": "automatic_semantic_layer",
                            "semantic_layer": layer_name,
                            "target_category": (
                                "resource"
                                if layer_name
                                in {"forest", "scrub", "water", "waterways", "wetlands"}
                                else "land_use"
                            ),
                            "area_m2": round(area_m2, 2),
                            "target_relation": target_relation,
                            "distance_to_feature_m": round(distance_to_feature_m, 2),
                            "longitude": float(longitude),
                            "latitude": float(latitude),
                        },
                    )
                )
        return candidates

    def plan_waypoint_mission(
        self,
        start_lon_lat: tuple[float, float],
        manual_waypoints: list[dict[str, Any]] | None = None,
        semantic_layers: list[str] | None = None,
        waypoint_spacing_m: float = 2.0,
        minimum_semantic_area_m2: float = 25.0,
        max_automatic_targets: int = 20,
    ) -> RoutePlan:
        """Plan through editable targets and optional public semantic features."""
        start_xy = self._wgs_to_xy(*start_lon_lat)
        start_cell = self._xy_to_cell(start_xy)
        if not (
            0 <= start_cell[0] < self.blocked.shape[0]
            and 0 <= start_cell[1] < self.blocked.shape[1]
        ):
            raise ValueError(
                "El origen queda fuera del proyecto. Genera o selecciona un mapa "
                "alrededor del origen."
            )
        start_cell = _nearest_free(self.blocked, start_cell)
        start_xy = self._cell_to_xy(start_cell)

        connectivity = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]])
        components, _ = ndimage.label(~self.blocked, structure=connectivity)
        reachable_component = components[start_cell]
        targets_xy = [start_xy]
        start_lon, start_lat = self.to_wgs84.transform(*start_xy)
        mission_targets: list[dict[str, Any]] = [
            {
                "name": "Origen",
                "source": "user_origin",
                "longitude": float(start_lon),
                "latitude": float(start_lat),
            }
        ]

        for index, waypoint in enumerate(manual_waypoints or [], start=1):
            longitude = float(waypoint["longitude"])
            latitude = float(waypoint["latitude"])
            xy = self._wgs_to_xy(longitude, latitude)
            cell = self._xy_to_cell(xy)
            if not (0 <= cell[0] < self.blocked.shape[0] and 0 <= cell[1] < self.blocked.shape[1]):
                raise ValueError(f"El waypoint manual {index} queda fuera del proyecto.")
            free = _nearest_free(self.blocked, cell)
            if components[free] != reachable_component:
                raise ValueError(f"El waypoint manual {index} no es alcanzable desde el origen.")
            free_xy = self._cell_to_xy(free)
            free_lon, free_lat = self.to_wgs84.transform(*free_xy)
            targets_xy.append(free_xy)
            mission_targets.append(
                {
                    "name": str(waypoint.get("name") or f"WP{index}"),
                    "source": "manual_waypoint",
                    "longitude": float(free_lon),
                    "latitude": float(free_lat),
                }
            )

        automatic = self._semantic_layer_targets(
            semantic_layers or [],
            minimum_semantic_area_m2,
            reachable_mask=components == reachable_component,
        )
        current = targets_xy[-1]
        selected_automatic = []
        while automatic and len(selected_automatic) < max_automatic_targets:
            nearest = min(
                automatic,
                key=lambda item: math.hypot(item[0][0] - current[0], item[0][1] - current[1]),
            )
            selected_automatic.append(nearest)
            automatic.remove(nearest)
            current = nearest[0]
        for xy, metadata in selected_automatic:
            targets_xy.append(xy)
            mission_targets.append(metadata)

        if len(targets_xy) < 2:
            raise ValueError(
                "Añade al menos un waypoint manual o activa una capa con objetivos disponibles."
            )
        return self._plan(
            "waypoint_mission",
            "manual_and_semantic_waypoints",
            targets_xy,
            waypoint_spacing_m,
            mission_targets=mission_targets,
            manual_targets=len(manual_waypoints or []),
            automatic_targets=len(selected_automatic),
            semantic_layers=semantic_layers or [],
        )

    def plan_coverage(
        self,
        polygon_geometry: dict[str, Any],
        row_spacing_m: float = 5.0,
        waypoint_spacing_m: float = 2.0,
        bearing_deg: float | None = None,
    ) -> RoutePlan:
        if row_spacing_m <= 0:
            raise ValueError("La separación entre pasadas debe ser positiva.")
        projected = _project_geometry(polygon_geometry, self.crs)
        if projected.geom_type == "MultiPolygon":
            projected = max(projected.geoms, key=lambda item: item.area)
        if not isinstance(projected, Polygon) or projected.is_empty:
            raise ValueError("La cobertura necesita un polígono válido.")
        if bearing_deg is None:
            rectangle = projected.minimum_rotated_rectangle
            edges = [
                (rectangle.exterior.coords[index], rectangle.exterior.coords[index + 1])
                for index in range(4)
            ]
            longest = max(edges, key=lambda edge: LineString(edge).length)
            dx, dy = longest[1][0] - longest[0][0], longest[1][1] - longest[0][1]
            angle_x = math.degrees(math.atan2(dy, dx))
            bearing_deg = (90.0 - angle_x) % 180.0
        else:
            angle_x = 90.0 - bearing_deg

        rotated = affinity.rotate(projected, -angle_x, origin=projected.centroid)
        min_x, min_y, max_x, max_y = rotated.bounds
        rows: list[LineString] = []
        y = min_y + row_spacing_m / 2.0
        while y <= max_y:
            intersection = rotated.intersection(LineString([(min_x - 1, y), (max_x + 1, y)]))
            segments = []
            if isinstance(intersection, LineString):
                segments = [intersection]
            elif isinstance(intersection, MultiLineString):
                segments = list(intersection.geoms)
            segments = sorted(segments, key=lambda segment: segment.centroid.x)
            rows.extend(segment for segment in segments if segment.length >= row_spacing_m / 2)
            y += row_spacing_m
        if not rows:
            raise ValueError("El AOI es demasiado pequeño para el espaciado entre pasadas.")

        targets: list[tuple[float, float]] = []
        forward = True
        for row in rows:
            coordinates = list(row.coords)
            endpoints = [coordinates[0], coordinates[-1]]
            if not forward:
                endpoints.reverse()
            targets.extend(
                (point.x, point.y)
                for point in (
                    affinity.rotate(
                        shape({"type": "Point", "coordinates": endpoint}),
                        angle_x,
                        origin=projected.centroid,
                    )
                    for endpoint in endpoints
                )
            )
            forward = not forward
        return self._plan(
            "coverage_route",
            "coverage",
            targets,
            waypoint_spacing_m,
            direct_when_clear=True,
            coverage_rows=len(rows),
            row_spacing_m=float(row_spacing_m),
            bearing_deg=round(float(bearing_deg), 2),
            covered_area_m2=round(float(projected.area), 2),
        )

    def plan_layer_visit(
        self,
        layer_name: str = "scrub",
        start_lon_lat: tuple[float, float] | None = None,
        waypoint_spacing_m: float = 2.0,
        minimum_area_m2: float = 8.0,
        max_targets: int = 25,
    ) -> RoutePlan:
        layer_path = self.paths.get(layer_name)
        if layer_path is None:
            raise FileNotFoundError(f"El proyecto no contiene la capa {layer_name}.")
        with rasterio.open(layer_path) as source:
            mask = np.nan_to_num(source.read(1, masked=True).filled(0), nan=0) > 0.5
        labels, count = ndimage.label(mask)
        pixel_area = abs(self.transform.a * self.transform.e)
        targets: list[tuple[float, float]] = []
        for label_id in range(1, count + 1):
            cells = np.argwhere(labels == label_id)
            if len(cells) * pixel_area < minimum_area_m2:
                continue
            centroid = tuple(np.rint(cells.mean(axis=0)).astype(int))
            free = _nearest_free(self.blocked, centroid, max_radius=50)
            targets.append(self._cell_to_xy(free))
        if not targets:
            raise ValueError(f"No se encontraron zonas de {layer_name} con área suficiente.")
        if start_lon_lat:
            start_xy = self._wgs_to_xy(*start_lon_lat)
            row, col = self._xy_to_cell(start_xy)
            if not (0 <= row < self.blocked.shape[0] and 0 <= col < self.blocked.shape[1]):
                start_xy = self._cell_to_xy(
                    (self.blocked.shape[0] // 2, self.blocked.shape[1] // 2)
                )
        else:
            start_xy = self._cell_to_xy((self.blocked.shape[0] // 2, self.blocked.shape[1] // 2))
        ordered: list[tuple[float, float]] = [start_xy]
        remaining = targets[:]
        while remaining and len(ordered) <= max_targets:
            current = ordered[-1]
            nearest = min(
                remaining, key=lambda item: math.hypot(item[0] - current[0], item[1] - current[1])
            )
            ordered.append(nearest)
            remaining.remove(nearest)
        return self._plan(
            f"visit_{layer_name}",
            "semantic_targets",
            ordered,
            waypoint_spacing_m,
            semantic_layer=layer_name,
            semantic_targets=len(ordered) - 1,
        )


def save_route_bundle(plan: RoutePlan, output_dir: str | Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = Path(output_dir) / "routes" / f"{plan.name}_{timestamp}"
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "route.csv").write_text(plan.csv_text(), encoding="utf-8")
    (destination / "route.yaml").write_text(plan.yaml_text(), encoding="utf-8")
    (destination / "route.geojson").write_text(
        json.dumps(plan.geojson(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (destination / "metrics.json").write_text(
        json.dumps(plan.metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return destination
