"""CLI for the terrain-aware coverage proof of concept."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .coverage import DEFAULT_FIELD_CORNERS
from .elevation import MapboxTerrainRgbDirectory, SyntheticPlaneElevation
from .elevation_planning import (
    export_terrain_plan,
    plan_elevation_aware_coverage,
    render_terrain_plan_preview,
)
from .geometry import PointLL, local_origin


def _read_polygon(path: Path) -> list[PointLL]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("type") == "FeatureCollection":
        features = payload.get("features", [])
        polygon_feature = next(
            (item for item in features if item.get("geometry", {}).get("type") == "Polygon"),
            None,
        )
        if polygon_feature is None:
            raise ValueError("GeoJSON FeatureCollection contains no Polygon feature.")
        coordinates = polygon_feature["geometry"]["coordinates"][0]
    elif payload.get("type") == "Feature" and payload.get("geometry", {}).get("type") == "Polygon":
        coordinates = payload["geometry"]["coordinates"][0]
    elif payload.get("type") == "Polygon":
        coordinates = payload["coordinates"][0]
    else:
        raise ValueError("Expected a GeoJSON Polygon, Feature, or FeatureCollection.")
    vertices = [(float(latitude), float(longitude)) for longitude, latitude, *_ in coordinates]
    if len(vertices) > 1 and vertices[0] == vertices[-1]:
        vertices.pop()
    return vertices


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare crop-row orientations using DEM-derived route grades."
    )
    parser.add_argument("--polygon", type=Path, help="Field polygon as GeoJSON (defaults to demo field).")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--terrain-world",
        type=Path,
        help="gazebo_terrain_generator working directory containing metadata.json and dem/.",
    )
    source.add_argument(
        "--demo",
        action="store_true",
        help="Use a deterministic synthetic plane; this is not a real DEM.",
    )
    parser.add_argument("--out", type=Path, default=Path("outputs/elevation-poc"))
    parser.add_argument("--row-spacing", type=float, default=4.0)
    parser.add_argument("--point-spacing", type=float, default=2.0)
    parser.add_argument("--angle-step", type=int, default=15)
    parser.add_argument("--max-grade-pct", type=float, default=18.0)
    parser.add_argument("--slope-weight", type=float, default=1000.0)
    parser.add_argument("--turn-penalty", type=float, default=0.5)
    parser.add_argument("--no-preview", action="store_true", help="Do not generate preview.png.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.angle_step <= 0 or args.angle_step >= 180:
        raise SystemExit("--angle-step must be between 1 and 179 degrees.")
    vertices = _read_polygon(args.polygon) if args.polygon else [
        DEFAULT_FIELD_CORNERS[key] for key in ("nw", "ne", "se", "sw")
    ]
    if args.terrain_world:
        elevation_model = MapboxTerrainRgbDirectory.from_terrain_world(args.terrain_world)
    else:
        origin_lat, origin_lon = local_origin(vertices)
        elevation_model = SyntheticPlaneElevation(origin_lat, origin_lon)
    result = plan_elevation_aware_coverage(
        vertices,
        elevation_model,
        angles_deg=range(0, 180, args.angle_step),
        row_spacing_m=args.row_spacing,
        point_spacing_m=args.point_spacing,
        max_grade_pct=args.max_grade_pct,
        slope_weight=args.slope_weight,
        turn_penalty=args.turn_penalty,
    )
    paths = export_terrain_plan(result, args.out)
    if not args.no_preview:
        paths.append(render_terrain_plan_preview(result, vertices, args.out / "preview.png"))
    print(
        json.dumps(
            {
                "status": "selected" if result.selected else "no_feasible_candidate",
                "selected_angle_deg": result.selected.metrics.angle_deg if result.selected else None,
                "outputs": [str(path) for path in paths],
            },
            indent=2,
        )
    )
    return 0 if result.selected else 2


if __name__ == "__main__":
    raise SystemExit(main())
