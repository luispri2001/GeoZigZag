"""Stable waypoint export formats."""

from __future__ import annotations

import csv
import json
import math
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
        fields = EXPORT_FIELDS + tuple(key for key in ("action", "phase") if any(key in w for w in waypoints))
        writer = csv.DictWriter(handle, fieldnames=fields)
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
        for key in ("action", "phase"):
            if key in waypoint:
                lines.append(f"    {key}: {json.dumps(waypoint[key])}")
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


def export_ardupilot_wpl(
    waypoints: Sequence[dict[str, float]], path: str | Path
) -> Path:
    """Export position-only waypoints as a Mission Planner QGC WPL 110 file."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    positions = []
    for waypoint in waypoints:
        latitude = waypoint.get("latitude")
        longitude = waypoint.get("longitude")
        if (
            isinstance(latitude, bool)
            or isinstance(longitude, bool)
            or not isinstance(latitude, (int, float))
            or not isinstance(longitude, (int, float))
            or not math.isfinite(latitude)
            or not math.isfinite(longitude)
            or not -90 <= latitude <= 90
            or not -180 <= longitude <= 180
        ):
            raise ValueError("ArduPilot export requires valid WGS84 coordinates.")
        if positions and (
            abs(latitude - positions[-1][0]) <= 1e-12
            and abs(longitude - positions[-1][1]) <= 1e-12
        ):
            # WPL NAV_WAYPOINT has no way to express a stationary yaw change.
            continue
        positions.append((latitude, longitude))
    if len(positions) < 2:
        raise ValueError(
            "ArduPilot export requires at least two distinct positions."
        )

    home_latitude, home_longitude = positions[0]
    lines = [
        "QGC WPL 110",
        f"0\t1\t0\t16\t0\t0\t0\t0\t{home_latitude:.12f}\t{home_longitude:.12f}\t0\t1",
    ]
    lines.extend(
        f"{sequence}\t0\t3\t16\t0\t0\t0\t0\t{latitude:.12f}\t{longitude:.12f}\t0\t1"
        for sequence, (latitude, longitude) in enumerate(positions, start=1)
    )
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def export_route_bundle(waypoints: Sequence[dict[str, float]], directory: str | Path) -> list[Path]:
    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    return [
        export_csv(waypoints, output / "waypoints.csv"),
        export_ros_yaml(waypoints, output / "waypoints.yaml"),
        export_geojson(waypoints, output / "route.geojson"),
        export_ardupilot_wpl(waypoints, output / "mission.waypoints"),
    ]
