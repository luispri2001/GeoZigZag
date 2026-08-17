"""Demonstrate DEM slope as a layer of the semantic A* costmap."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from .elevation import MapboxTerrainRgbDirectory, SyntheticGaussianHillElevation
from .export import export_route_bundle
from .geometry import PointLL, ll_to_xy, local_origin, route_distance_m, xy_to_ll
from .routing import feature_by_id, generate_cost_route, load_geojson
from .terrain_costmap import ElevationCostConfig, TerrainCostLayer, build_terrain_cost_layer


def _point_feature(identifier: str, point: PointLL) -> dict[str, Any]:
    return {
        "id": identifier,
        "type": "Feature",
        "properties": {"landcover": "pastizal"},
        "geometry": {"type": "Point", "coordinates": [point[1], point[0]]},
    }


def _demo_problem() -> tuple[dict[str, Any], list[str], SyntheticGaussianHillElevation]:
    origin = (42.0, -5.0)
    start = xy_to_ll(-35.0, 0.0, *origin)
    goal = xy_to_ll(35.0, 0.0, *origin)
    geojson = {
        "type": "FeatureCollection",
        "features": [_point_feature("start", start), _point_feature("goal", goal)],
    }
    return geojson, ["start", "goal"], SyntheticGaussianHillElevation(*origin)


def _grid_geometry(
    geojson: dict[str, Any], targets: list[str], resolution_m: float, padding_m: float
) -> tuple[PointLL, float, float, int, int]:
    target_points = [feature_by_id(geojson, identifier) for identifier in targets]
    feature_points: list[PointLL] = []
    for feature in geojson.get("features", []):
        if feature.get("geometry", {}).get("type") == "Point":
            longitude, latitude = feature["geometry"]["coordinates"]
            feature_points.append((float(latitude), float(longitude)))
    all_points = target_points + feature_points
    origin = local_origin(all_points)
    xy = [ll_to_xy(*point, *origin) for point in all_points]
    min_x = min(point[0] for point in xy) - padding_m
    max_x = max(point[0] for point in xy) + padding_m
    min_y = min(point[1] for point in xy) - padding_m
    max_y = max(point[1] for point in xy) + padding_m
    width = max(3, int(math.ceil((max_x - min_x) / resolution_m)) + 1)
    height = max(3, int(math.ceil((max_y - min_y) / resolution_m)) + 1)
    return origin, min_x, min_y, width, height


def _render_preview(
    layer: TerrainCostLayer,
    baseline: list[dict[str, float]],
    terrain_route: list[dict[str, float]],
    origin: PointLL,
    min_x: float,
    min_y: float,
    output: Path,
) -> None:
    import matplotlib.pyplot as plt

    max_x = min_x + (len(layer.elevations_m[0]) - 1) * layer.resolution_m
    max_y = min_y + (len(layer.elevations_m) - 1) * layer.resolution_m
    extent = (min_x, max_x, min_y, max_y)
    figure, axes = plt.subplots(1, 2, figsize=(11, 4.6), constrained_layout=True)
    panels = (
        (layer.elevations_m, "terrain", "DEM elevation (m)"),
        (layer.penalties, "magma", "DEM slope penalty"),
    )
    for axis, (values, colourmap, title) in zip(axes, panels):
        image = axis.imshow(values, origin="lower", extent=extent, cmap=colourmap, aspect="equal")
        figure.colorbar(image, ax=axis)
        blocked_x: list[float] = []
        blocked_y: list[float] = []
        for row, blocked_row in enumerate(layer.blocked):
            for col, is_blocked in enumerate(blocked_row):
                if is_blocked:
                    blocked_x.append(min_x + col * layer.resolution_m)
                    blocked_y.append(min_y + row * layer.resolution_m)
        axis.scatter(blocked_x, blocked_y, marker="s", s=8, color="#d62828", alpha=0.55, label="blocked slope")
        for route, style, label in (
            (baseline, "--", "semantic cost only"),
            (terrain_route, "-", "semantic + DEM"),
        ):
            route_xy = [ll_to_xy(point["latitude"], point["longitude"], *origin) for point in route]
            axis.plot(
                [point[0] for point in route_xy],
                [point[1] for point in route_xy],
                style,
                linewidth=2.0,
                label=label,
            )
        axis.set(title=title, xlabel="East (m)", ylabel="North (m)")
        axis.legend(fontsize=8, loc="best")
        axis.grid(alpha=0.2)
    figure.suptitle("Semantic navigation with DEM traversability")
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=170)
    plt.close(figure)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fuse DEM slope with GeoZigZag's local semantic A* costmap."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--demo", action="store_true", help="Run the synthetic hill example.")
    source.add_argument(
        "--terrain-world",
        type=Path,
        help="gazebo_terrain_generator directory containing metadata.json and dem/.",
    )
    parser.add_argument("--geojson", type=Path, help="Semantic point GeoJSON for a real DEM.")
    parser.add_argument("--targets", nargs="+", help="Ordered semantic feature IDs.")
    parser.add_argument("--resolution", type=float, default=2.0)
    parser.add_argument("--padding", type=float, default=20.0)
    parser.add_argument("--preferred-slope-pct", type=float, default=5.0)
    parser.add_argument("--max-slope-pct", type=float, default=20.0)
    parser.add_argument("--slope-cost-multiplier", type=float, default=20.0)
    parser.add_argument("--out", type=Path, default=Path("outputs/semantic-elevation-poc"))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.demo:
        geojson, targets, elevation_model = _demo_problem()
    else:
        if args.geojson is None or not args.targets:
            raise SystemExit("--terrain-world requires --geojson and --targets.")
        geojson = load_geojson(args.geojson)
        targets = list(args.targets)
        elevation_model = MapboxTerrainRgbDirectory.from_terrain_world(args.terrain_world)
    config = ElevationCostConfig(
        preferred_slope_pct=args.preferred_slope_pct,
        max_slope_pct=args.max_slope_pct,
        slope_cost_multiplier=args.slope_cost_multiplier,
    )
    baseline = generate_cost_route(
        geojson, targets, resolution_m=args.resolution, padding_m=args.padding
    )
    terrain_route = generate_cost_route(
        geojson,
        targets,
        resolution_m=args.resolution,
        padding_m=args.padding,
        elevation_model=elevation_model,
        elevation_config=config,
    )
    origin, min_x, min_y, width, height = _grid_geometry(
        geojson, targets, args.resolution, args.padding
    )

    def to_ll(cell: tuple[int, int]) -> PointLL:
        row, col = cell
        return xy_to_ll(
            min_x + col * args.resolution,
            min_y + row * args.resolution,
            *origin,
        )

    layer = build_terrain_cost_layer(
        elevation_model, width, height, args.resolution, to_ll, config
    )
    export_route_bundle(baseline, args.out / "semantic_only")
    export_route_bundle(terrain_route, args.out / "semantic_plus_dem")
    preview = args.out / "costmap_preview.png"
    _render_preview(layer, baseline, terrain_route, origin, min_x, min_y, preview)
    summary = {
        "status": "route_found",
        "terrain_layer": layer.summary(),
        "semantic_only": {
            "waypoint_count": len(baseline),
            "distance_m": route_distance_m(baseline),
        },
        "semantic_plus_dem": {
            "waypoint_count": len(terrain_route),
            "distance_m": route_distance_m(terrain_route),
        },
        "outputs": {
            "preview": str(preview),
            "semantic_only": str(args.out / "semantic_only"),
            "semantic_plus_dem": str(args.out / "semantic_plus_dem"),
        },
    }
    args.out.mkdir(parents=True, exist_ok=True)
    summary_path = args.out / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
