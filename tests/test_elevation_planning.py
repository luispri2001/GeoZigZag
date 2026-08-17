import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from geozigzag.elevation import MapboxTerrainRgbDirectory, SyntheticPlaneElevation
from geozigzag.elevation_planning import (
    annotate_elevation,
    export_terrain_plan,
    plan_elevation_aware_coverage,
)
from geozigzag.geometry import points_to_waypoints, xy_to_ll


class ElevationPlanningTests(unittest.TestCase):
    origin = (42.0, -5.0)

    def rectangle(self, east_m: float = 60.0, north_m: float = 30.0):
        return [
            xy_to_ll(0.0, 0.0, *self.origin),
            xy_to_ll(east_m, 0.0, *self.origin),
            xy_to_ll(east_m, north_m, *self.origin),
            xy_to_ll(0.0, north_m, *self.origin),
        ]

    def test_annotation_uses_metres_and_local_first_waypoint_origin(self) -> None:
        points = [
            xy_to_ll(0.0, 0.0, *self.origin),
            xy_to_ll(10.0, 0.0, *self.origin),
        ]
        model = SyntheticPlaneElevation(*self.origin, base_elevation_m=700.0, east_grade=0.1)
        route = annotate_elevation(points_to_waypoints(points), model)
        self.assertAlmostEqual(route[0]["elevation_m"], 700.0, places=6)
        self.assertAlmostEqual(route[0]["z_local_m"], 0.0, places=6)
        self.assertAlmostEqual(route[1]["z_local_m"], 1.0, places=5)
        self.assertAlmostEqual(route[0]["segment_grade_pct"], 10.0, places=5)

    def test_orientation_search_prefers_rows_close_to_contours(self) -> None:
        model = SyntheticPlaneElevation(
            *self.origin, base_elevation_m=700.0, east_grade=0.0, north_grade=0.08
        )
        result = plan_elevation_aware_coverage(
            self.rectangle(),
            model,
            angles_deg=[0.0, 90.0],
            row_spacing_m=5.0,
            point_spacing_m=3.0,
            max_grade_pct=10.0,
            slope_weight=1000.0,
        )
        self.assertIsNotNone(result.selected)
        self.assertEqual(result.selected.metrics.angle_deg, 90.0)
        by_angle = {item.metrics.angle_deg: item.metrics for item in result.candidates}
        self.assertLess(by_angle[90.0].rms_grade_pct, by_angle[0.0].rms_grade_pct)

    def test_no_route_is_selected_when_every_candidate_breaks_grade_limit(self) -> None:
        model = SyntheticPlaneElevation(
            *self.origin, base_elevation_m=700.0, east_grade=0.0, north_grade=0.08
        )
        result = plan_elevation_aware_coverage(
            self.rectangle(),
            model,
            angles_deg=[0.0, 90.0],
            row_spacing_m=5.0,
            point_spacing_m=3.0,
            max_grade_pct=5.0,
        )
        self.assertIsNone(result.selected)
        self.assertTrue(all(not item.metrics.feasible for item in result.candidates))

    def test_exports_candidate_audit_and_selected_3d_route(self) -> None:
        result = plan_elevation_aware_coverage(
            self.rectangle(20.0, 10.0),
            SyntheticPlaneElevation(*self.origin),
            angles_deg=[90.0],
            row_spacing_m=5.0,
            point_spacing_m=2.0,
        )
        with tempfile.TemporaryDirectory() as temporary:
            paths = export_terrain_plan(result, temporary)
            self.assertEqual({path.name for path in paths}, {
                "summary.json", "candidates.csv", "waypoints_3d.csv", "route_3d.geojson"
            })
            summary = json.loads((Path(temporary) / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "selected")
            geojson = json.loads((Path(temporary) / "route_3d.geojson").read_text(encoding="utf-8"))
            self.assertEqual(len(geojson["features"][0]["geometry"]["coordinates"][0]), 3)

    @unittest.skipUnless(importlib.util.find_spec("PIL"), "Pillow is an optional DEM dependency")
    def test_mapbox_terrain_rgb_decoder(self) -> None:
        from PIL import Image

        encoded = round((123.4 + 10000.0) / 0.1)
        colour = (encoded // 65536, encoded // 256 % 256, encoded % 256)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            Image.new("RGB", (2, 2), colour).save(directory / "[0,0,0].png")
            model = MapboxTerrainRgbDirectory(directory)
            self.assertAlmostEqual(model.elevation_m(0.0, 0.0), 123.4, places=6)


if __name__ == "__main__":
    unittest.main()
