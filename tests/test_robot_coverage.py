import csv
import json
import math
import random
import tempfile
import unittest
from pathlib import Path

import yaml
from shapely.geometry import LineString, Polygon

from geozigzag.export import export_route_bundle
from geozigzag.geometry import ll_to_xy, normalize_angle, xy_to_ll
from geozigzag.headland import dubins_words, sample_dubins
from geozigzag.robot import RobotProfile
from geozigzag.robot_coverage import coverage_request, generate_robot_coverage


def field_xy(points):
    return [xy_to_ll(x, y, 42, -5) for x, y in points]


class DubinsTests(unittest.TestCase):
    def test_all_six_words_reach_pose_with_bounded_curvature(self):
        rng = random.Random(42)
        words = set()
        for _ in range(100):
            start = (
                rng.uniform(-10, 10),
                rng.uniform(-10, 10),
                rng.uniform(-math.pi, math.pi),
            )
            goal = (
                rng.uniform(-10, 10),
                rng.uniform(-10, 10),
                rng.uniform(-math.pi, math.pi),
            )
            radius = rng.uniform(0.5, 4)
            for word, lengths in dubins_words(start, goal, radius):
                words.add(word)
                poses = sample_dubins(start, word, lengths, radius, 0.1)
                self.assertLess(math.dist(poses[-1][:2], goal[:2]), 1e-8)
                self.assertAlmostEqual(
                    normalize_angle(poses[-1][2] - goal[2]), 0, places=9
                )
                for a, b in zip(poses, poses[1:]):
                    delta = abs(normalize_angle(b[2] - a[2]))
                    chord = math.dist(a[:2], b[:2])
                    if delta > 1e-8:
                        measured_radius = chord / (2 * math.sin(delta / 2))
                        self.assertAlmostEqual(measured_radius, radius, places=7)
        self.assertEqual(words, {"LSL", "LSR", "RSL", "RSR", "RLR", "LRL"})


class RobotCoverageTests(unittest.TestCase):
    def setUp(self):
        self.field = field_xy([(0, 0), (30, 0), (30, 60), (0, 60)])

    def plan(self, kind="differential", **kwargs):
        return generate_robot_coverage(
            self.field,
            2,
            1,
            0,
            robot=RobotProfile(kind=kind, clearance_m=0.8, headland_m=8),
            **kwargs,
        )

    def test_different_kinematics_and_continuous_containment(self):
        for kind in ["differential", "skid_steer", "ackermann"]:
            result = self.plan(kind)
            origin = result["origin_wgs84"]
            field = Polygon([ll_to_xy(*v, *origin) for v in self.field])
            path = LineString(
                [
                    ll_to_xy(w["latitude"], w["longitude"], *origin)
                    for w in result["waypoints"]
                ]
            )
            self.assertTrue(field.buffer(-0.79999).covers(path))
            work = Polygon([ll_to_xy(*v, *origin) for v in result["working_polygon"]])
            for turn in result["headland_paths"]:
                line = LineString([ll_to_xy(*v, *origin) for v in turn])
                self.assertLess(line.intersection(work.buffer(-0.0011)).length, 1e-7)
            self.assertEqual(result["rows"], 7)
            rotates = [w for w in result["waypoints"] if w["action"] == "rotate"]
            self.assertEqual(bool(rotates), kind != "ackermann")
            length = sum(
                math.dist(a, b) for a, b in zip(path.coords, list(path.coords)[1:])
            )
            self.assertAlmostEqual(length, result["distance_m"], delta=0.02)

    def test_ackermann_has_continuous_heading_and_no_pivots(self):
        result = self.plan("ackermann")
        points = result["waypoints"]
        radius = result["robot"]["minimum_radius_m"]
        origin = result["origin_wgs84"]
        for a, b in zip(points, points[1:]):
            chord = math.dist(
                ll_to_xy(a["latitude"], a["longitude"], *origin),
                ll_to_xy(b["latitude"], b["longitude"], *origin),
            )
            delta = abs(normalize_angle(b["yaw"] - a["yaw"]))
            self.assertGreater(chord, 1e-8)
            self.assertLessEqual(delta, chord / radius + 0.001)

    def test_radius_depends_on_wheelbase_and_steering(self):
        self.assertAlmostEqual(
            RobotProfile(
                kind="ackermann", wheelbase_m=2, max_steering_deg=45
            ).minimum_radius_m,
            2,
        )

    def test_route_matrix_across_robot_types_fields_and_row_bearings(self):
        small_ackermann = RobotProfile(
            kind="ackermann",
            clearance_m=0.55,
            headland_m=4,
            wheelbase_m=0.8,
            max_steering_deg=50,
        )
        field_shapes = {
            "rectangle": field_xy([(0, 0), (24, 0), (24, 38), (0, 38)]),
            "trapezoid": field_xy([(0, 0), (42, 0), (38, 58), (4, 54)]),
        }
        cases = [
            ("rectangle", "differential", 0, 2.5, RobotProfile(headland_m=2)),
            ("rectangle", "differential", 35, 1.2, RobotProfile(headland_m=2)),
            (
                "rectangle",
                "skid_steer",
                90,
                1.2,
                RobotProfile(kind="skid_steer", headland_m=3),
            ),
            ("rectangle", "ackermann", 0, 1.2, small_ackermann),
            ("rectangle", "ackermann", 35, 1.2, small_ackermann),
            ("rectangle", "ackermann", 90, 1.2, small_ackermann),
            ("trapezoid", "differential", 35, 1.2, RobotProfile(headland_m=3)),
            (
                "trapezoid",
                "skid_steer",
                90,
                1.2,
                RobotProfile(kind="skid_steer", headland_m=2),
            ),
            ("trapezoid", "ackermann", 0, 2.5, small_ackermann),
            ("trapezoid", "ackermann", 35, 1.2, small_ackermann),
            ("trapezoid", "ackermann", 90, 1.2, small_ackermann),
        ]
        for shape, kind, bearing, row_spacing, profile in cases:
            with self.subTest(
                shape=shape, robot=kind, bearing=bearing, spacing=row_spacing
            ):
                result = generate_robot_coverage(
                    field_shapes[shape], row_spacing, 0.9, bearing, robot=profile
                )
                self.assertGreater(result["rows"], 1)
                self.assertGreater(result["metrics"]["total_route_length_m"], 0)
                self.assertAlmostEqual(
                    result["metrics"]["curve_sampling_difference_m"],
                    result["metrics"]["total_route_length_m"]
                    - result["metrics"]["waypoint_polyline_length_m"],
                    delta=1e-6,
                )
                origin = result["origin_wgs84"]
                field = Polygon(
                    [ll_to_xy(*point, *origin) for point in field_shapes[shape]]
                )
                route = LineString(
                    [
                        ll_to_xy(w["latitude"], w["longitude"], *origin)
                        for w in result["waypoints"]
                    ]
                )
                self.assertTrue(field.buffer(-profile.clearance_m + 1e-5).covers(route))

    def test_large_ackermann_radius_needs_a_wider_headland(self):
        field = field_xy([(0, 0), (82, 0), (82, 54), (0, 54)])
        vehicle = RobotProfile(
            kind="ackermann",
            clearance_m=0.75,
            headland_m=8,
            wheelbase_m=2.4,
            max_steering_deg=30,
        )
        with self.assertRaisesRegex(ValueError, "No checked ackermann turn"):
            generate_robot_coverage(field, 2.5, 1, 0, robot=vehicle)
        result = generate_robot_coverage(
            field,
            2.5,
            1,
            0,
            robot=RobotProfile(
                kind="ackermann",
                clearance_m=0.75,
                headland_m=12,
                wheelbase_m=2.4,
                max_steering_deg=30,
            ),
        )
        self.assertEqual(result["rows"], 23)
        self.assertGreater(result["robot"]["minimum_radius_m"], 4)

    def test_turns_that_cross_the_crop_area_are_rejected(self):
        trapezoid = field_xy([(0, 0), (42, 0), (38, 58), (4, 54)])
        with self.assertRaisesRegex(ValueError, "No checked differential turn"):
            generate_robot_coverage(
                trapezoid,
                1.2,
                0.9,
                35,
                robot=RobotProfile(kind="differential", headland_m=2),
            )

    def test_all_start_corners_and_arbitrary_bearing(self):
        for corner in ["nw", "ne", "sw", "se"]:
            result = self.plan(start_corner=corner)
            first = result["waypoints"][0]
            x, y = ll_to_xy(first["latitude"], first["longitude"], 42, -5)
            self.assertEqual(x < 15, corner.endswith("w"))
            self.assertEqual(y > 30, corner.startswith("n"))
        result = generate_robot_coverage(
            self.field, 2, 1, 27, robot=RobotProfile(headland_m=3)
        )
        self.assertGreater(result["rows"], 1)

    def test_infeasible_turns_fail_without_returning_partial_route(self):
        with self.assertRaisesRegex(ValueError, "No checked ackermann turn"):
            generate_robot_coverage(
                self.field,
                2,
                1,
                0,
                robot=RobotProfile(kind="ackermann", headland_m=0.1, wheelbase_m=3),
            )
        with self.assertRaisesRegex(ValueError, "working area"):
            generate_robot_coverage(self.field, robot=RobotProfile(headland_m=100))

    def test_concave_field_is_checked_including_connectors(self):
        concave = field_xy(
            [(0, 0), (40, 0), (40, 60), (30, 60), (30, 20), (10, 20), (10, 60), (0, 60)]
        )
        with self.assertRaisesRegex(ValueError, "No checked|connected working area"):
            generate_robot_coverage(concave, 2, 1, 90, robot=RobotProfile(headland_m=2))

    def test_invalid_profile_and_request(self):
        for kwargs in [
            {"kind": "unknown"},
            {"wheelbase_m": 0},
            {"max_steering_deg": 90},
            {"headland_m": float("nan")},
            {"clearance_m": -1},
        ]:
            with self.assertRaises(ValueError):
                RobotProfile(**kwargs)
        for spacing in [0, float("nan"), float("inf"), -0.1]:
            with self.assertRaises(ValueError):
                generate_robot_coverage(self.field, spacing)
        with self.assertRaises(ValueError):
            coverage_request({"vertices": self.field, "unexpected": True})
        bowtie = field_xy([(0, 0), (20, 20), (0, 20), (20, 0)])
        with self.assertRaises(ValueError):
            generate_robot_coverage(bowtie)

    def test_export_preserves_stationary_rotations_and_orientations(self):
        result = self.plan()
        with tempfile.TemporaryDirectory() as folder:
            export_route_bundle(result["waypoints"], folder)
            with Path(folder, "waypoints.csv").open() as f:
                rows = list(csv.DictReader(f))
            document = yaml.safe_load(Path(folder, "waypoints.yaml").read_text())
            geojson = json.loads(Path(folder, "route.geojson").read_text())
        self.assertEqual(len(rows), len(result["waypoints"]))
        for i, w in enumerate(result["waypoints"]):
            self.assertEqual(rows[i]["action"], w["action"])
            self.assertEqual(document["waypoints"][i]["action"], w["action"])
            q = document["waypoints"][i]["orientation"]
            self.assertAlmostEqual(sum(v * v for v in q.values()), 1, places=10)
            if w["action"] == "rotate":
                self.assertEqual(rows[i]["latitude"], rows[i - 1]["latitude"])
                self.assertEqual(rows[i]["longitude"], rows[i - 1]["longitude"])
        self.assertEqual(len(geojson["features"]), len(rows) + 1)
