import csv
import json
import tempfile
import unittest
from pathlib import Path

from geozigzag.planning import (
    DEFAULT_FIELD_CORNERS,
    export_csv,
    export_ros_yaml,
    generate_cost_route,
    generate_direct_route_from_ids,
    generate_zigzag_polygon,
    generate_zigzag_rect,
    load_geojson,
    points_to_waypoints,
    summarize_route,
    yaw_to_quaternion,
)


class PlanningTests(unittest.TestCase):
    def test_default_zigzag_summary_is_reproducible(self) -> None:
        waypoints, info = generate_zigzag_rect(
            DEFAULT_FIELD_CORNERS,
            row_spacing_m=0.75,
            point_spacing_m=1.0,
            start_corner="nw",
        )
        summary = summarize_route(waypoints)

        self.assertEqual(info["coverage_rows"], 16)
        self.assertEqual(summary["points"], 224)
        self.assertAlmostEqual(summary["distance_m"], 205.84696570982493, places=6)

    def test_polygon_zigzag_generates_rows_and_waypoints(self) -> None:
        polygon = [
            (42.0, -5.0),
            (42.0, -4.99985),
            (41.99990, -4.99985),
            (41.99990, -5.0),
        ]

        waypoints, info = generate_zigzag_polygon(
            polygon,
            row_spacing_m=4.0,
            point_spacing_m=4.0,
            row_direction_deg=90.0,
        )

        self.assertGreater(info["coverage_rows"], 1)
        self.assertGreater(len(waypoints), 3)
        self.assertTrue(all("yaw" in waypoint for waypoint in waypoints))

    def test_waypoint_yaw_and_quaternion_use_planar_ros_orientation(self) -> None:
        waypoints = points_to_waypoints([(42.0, -5.0), (42.0, -4.9999)])
        qx, qy, qz, qw = yaw_to_quaternion(waypoints[0]["yaw"])

        self.assertAlmostEqual(waypoints[0]["yaw"], 0.0, places=6)
        self.assertEqual(qx, 0.0)
        self.assertEqual(qy, 0.0)
        self.assertAlmostEqual(qz, 0.0, places=6)
        self.assertAlmostEqual(qw, 1.0, places=6)

    def test_mission_routes_from_demo_geojson(self) -> None:
        geojson = load_geojson("data/points.geojson")
        mission = ["water_1", "arbustivo_2", "water_2"]

        direct = generate_direct_route_from_ids(geojson, mission, interval_m=10.0)
        cost = generate_cost_route(geojson, mission, resolution_m=5.0)

        self.assertEqual(len(direct), 54)
        self.assertEqual(len(cost), 83)
        self.assertGreater(summarize_route(cost)["distance_m"], summarize_route(direct)["distance_m"])

    def test_export_files_have_expected_schema(self) -> None:
        waypoints, _ = generate_zigzag_rect(row_spacing_m=3.0, point_spacing_m=3.0)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            csv_path = export_csv(waypoints, tmp_path / "route.csv")
            yaml_path = export_ros_yaml(waypoints, tmp_path / "route.yaml")

            with csv_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))

            self.assertEqual(
                rows[0].keys(),
                {"latitude", "longitude", "yaw", "qx", "qy", "qz", "qw"},
            )
            self.assertIn("waypoints:", yaml_path.read_text(encoding="utf-8"))
            self.assertIn("orientation:", yaml_path.read_text(encoding="utf-8"))

    def test_summary_json_shape_matches_cli_output_contract(self) -> None:
        coverage, coverage_info = generate_zigzag_rect()
        summary = {"coverage": {**coverage_info, **summarize_route(coverage)}}
        encoded = json.loads(json.dumps(summary))

        self.assertEqual(
            set(encoded["coverage"].keys()),
            {"length_m", "width_m", "coverage_rows", "points", "distance_m"},
        )

    def test_invalid_spacing_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            generate_zigzag_rect(row_spacing_m=0.0)


if __name__ == "__main__":
    unittest.main()
