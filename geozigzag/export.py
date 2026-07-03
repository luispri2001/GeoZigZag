"""Stable waypoint export formats."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Sequence

from .geometry import yaw_to_quaternion

EXPORT_FIELDS = ("latitude", "longitude", "yaw", "qx", "qy", "qz", "qw")


def waypoint_record(waypoint: dict[str, float]) -> dict[str, float]:
    qx, qy, qz, qw = yaw_to_quaternion(waypoint["yaw"])
    return {**waypoint, "qx": qx, "qy": qy, "qz": qz, "qw": qw}


def export_csv(waypoints: Sequence[dict[str, float]], path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPORT_FIELDS)
        writer.writeheader()
        writer.writerows(waypoint_record(waypoint) for waypoint in waypoints)
    return output


def export_ros_yaml(waypoints: Sequence[dict[str, float]], path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = ["waypoints:"]
    for waypoint in waypoints:
        record = waypoint_record(waypoint)
        lines.extend(
            [
                f"  - latitude: {record['latitude']:.12f}",
                f"    longitude: {record['longitude']:.12f}",
                f"    yaw: {record['yaw']:.12f}",
                "    orientation:",
                f"      qx: {record['qx']:.12f}",
                f"      qy: {record['qy']:.12f}",
                f"      qz: {record['qz']:.12f}",
                f"      qw: {record['qw']:.12f}",
            ]
        )
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def export_geojson(waypoints: Sequence[dict[str, float]], path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": "route", "waypoint_count": len(waypoints)},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [waypoint["longitude"], waypoint["latitude"]] for waypoint in waypoints
                    ],
                },
            },
            *[
                {
                    "type": "Feature",
                    "properties": {"index": index, **waypoint_record(waypoint)},
                    "geometry": {
                        "type": "Point",
                        "coordinates": [waypoint["longitude"], waypoint["latitude"]],
                    },
                }
                for index, waypoint in enumerate(waypoints)
            ],
        ],
    }
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return output


def export_route_bundle(waypoints: Sequence[dict[str, float]], directory: str | Path) -> list[Path]:
    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    return [
        export_csv(waypoints, output / "waypoints.csv"),
        export_ros_yaml(waypoints, output / "waypoints.yaml"),
        export_geojson(waypoints, output / "route.geojson"),
    ]
