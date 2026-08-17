"""Terrain-aware orientation search for agricultural coverage routes."""

from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

from .coverage import generate_zigzag_polygon
from .elevation import ElevationModel
from .export import waypoint_record
from .geometry import PointLL, ll_to_xy, normalize_angle


@dataclass(frozen=True)
class TerrainRouteMetrics:
    angle_deg: float
    feasible: bool
    score: float
    horizontal_distance_m: float
    distance_3d_m: float
    ascent_m: float
    descent_m: float
    maximum_absolute_grade_pct: float
    mean_absolute_grade_pct: float
    rms_grade_pct: float
    turn_count: int
    row_count: int
    waypoint_count: int


@dataclass
class TerrainRouteCandidate:
    metrics: TerrainRouteMetrics
    coverage_info: dict[str, float | int | None]
    waypoints: list[dict[str, float]]


@dataclass
class TerrainPlanResult:
    selected: TerrainRouteCandidate | None
    candidates: list[TerrainRouteCandidate]
    dem_provenance: dict[str, object]
    constraints: dict[str, float]


def _normalise_angles(angles_deg: Iterable[float]) -> list[float]:
    values: list[float] = []
    for angle in angles_deg:
        normalised = float(angle) % 180.0
        if not any(abs(normalised - existing) < 1e-9 for existing in values):
            values.append(normalised)
    if not values:
        raise ValueError("At least one candidate row angle is required.")
    return values


def annotate_elevation(
    waypoints: Sequence[dict[str, float]], elevation_model: ElevationModel
) -> list[dict[str, float]]:
    """Copy route waypoints and add absolute/local height and outgoing grade."""
    if not waypoints:
        return []
    elevations = [
        float(elevation_model.elevation_m(point["latitude"], point["longitude"]))
        for point in waypoints
    ]
    if not all(math.isfinite(value) for value in elevations):
        raise ValueError("The elevation source returned a non-finite value.")
    reference = elevations[0]
    enriched = [
        {
            **point,
            "elevation_m": elevation,
            "z_local_m": elevation - reference,
            "segment_grade_pct": 0.0,
        }
        for point, elevation in zip(waypoints, elevations)
    ]
    for index, (first, second) in enumerate(zip(enriched, enriched[1:])):
        east_m, north_m = ll_to_xy(
            second["latitude"],
            second["longitude"],
            first["latitude"],
            first["longitude"],
        )
        horizontal = math.hypot(east_m, north_m)
        if horizontal > 1e-9:
            first["segment_grade_pct"] = (
                100.0 * (second["elevation_m"] - first["elevation_m"]) / horizontal
            )
    return enriched


def _route_metrics(
    waypoints: Sequence[dict[str, float]],
    *,
    angle_deg: float,
    row_count: int,
    max_grade_pct: float,
    slope_weight: float,
    turn_penalty: float,
) -> TerrainRouteMetrics:
    horizontal_total = 0.0
    distance_3d = 0.0
    ascent = 0.0
    descent = 0.0
    weighted_absolute_grade = 0.0
    weighted_squared_grade = 0.0
    max_absolute_grade = 0.0
    for first, second in zip(waypoints, waypoints[1:]):
        east_m, north_m = ll_to_xy(
            second["latitude"], second["longitude"], first["latitude"], first["longitude"]
        )
        horizontal = math.hypot(east_m, north_m)
        delta_z = second["elevation_m"] - first["elevation_m"]
        grade = delta_z / horizontal if horizontal > 1e-9 else 0.0
        horizontal_total += horizontal
        distance_3d += math.hypot(horizontal, delta_z)
        ascent += max(0.0, delta_z)
        descent += max(0.0, -delta_z)
        weighted_absolute_grade += horizontal * abs(grade)
        weighted_squared_grade += horizontal * grade * grade
        max_absolute_grade = max(max_absolute_grade, abs(grade))

    turn_count = 0
    for first, second in zip(waypoints, waypoints[1:]):
        if abs(normalize_angle(second["yaw"] - first["yaw"])) > math.radians(20.0):
            turn_count += 1
    mean_grade = weighted_absolute_grade / horizontal_total if horizontal_total else 0.0
    rms_grade = math.sqrt(weighted_squared_grade / horizontal_total) if horizontal_total else 0.0
    score = distance_3d + slope_weight * weighted_squared_grade + turn_penalty * turn_count
    return TerrainRouteMetrics(
        angle_deg=angle_deg,
        feasible=max_absolute_grade * 100.0 <= max_grade_pct + 1e-9,
        score=score,
        horizontal_distance_m=horizontal_total,
        distance_3d_m=distance_3d,
        ascent_m=ascent,
        descent_m=descent,
        maximum_absolute_grade_pct=max_absolute_grade * 100.0,
        mean_absolute_grade_pct=mean_grade * 100.0,
        rms_grade_pct=rms_grade * 100.0,
        turn_count=turn_count,
        row_count=row_count,
        waypoint_count=len(waypoints),
    )


def plan_elevation_aware_coverage(
    vertices: list[PointLL],
    elevation_model: ElevationModel,
    *,
    angles_deg: Iterable[float] = range(0, 180, 15),
    row_spacing_m: float = 4.0,
    point_spacing_m: float = 2.0,
    max_grade_pct: float = 18.0,
    slope_weight: float = 1000.0,
    turn_penalty: float = 0.5,
) -> TerrainPlanResult:
    """Evaluate parallel-row orientations and select the cheapest feasible one."""
    if max_grade_pct <= 0:
        raise ValueError("Maximum grade must be positive.")
    if slope_weight < 0 or turn_penalty < 0:
        raise ValueError("Cost weights cannot be negative.")
    candidates: list[TerrainRouteCandidate] = []
    for angle in _normalise_angles(angles_deg):
        route, coverage_info = generate_zigzag_polygon(
            vertices,
            row_spacing_m=row_spacing_m,
            point_spacing_m=point_spacing_m,
            row_direction_deg=angle,
        )
        enriched = annotate_elevation(route, elevation_model)
        metrics = _route_metrics(
            enriched,
            angle_deg=angle,
            row_count=int(coverage_info["coverage_rows"]),
            max_grade_pct=max_grade_pct,
            slope_weight=slope_weight,
            turn_penalty=turn_penalty,
        )
        candidates.append(TerrainRouteCandidate(metrics, coverage_info, enriched))
    feasible = [candidate for candidate in candidates if candidate.metrics.feasible]
    selected = min(feasible, key=lambda candidate: candidate.metrics.score) if feasible else None
    return TerrainPlanResult(
        selected=selected,
        candidates=candidates,
        dem_provenance=elevation_model.provenance(),
        constraints={
            "max_grade_pct": max_grade_pct,
            "slope_weight": slope_weight,
            "turn_penalty": turn_penalty,
            "row_spacing_m": row_spacing_m,
            "point_spacing_m": point_spacing_m,
        },
    )


def export_terrain_plan(result: TerrainPlanResult, directory: str | Path) -> list[Path]:
    """Export candidate metrics and, when feasible, the selected 3-D route."""
    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    summary_path = output / "summary.json"
    candidate_path = output / "candidates.csv"
    summary = {
        "status": "selected" if result.selected else "no_feasible_candidate",
        "selected": asdict(result.selected.metrics) if result.selected else None,
        "constraints": result.constraints,
        "dem": result.dem_provenance,
        "candidates": [asdict(candidate.metrics) for candidate in result.candidates],
    }
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    metric_fields = list(asdict(result.candidates[0].metrics))
    with candidate_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=metric_fields)
        writer.writeheader()
        writer.writerows(asdict(candidate.metrics) for candidate in result.candidates)
    paths = [summary_path, candidate_path]
    if result.selected is None:
        return paths

    waypoint_path = output / "waypoints_3d.csv"
    geojson_path = output / "route_3d.geojson"
    records = [waypoint_record(waypoint) for waypoint in result.selected.waypoints]
    fields = [
        "latitude",
        "longitude",
        "elevation_m",
        "z_local_m",
        "segment_grade_pct",
        "yaw",
        "qx",
        "qy",
        "qz",
        "qw",
    ]
    with waypoint_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "angle_deg": result.selected.metrics.angle_deg,
                    "score": result.selected.metrics.score,
                    "maximum_absolute_grade_pct": result.selected.metrics.maximum_absolute_grade_pct,
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [point["longitude"], point["latitude"], point["elevation_m"]]
                        for point in result.selected.waypoints
                    ],
                },
            }
        ],
    }
    geojson_path.write_text(json.dumps(geojson, indent=2) + "\n", encoding="utf-8")
    return [*paths, waypoint_path, geojson_path]


def render_terrain_plan_preview(
    result: TerrainPlanResult, vertices: Sequence[PointLL], path: str | Path
) -> Path:
    """Render a compact route/elevation and candidate-cost sanity check."""
    try:
        import matplotlib.pyplot as plt
    except ImportError as error:
        raise RuntimeError("Matplotlib is required to render the DEM preview.") from error

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    figure, (route_axis, cost_axis) = plt.subplots(1, 2, figsize=(11, 4.5))
    origin = vertices[0]
    polygon_xy = [ll_to_xy(*point, *origin) for point in [*vertices, vertices[0]]]
    route_axis.plot(
        [point[0] for point in polygon_xy],
        [point[1] for point in polygon_xy],
        color="black",
        linewidth=1.2,
        label="field",
    )
    if result.selected:
        route_xy = [
            ll_to_xy(point["latitude"], point["longitude"], *origin)
            for point in result.selected.waypoints
        ]
        elevations = [point["elevation_m"] for point in result.selected.waypoints]
        route_axis.plot(
            [point[0] for point in route_xy],
            [point[1] for point in route_xy],
            color="#555555",
            linewidth=0.8,
        )
        samples = route_axis.scatter(
            [point[0] for point in route_xy],
            [point[1] for point in route_xy],
            c=elevations,
            cmap="terrain",
            s=20,
            zorder=3,
        )
        figure.colorbar(samples, ax=route_axis, label="Elevation (m)")
        route_axis.set_title(f"Selected rows: {result.selected.metrics.angle_deg:.0f}°")
    else:
        route_axis.text(0.5, 0.5, "No feasible route", ha="center", va="center", transform=route_axis.transAxes)
        route_axis.set_title("Rejected terrain")
    route_axis.set_xlabel("East (m)")
    route_axis.set_ylabel("North (m)")
    route_axis.set_aspect("equal", adjustable="box")
    route_axis.grid(alpha=0.25)

    angles = [candidate.metrics.angle_deg for candidate in result.candidates]
    scores = [candidate.metrics.score for candidate in result.candidates]
    colours = ["#2a9d8f" if candidate.metrics.feasible else "#d1495b" for candidate in result.candidates]
    cost_axis.bar([str(int(angle) if angle.is_integer() else angle) for angle in angles], scores, color=colours)
    cost_axis.set_title("Orientation objective")
    cost_axis.set_xlabel("Row azimuth (° from north)")
    cost_axis.set_ylabel("Cost (configured units)")
    cost_axis.tick_params(axis="x", rotation=45)
    cost_axis.grid(axis="y", alpha=0.25)
    figure.suptitle("Elevation-aware coverage sanity check")
    figure.tight_layout()
    figure.savefig(output, dpi=160, bbox_inches="tight")
    plt.close(figure)
    return output
