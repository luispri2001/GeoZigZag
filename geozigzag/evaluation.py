"""Deterministic multi-scenario evaluation for GeoZigzag Studio."""

from __future__ import annotations

import csv
import hashlib
import json
import random
import statistics
import time
from pathlib import Path
from typing import Any, Callable

import yaml

from .coverage import generate_zigzag_polygon, generate_zigzag_rect
from .export import export_route_bundle
from .metrics import summarize_route
from .routing import (
    CachedOSRMClient,
    feature_by_id,
    generate_cost_route,
    generate_direct_route,
    generate_osrm_route,
    load_geojson,
)
from .geometry import points_to_waypoints

RESULT_FIELDS = (
    "scenario",
    "task",
    "strategy",
    "success",
    "success_rate",
    "waypoints",
    "distance_m",
    "rows",
    "turns",
    "computation_time_ms",
    "forbidden_zone_intersections",
    "max_snap_distance_m",
    "area_m2",
    "average_row_length_m",
    "row_spacing_m",
    "waypoint_spacing_m",
    "error",
)


def load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("Evaluation config must use schema_version: 1.")
    return data


def _timed_repetitions(
    operation: Callable[[], tuple[list[dict[str, float]], dict[str, Any]]], repetitions: int
) -> tuple[list[dict[str, float]], dict[str, Any], float, float, str]:
    durations: list[float] = []
    successes = 0
    last_route: list[dict[str, float]] = []
    last_metadata: dict[str, Any] = {}
    last_error = ""
    for _ in range(repetitions):
        started = time.perf_counter_ns()
        try:
            last_route, last_metadata = operation()
            successes += 1
        except Exception as error:  # The error is part of the evaluated failure contract.
            last_error = f"{type(error).__name__}: {error}"
        durations.append((time.perf_counter_ns() - started) / 1_000_000.0)
    successful_time = statistics.median(durations) if durations else 0.0
    return last_route, last_metadata, successful_time, successes / repetitions, last_error


def _scenario_vertices(scenario: dict[str, Any]) -> list[tuple[float, float]]:
    if scenario["kind"] == "rectangle":
        corners = scenario["corners"]
        return [tuple(corners[key]) for key in ("nw", "ne", "se", "sw")]
    return [tuple(point) for point in scenario["polygon"]]


def _coverage_operation(scenario: dict[str, Any]) -> tuple[list[dict[str, float]], dict[str, Any]]:
    if scenario["kind"] == "rectangle":
        return generate_zigzag_rect(
            {key: tuple(value) for key, value in scenario["corners"].items()},
            row_spacing_m=float(scenario["row_spacing_m"]),
            point_spacing_m=float(scenario["waypoint_spacing_m"]),
            start_corner=scenario.get("start_corner", "nw"),
            row_direction_deg=scenario.get("row_direction_deg"),
        )
    return generate_zigzag_polygon(
        [tuple(point) for point in scenario["polygon"]],
        row_spacing_m=float(scenario["row_spacing_m"]),
        point_spacing_m=float(scenario["waypoint_spacing_m"]),
        row_direction_deg=scenario.get("row_direction_deg"),
    )


def _write_results(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in RESULT_FIELDS})


def _plot_route_comparison(
    scenario: dict[str, Any], routes: dict[str, list[dict[str, float]]], output: Path
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    styles = {
        "direct": ("#1764aa", "Direct"),
        "cost_aware": ("#b65d13", "Cost + forbidden zones"),
        "osrm_cached": ("#734a9d", "Cached OSRM"),
    }
    zones = scenario.get("forbidden_zones", [])
    figure, axes = plt.subplots(2, 2, figsize=(8.0, 6.2), constrained_layout=True)
    panels = ["direct", "cost_aware", "osrm_cached", "overlay"]
    for axis, panel in zip(axes.flat, panels):
        for zone in zones:
            longitude = [point[1] for point in zone] + [zone[0][1]]
            latitude = [point[0] for point in zone] + [zone[0][0]]
            axis.fill(longitude, latitude, color="#d62728", alpha=0.22, label="Forbidden zone")
            axis.plot(longitude, latitude, color="#d62728", linewidth=1.1)
        selected = styles if panel == "overlay" else {panel: styles[panel]}
        for key, (color, label) in selected.items():
            route = routes.get(key, [])
            if route:
                axis.plot(
                    [point["longitude"] for point in route],
                    [point["latitude"] for point in route],
                    color=color,
                    linewidth=1.6,
                    label=label,
                )
        axis.set_title("All strategies" if panel == "overlay" else styles[panel][1])
        axis.set_xlabel("Longitude [deg]")
        axis.set_ylabel("Latitude [deg]")
        axis.ticklabel_format(useOffset=False)
        axis.grid(alpha=0.25)
        axis.legend(fontsize=7, loc="best")
    figure.suptitle(f"Route comparison: {scenario['name'].replace('_', ' ')}")
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=220)
    plt.close(figure)


def _plot_sensitivity(rows: list[dict[str, Any]], output: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(1, 2, figsize=(8.0, 3.2), constrained_layout=True)
    waypoint_spacings = sorted({float(row["waypoint_spacing_m"]) for row in rows})
    for spacing in waypoint_spacings:
        selected = sorted(
            (row for row in rows if float(row["waypoint_spacing_m"]) == spacing),
            key=lambda row: float(row["row_spacing_m"]),
        )
        axes[0].plot(
            [row["row_spacing_m"] for row in selected],
            [row["rows"] for row in selected],
            marker="o",
            label=f"waypoints {spacing:g} m",
        )
        axes[1].plot(
            [row["row_spacing_m"] for row in selected],
            [row["waypoints"] for row in selected],
            marker="o",
            label=f"waypoints {spacing:g} m",
        )
    axes[0].set(xlabel="Row spacing [m]", ylabel="Coverage rows")
    axes[1].set(xlabel="Row spacing [m]", ylabel="Exported waypoints")
    for axis in axes:
        axis.grid(alpha=0.25)
        axis.legend(fontsize=7)
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=220)
    plt.close(figure)


def _write_latex_table(rows: list[dict[str, Any]], output: Path) -> None:
    routing_rows = [row for row in rows if row["task"] == "routing"]
    lines = [
        "\\begin{tabular}{llrrrrr}",
        "\\hline",
        "Scenario & Strategy & Points & Dist. (m) & Turns & Hits & Time (ms) \\\\",
        "\\hline",
    ]
    for row in routing_rows:
        scenario = str(row["scenario"]).replace("_", "\\_")
        strategy = str(row["strategy"]).replace("_", "\\_")
        lines.append(
            f"{scenario} & {strategy} & {row['waypoints']} & {float(row['distance_m']):.1f} & "
            f"{row['turns']} & {row['forbidden_zone_intersections']} & "
            f"{float(row['computation_time_ms']):.2f} \\\\"
        )
    lines.extend(["\\hline", "\\end{tabular}"])
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_evaluation(config_path: str | Path, output_directory: str | Path) -> dict[str, Any]:
    config_path = Path(config_path).resolve()
    project_root = config_path.parent.parent
    output = Path(output_directory).resolve()
    output.mkdir(parents=True, exist_ok=True)
    config = load_config(config_path)
    seed = int(config.get("seed", 0))
    random.seed(seed)
    repetitions = max(1, int(config.get("repetitions", 1)))
    rows: list[dict[str, Any]] = []
    route_cache: dict[str, dict[str, list[dict[str, float]]]] = {}

    for scenario in config.get("coverage_scenarios", []):
        route, info, duration, success_rate, error = _timed_repetitions(
            lambda scenario=scenario: _coverage_operation(scenario), repetitions
        )
        metrics = summarize_route(
            route,
            computation_time_ms=duration,
            success=bool(route),
            rows=int(info.get("coverage_rows", 0)) if info else 0,
            area_m2=float(info.get("area_m2", 0.0)) if info else 0.0,
            average_row_length_m=float(info.get("average_row_length_m", 0.0)) if info else 0.0,
            row_spacing_m=float(scenario["row_spacing_m"]),
            waypoint_spacing_m=float(scenario["waypoint_spacing_m"]),
        )
        row = {
            "scenario": scenario["name"],
            "task": "coverage",
            "strategy": "boustrophedon",
            "success_rate": success_rate,
            "error": error,
            **metrics,
        }
        rows.append(row)
        if route:
            export_route_bundle(route, output / "routes" / scenario["name"] / "boustrophedon")

    fixture = project_root / config["osrm"]["fixture"]
    osrm = CachedOSRMClient(fixture)
    for scenario in config.get("routing_scenarios", []):
        geojson = load_geojson(project_root / scenario["geojson"])
        targets = [feature_by_id(geojson, feature_id) for feature_id in scenario["targets"]]
        zones = [[tuple(point) for point in zone] for zone in scenario.get("forbidden_zones", [])]

        def direct() -> tuple[list[dict[str, float]], dict[str, Any]]:
            return points_to_waypoints(
                generate_direct_route(targets, float(scenario["waypoint_spacing_m"]))
            ), {}

        def cost() -> tuple[list[dict[str, float]], dict[str, Any]]:
            return generate_cost_route(
                geojson,
                list(scenario["targets"]),
                resolution_m=float(scenario["grid_resolution_m"]),
                forbidden_zones=zones,
            ), {}

        def cached_osrm() -> tuple[list[dict[str, float]], dict[str, Any]]:
            return generate_osrm_route(
                targets,
                osrm,
                profile=config["osrm"].get("profile", "driving"),
                route_key=scenario["osrm_fixture_key"],
                max_snap_m=float(config["osrm"]["max_snap_m"]),
            )

        operations = {"direct": direct, "cost_aware": cost, "osrm_cached": cached_osrm}
        route_cache[scenario["name"]] = {}
        for strategy, operation in operations.items():
            route, metadata, duration, success_rate, error = _timed_repetitions(operation, repetitions)
            metrics = summarize_route(
                route,
                forbidden_zones=zones,
                computation_time_ms=duration,
                success=bool(route),
                waypoint_spacing_m=float(scenario["waypoint_spacing_m"]),
                max_snap_distance_m=metadata.get("max_snap_distance_m", 0.0),
            )
            row = {
                "scenario": scenario["name"],
                "task": "routing",
                "strategy": strategy,
                "success_rate": success_rate,
                "error": error,
                **metrics,
            }
            rows.append(row)
            route_cache[scenario["name"]][strategy] = route
            if route:
                route_dir = output / "routes" / scenario["name"] / strategy
                export_route_bundle(route, route_dir)
                (route_dir / "metadata.json").write_text(
                    json.dumps({"scenario": scenario, "strategy": strategy, **metadata}, indent=2) + "\n",
                    encoding="utf-8",
                )

    sensitivity_config = config.get("sensitivity", {})
    coverage_by_name = {scenario["name"]: scenario for scenario in config.get("coverage_scenarios", [])}
    sensitivity_base = coverage_by_name[sensitivity_config["scenario"]]
    sensitivity_rows: list[dict[str, Any]] = []
    for row_spacing in sensitivity_config.get("row_spacing_m", []):
        for waypoint_spacing in sensitivity_config.get("waypoint_spacing_m", []):
            scenario = {**sensitivity_base, "row_spacing_m": row_spacing, "waypoint_spacing_m": waypoint_spacing}
            route, info = _coverage_operation(scenario)
            sensitivity_rows.append(
                {
                    "row_spacing_m": float(row_spacing),
                    "waypoint_spacing_m": float(waypoint_spacing),
                    "rows": int(info["coverage_rows"]),
                    "waypoints": len(route),
                    "distance_m": summarize_route(route)["distance_m"],
                }
            )

    _write_results(rows, output / "results.csv")
    _write_results(
        [
            {"scenario": sensitivity_config["scenario"], "task": "sensitivity", "strategy": "boustrophedon", **row}
            for row in sensitivity_rows
        ],
        output / "sensitivity.csv",
    )
    figure_dir = output / "figures"
    for scenario in config.get("routing_scenarios", []):
        _plot_route_comparison(
            scenario, route_cache[scenario["name"]], figure_dir / f"comparison_{scenario['name']}.png"
        )
    _plot_sensitivity(sensitivity_rows, figure_dir / "sensitivity.png")
    _write_latex_table(rows, output / "paper_results.tex")

    config_digest = hashlib.sha256(config_path.read_bytes()).hexdigest()
    summary = {
        "schema_version": 1,
        "seed": seed,
        "repetitions": repetitions,
        "config_sha256": config_digest,
        "osrm_fixture": json.loads(fixture.read_text(encoding="utf-8"))["metadata"],
        "results": rows,
        "sensitivity": sensitivity_rows,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary
