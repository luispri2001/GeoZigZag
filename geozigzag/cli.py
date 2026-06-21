"""Command-line demo for GeoZigzag Studio."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from geozigzag.planning import (
    DEFAULT_FIELD_CORNERS,
    export_csv,
    export_ros_yaml,
    generate_cost_route,
    generate_direct_route_from_ids,
    generate_zigzag_rect,
    load_geojson,
    summarize_route,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate integrated GeoZigzag demo outputs.")
    parser.add_argument("--data", default="data/points.geojson", help="GeoJSON file with semantic mission points.")
    parser.add_argument("--out", default="outputs", help="Output directory.")
    parser.add_argument(
        "--mission",
        nargs="+",
        default=["water_1", "arbustivo_2", "water_2"],
        help="Ordered GeoJSON feature IDs for the point-to-point mission.",
    )
    parser.add_argument("--row-spacing", type=float, default=0.75, help="Coverage row spacing in metres.")
    parser.add_argument("--point-spacing", type=float, default=1.0, help="Coverage waypoint spacing in metres.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    coverage, coverage_info = generate_zigzag_rect(
        DEFAULT_FIELD_CORNERS,
        row_spacing_m=args.row_spacing,
        point_spacing_m=args.point_spacing,
        start_corner="nw",
    )
    export_csv(coverage, out_dir / "coverage_zigzag.csv")
    export_ros_yaml(coverage, out_dir / "coverage_zigzag.yaml")

    geojson = load_geojson(args.data)
    direct = generate_direct_route_from_ids(geojson, args.mission, interval_m=10.0)
    cost = generate_cost_route(geojson, args.mission, resolution_m=5.0)
    export_csv(direct, out_dir / "mission_direct.csv")
    export_ros_yaml(direct, out_dir / "mission_direct.yaml")
    export_csv(cost, out_dir / "mission_costmap.csv")
    export_ros_yaml(cost, out_dir / "mission_costmap.yaml")

    summary = {
        "coverage": {**coverage_info, **summarize_route(coverage)},
        "mission_direct": summarize_route(direct),
        "mission_costmap": summarize_route(cost),
        "mission_order": args.mission,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
