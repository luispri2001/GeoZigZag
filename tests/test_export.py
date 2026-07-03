import csv
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from geozigzag.export import EXPORT_FIELDS, export_route_bundle
from geozigzag.geometry import points_to_waypoints


class ExportTests(unittest.TestCase):
    def test_bundle_schema_is_consistent(self) -> None:
        route = points_to_waypoints([(42.0, -5.0), (42.0001, -4.9999)])
        with tempfile.TemporaryDirectory() as temporary:
            files = export_route_bundle(route, temporary)
            self.assertEqual({path.name for path in files}, {"waypoints.csv", "waypoints.yaml", "route.geojson"})
            with Path(temporary, "waypoints.csv").open(newline="", encoding="utf-8") as handle:
                csv_rows = list(csv.DictReader(handle))
            yaml_data = yaml.safe_load(Path(temporary, "waypoints.yaml").read_text(encoding="utf-8"))
            geojson = json.loads(Path(temporary, "route.geojson").read_text(encoding="utf-8"))
            self.assertEqual(tuple(csv_rows[0]), EXPORT_FIELDS)
            self.assertEqual(set(yaml_data["waypoints"][0]["orientation"]), {"qx", "qy", "qz", "qw"})
            self.assertEqual(geojson["features"][0]["geometry"]["type"], "LineString")


if __name__ == "__main__":
    unittest.main()
