import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from geozigzag.catastro import (
    CatastroBuildingSource,
    build_wfs_url,
    parse_building_gml,
    supports_bbox,
    utm_epsg_for_longitude,
)


@dataclass(frozen=True)
class BBox:
    south: float
    west: float
    north: float
    east: float


GML = b"""<?xml version="1.0" encoding="UTF-8"?>
<gml:FeatureCollection
  xmlns:gml="http://www.opengis.net/gml/3.2"
  xmlns:bu-ext2d="http://inspire.jrc.ec.europa.eu/schemas/bu-ext2d/2.0">
  <gml:featureMember>
    <bu-ext2d:Building gml:id="ES.SDGC.BU.1">
      <bu-ext2d:geometry2D>
        <gml:Surface srsName="EPSG::25829">
          <gml:patches><gml:PolygonPatch><gml:exterior><gml:LinearRing>
            <gml:posList>730300 4688200 730310 4688200 730310 4688210 730300 4688210 730300 4688200</gml:posList>
          </gml:LinearRing></gml:exterior></gml:PolygonPatch></gml:patches>
        </gml:Surface>
      </bu-ext2d:geometry2D>
    </bu-ext2d:Building>
  </gml:featureMember>
</gml:FeatureCollection>"""


class Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _limit):
        return GML


class CatastroTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bbox = BBox(42.311, -6.206, 42.314, -6.198)

    def test_spanish_bbox_uses_etrs89_utm_zone_29(self) -> None:
        self.assertTrue(supports_bbox(self.bbox))
        self.assertEqual(utm_epsg_for_longitude(-6.2), 25829)
        url, epsg = build_wfs_url(self.bbox)
        self.assertEqual(epsg, 25829)
        self.assertIn("typenames=BU.BUILDING", url)
        self.assertIn("srsname=EPSG%3A%3A25829", url)

    def test_gml_building_is_exposed_in_planner_contract(self) -> None:
        features = parse_building_gml(GML, 25829)
        self.assertEqual(len(features), 1)
        feature = features[0]
        self.assertEqual(feature["kind"], "building")
        self.assertEqual(feature["source"], "Dirección General del Catastro")
        self.assertAlmostEqual(feature["areaM2"], 100.0, places=3)
        self.assertEqual(len(feature["ring"]), 5)
        self.assertTrue(42.0 < feature["center"][0] < 43.0)
        self.assertTrue(-7.0 < feature["center"][1] < -5.0)

    def test_second_fetch_reuses_local_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = CatastroBuildingSource(temporary, cache_max_age_days=7)
            with patch("urllib.request.urlopen", return_value=Response()) as request:
                first = source.fetch(self.bbox)
                second = source.fetch(self.bbox)

            self.assertFalse(first.cached)
            self.assertTrue(second.cached)
            self.assertEqual(len(second.features), 1)
            self.assertEqual(request.call_count, 1)
            self.assertEqual(len(list(Path(temporary).glob("buildings_*.json"))), 1)

    def test_bbox_outside_spain_returns_empty_without_network(self) -> None:
        source = CatastroBuildingSource("unused")
        with patch("urllib.request.urlopen") as request:
            result = source.fetch(BBox(48.8, 2.2, 48.9, 2.4))
        self.assertEqual(result.features, [])
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
