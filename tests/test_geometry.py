import math
import unittest

from geozigzag.geometry import ll_to_xy, xy_to_ll, yaw_to_quaternion


class GeometryTests(unittest.TestCase):
    def test_local_enu_round_trip(self):
        for origin in [(42, -5), (0, 0), (-35, 149)]:
            for east, north in [(0, 0), (300, -200), (-50, 800)]:
                latitude, longitude = xy_to_ll(east, north, *origin)
                x, y = ll_to_xy(latitude, longitude, *origin)
                self.assertAlmostEqual(x, east, places=7)
                self.assertAlmostEqual(y, north, places=7)
                self.assertEqual(latitude > origin[0], north > 0)
                self.assertEqual(longitude > origin[1], east > 0)

    def test_poles_are_rejected(self):
        for latitude in [-90, 90]:
            with self.assertRaises(ValueError):
                ll_to_xy(latitude, 0, latitude, 0)
            with self.assertRaises(ValueError):
                xy_to_ll(0, 0, latitude, 0)

    def test_planar_quaternions_preserve_yaw_and_unit_norm(self):
        for yaw in [-math.pi, -0.2, 0, 1.4, math.pi]:
            x, y, z, w = yaw_to_quaternion(yaw)
            self.assertEqual((x, y), (0, 0))
            self.assertAlmostEqual(z * z + w * w, 1, places=12)
            self.assertAlmostEqual(2 * math.atan2(z, w), yaw, places=12)
