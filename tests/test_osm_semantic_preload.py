import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    "osm_semantic_preload_impl",
    Path("scripts/osm_buildings_preload.py"),
)
assert SPEC and SPEC.loader
osm = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = osm
SPEC.loader.exec_module(osm)


class OsmSemanticPreloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bbox = osm.BBox(41.999, -5.001, 42.002, -4.998)

    def test_query_requests_supported_public_layers(self) -> None:
        query = osm.overpass_query(self.bbox)
        for expression in (
            '["building"]',
            '["natural"="water"]',
            '["landuse"="reservoir"]',
            '["natural"="wood"]',
            '["landuse"="forest"]',
            '["natural"="scrub"]',
        ):
            self.assertIn(expression, query)
        self.assertIn("out geom;", query)

    def test_way_polygons_are_classified_and_get_centers(self) -> None:
        elements = []
        tag_sets = (
            {"building": "yes"},
            {"natural": "water"},
            {"landuse": "forest"},
            {"natural": "scrub"},
        )
        for index, tags in enumerate(tag_sets, start=1):
            west = -5.0 + index * 0.0002
            elements.append(
                {
                    "type": "way",
                    "id": index,
                    "tags": tags,
                    "geometry": [
                        {"lat": 42.0, "lon": west},
                        {"lat": 42.0, "lon": west + 0.0001},
                        {"lat": 42.0001, "lon": west + 0.0001},
                        {"lat": 42.0001, "lon": west},
                        {"lat": 42.0, "lon": west},
                    ],
                }
            )

        features = osm.parse_semantic_features({"elements": elements}, self.bbox)

        self.assertEqual(
            {feature["kind"] for feature in features},
            {"building", "water", "forest", "scrub"},
        )
        for feature in features:
            self.assertGreater(feature["areaM2"], 50.0)
            self.assertEqual(len(feature["center"]), 2)
            self.assertEqual(feature["source"], "OpenStreetMap")

    def test_relation_outer_fragments_are_joined(self) -> None:
        relation = {
            "type": "relation",
            "id": 99,
            "tags": {"natural": "wood", "name": "Example wood"},
            "members": [
                {
                    "role": "outer",
                    "geometry": [
                        {"lat": 42.0, "lon": -5.0},
                        {"lat": 42.0, "lon": -4.9998},
                        {"lat": 42.0002, "lon": -4.9998},
                    ],
                },
                {
                    "role": "outer",
                    "geometry": [
                        {"lat": 42.0002, "lon": -4.9998},
                        {"lat": 42.0002, "lon": -5.0},
                        {"lat": 42.0, "lon": -5.0},
                    ],
                },
            ],
        }

        features = osm.parse_semantic_features({"elements": [relation]}, self.bbox)

        self.assertEqual(len(features), 1)
        self.assertEqual(features[0]["kind"], "forest")
        self.assertEqual(features[0]["name"], "Example wood")
        self.assertGreater(features[0]["areaM2"], 100.0)

    def test_complete_tile_cache_is_reused_and_clipped(self) -> None:
        bbox = osm.BBox(42.0001, -4.9999, 42.0002, -4.9998)
        tile_key, tile_bbox = osm.tiles_for_bbox(bbox)[0]
        ring = [
            [tile_bbox.south, tile_bbox.west],
            [tile_bbox.south, tile_bbox.east],
            [tile_bbox.north, tile_bbox.east],
            [tile_bbox.north, tile_bbox.west],
            [tile_bbox.south, tile_bbox.west],
        ]
        feature = {
            "id": "relation/1/forest",
            "kind": "forest",
            "ring": ring,
            "bbox": osm.route_bbox(ring),
            "center": [42.0, -5.0],
            "areaM2": 1.0,
        }
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            payload = {"features": [feature]}
            (output / osm.tile_filename(tile_key)).write_text(
                json.dumps(payload),
                encoding="utf-8",
            )
            cached = osm.read_cached_bbox(output, bbox)

        self.assertIsNotNone(cached)
        self.assertEqual(len(cached), 1)
        self.assertEqual(cached[0]["kind"], "forest")
        self.assertTrue(bbox.south <= cached[0]["center"][0] <= bbox.north)
        self.assertTrue(bbox.west <= cached[0]["center"][1] <= bbox.east)


if __name__ == "__main__":
    unittest.main()
