import importlib.util
import tempfile
import unittest
from pathlib import Path

from geozigzag.elevation import MapboxTerrainRgbDirectory
from geozigzag.terrain_costmap import ElevationCostConfig, build_terrain_cost_layer


class _ConstantElevation:
    def __init__(self, elevation_m):
        self.value = elevation_m

    def elevation_m(self, latitude, longitude):
        return self.value

    def provenance(self):
        return {"type": "test_constant"}


class TerrainCostmapTests(unittest.TestCase):
    def test_absolute_altitude_does_not_penalise_a_flat_plateau(self) -> None:
        layer = build_terrain_cost_layer(
            _ConstantElevation(1500.0),
            width=3,
            height=3,
            resolution_m=2.0,
            cell_to_ll=lambda cell: (42.0 + cell[0] * 1e-6, -5.0 + cell[1] * 1e-6),
        )
        self.assertTrue(all(value == 0.0 for row in layer.slopes_pct for value in row))
        self.assertTrue(all(value == 0.0 for row in layer.penalties for value in row))
        self.assertFalse(any(value for row in layer.blocked for value in row))

    def test_invalid_robot_slope_limits_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ElevationCostConfig(preferred_slope_pct=10.0, max_slope_pct=5.0)

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
