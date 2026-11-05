import importlib.util
import tempfile
import unittest
from pathlib import Path

from geozigzag.elevation import (
    GeoTiffElevation,
    MapboxTerrainRgbDirectory,
    TerrariumTileElevation,
)


class ElevationTests(unittest.TestCase):
    @unittest.skipUnless(
        importlib.util.find_spec("PIL"), "Pillow is an optional DEM dependency"
    )
    def test_mapbox_terrain_rgb_decoder(self) -> None:
        from PIL import Image

        encoded = round((123.4 + 10000.0) / 0.1)
        colour = (encoded // 65536, encoded // 256 % 256, encoded % 256)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            Image.new("RGB", (2, 2), colour).save(directory / "[0,0,0].png")
            model = MapboxTerrainRgbDirectory(directory)
            self.assertAlmostEqual(model.elevation_m(0.0, 0.0), 123.4, places=6)

    @unittest.skipUnless(
        importlib.util.find_spec("PIL"), "Pillow is required for DEM tiles"
    )
    def test_cached_real_terrarium_decoder(self) -> None:
        from PIL import Image

        elevation = 321.25
        encoded = elevation + 32768.0
        red = int(encoded // 256)
        green = int(encoded - red * 256)
        blue = round((encoded - red * 256 - green) * 256)
        with tempfile.TemporaryDirectory() as temporary:
            tile = Path(temporary) / "0" / "0" / "0.png"
            tile.parent.mkdir(parents=True)
            Image.new("RGB", (256, 256), (red, green, blue)).save(tile)
            model = TerrariumTileElevation(temporary, zoom=0)

            self.assertAlmostEqual(model.elevation_m(0.0, 0.0), elevation, places=6)
            self.assertEqual(
                model.provenance()["type"], "mapzen_terrarium_aws_open_data"
            )

    @unittest.skipUnless(importlib.util.find_spec("rasterio"), "rasterio is optional")
    def test_geotiff_source_uses_embedded_crs_and_bilinear_sampling(self) -> None:
        import numpy
        import rasterio
        from rasterio.transform import from_origin

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "real_dem.tif"
            with rasterio.open(
                path,
                "w",
                driver="GTiff",
                width=2,
                height=2,
                count=1,
                dtype="float32",
                crs="EPSG:4326",
                transform=from_origin(-5.0, 42.2, 0.1, 0.1),
                nodata=-9999.0,
            ) as dataset:
                dataset.write(
                    numpy.array([[100.0, 200.0], [300.0, 400.0]], dtype="float32"), 1
                )

            model = GeoTiffElevation(path)
            self.assertAlmostEqual(model.elevation_m(42.1, -4.9), 250.0, places=4)
            self.assertEqual(model.provenance()["crs"], "EPSG:4326")
