import math
import unittest

from geozigzag.coverage import generate_zigzag_polygon, generate_zigzag_rect
from geozigzag.geometry import point_in_polygon_xy, ll_to_xy, xy_to_ll, yaw_to_quaternion


class CoverageTests(unittest.TestCase):
    def test_polygon_coverage_points_remain_in_convex_field(self) -> None:
        polygon = [(42.0, -5.0), (42.0, -4.9997), (41.9998, -4.9997), (41.9998, -5.0)]
        route, info = generate_zigzag_polygon(
            polygon, row_spacing_m=4.0, point_spacing_m=3.0, row_direction_deg=90.0
        )
        origin = polygon[0]
        polygon_xy = [ll_to_xy(*point, *origin) for point in polygon]
        self.assertGreater(info["coverage_rows"], 2)
        self.assertTrue(
            all(
                point_in_polygon_xy(
                    ll_to_xy(point["latitude"], point["longitude"], *origin), polygon_xy
                )
                for point in route
            )
        )

    def test_invalid_spacings_are_rejected(self) -> None:
        for row_spacing, waypoint_spacing in ((0.0, 1.0), (1.0, 0.0), (-1.0, 1.0)):
            with self.subTest(row=row_spacing, waypoint=waypoint_spacing):
                with self.assertRaises(ValueError):
                    generate_zigzag_rect(
                        row_spacing_m=row_spacing, point_spacing_m=waypoint_spacing
                    )

    def test_polygon_vertex_touch_does_not_create_zero_length_row(self) -> None:
        origin = (42.0, -5.0)
        polygon = [
            xy_to_ll(0.0, 0.0, *origin),
            xy_to_ll(12.0, -1.0, *origin),
            xy_to_ll(11.0, -13.0, *origin),
            xy_to_ll(-2.0, -12.0, *origin),
        ]
        route, info = generate_zigzag_polygon(
            polygon, row_spacing_m=4.0, point_spacing_m=2.0, row_direction_deg=90.0
        )
        self.assertEqual(info["coverage_rows"], 3)
        first_x, first_y = ll_to_xy(route[0]["latitude"], route[0]["longitude"], *origin)
        second_x, second_y = ll_to_xy(route[1]["latitude"], route[1]["longitude"], *origin)
        self.assertLess(abs(second_y - first_y), 0.1)

    def test_yaw_quaternion_is_unit_length(self) -> None:
        for yaw in (-math.pi, -0.2, 0.0, 1.4, math.pi):
            quaternion = yaw_to_quaternion(yaw)
            self.assertAlmostEqual(sum(value * value for value in quaternion), 1.0, places=12)


if __name__ == "__main__":
    unittest.main()
