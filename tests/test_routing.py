import io
import json
import unittest

from geozigzag.metrics import forbidden_zone_intersections
from geozigzag.routing import (
    ExternalRoutingError,
    OSRMClient,
    RouteNotFound,
    generate_cost_route,
    generate_direct_route_from_ids,
    generate_osrm_route,
    load_geojson,
)


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class RoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.geojson = load_geojson("data/points.geojson")

    def test_direct_route_intersects_declared_zone(self) -> None:
        zone = [[
            (42.3099, -6.20545),
            (42.31018, -6.20545),
            (42.31018, -6.20500),
            (42.3099, -6.20500),
        ]]
        route = generate_direct_route_from_ids(
            self.geojson, ["water_1", "arbustivo_2", "water_2"], interval_m=10.0
        )
        self.assertGreater(forbidden_zone_intersections(route, zone), 0)

    def test_cost_route_avoids_declared_zone(self) -> None:
        zone = [[
            (42.3099, -6.20545),
            (42.31018, -6.20545),
            (42.31018, -6.20500),
            (42.3099, -6.20500),
        ]]
        route = generate_cost_route(
            self.geojson,
            ["water_1", "arbustivo_2", "water_2"],
            resolution_m=5.0,
            forbidden_zones=zone,
        )
        self.assertEqual(forbidden_zone_intersections(route, zone), 0)

    def test_impossible_route_raises(self) -> None:
        enclosing_start = [[
            (42.3090, -6.2043),
            (42.3096, -6.2043),
            (42.3096, -6.2037),
            (42.3090, -6.2037),
        ]]
        with self.assertRaises(RouteNotFound):
            generate_cost_route(
                self.geojson,
                ["water_1", "water_2"],
                resolution_m=3.0,
                forbidden_zones=enclosing_start,
            )

    def test_building_and_water_semantic_zones_are_impassable(self) -> None:
        for kind in ("building", "water"):
            zone = [
                (42.3098, -6.2048),
                (42.3102, -6.2048),
                (42.3102, -6.2044),
                (42.3098, -6.2044),
            ]
            route = generate_cost_route(
                self.geojson,
                ["water_1", "arbustivo_2"],
                resolution_m=2.0,
                semantic_zones=[{"kind": kind, "ring": zone}],
            )
            self.assertEqual(
                forbidden_zone_intersections(route, [zone]),
                0,
                msg=f"The route crossed a {kind} zone.",
            )

    def test_forest_semantic_zone_is_avoided_when_a_short_detour_exists(self) -> None:
        zone = [
            (42.3098, -6.2048),
            (42.3102, -6.2048),
            (42.3102, -6.2044),
            (42.3098, -6.2044),
        ]
        route = generate_cost_route(
            self.geojson,
            ["water_1", "arbustivo_2"],
            resolution_m=2.0,
            semantic_zones=[{"kind": "forest", "ring": zone}],
        )
        self.assertEqual(forbidden_zone_intersections(route, [zone]), 0)

    def test_scrub_semantic_zone_adds_a_traversal_penalty(self) -> None:
        zone = [
            (42.3098, -6.2048),
            (42.3102, -6.2048),
            (42.3102, -6.2044),
            (42.3098, -6.2044),
        ]
        direct = generate_cost_route(
            self.geojson,
            ["water_1", "arbustivo_2"],
            resolution_m=2.0,
        )
        scrub = generate_cost_route(
            self.geojson,
            ["water_1", "arbustivo_2"],
            resolution_m=2.0,
            semantic_zones=[{"kind": "scrub", "ring": zone}],
        )
        self.assertGreaterEqual(len(scrub), len(direct))

    def test_scrub_semantic_zone_has_a_finite_cost(self) -> None:
        zone = [
            (42.3098, -6.2048),
            (42.3102, -6.2048),
            (42.3102, -6.2044),
            (42.3098, -6.2044),
        ]
        route = generate_cost_route(
            self.geojson,
            ["water_1", "arbustivo_2"],
            resolution_m=2.0,
            semantic_zones=[{"kind": "scrub", "ring": zone}],
        )
        self.assertGreater(len(route), 2)

    def test_unknown_semantic_zone_kind_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown semantic zone kind"):
            generate_cost_route(
                self.geojson,
                ["water_1", "water_2"],
                semantic_zones=[
                    {
                        "kind": "lava",
                        "ring": [
                            (42.3100, -6.2050),
                            (42.3101, -6.2050),
                            (42.3101, -6.2049),
                        ],
                    }
                ],
            )

    def test_osrm_response_exposes_snap_distance(self) -> None:
        payload = {
            "code": "Ok",
            "waypoints": [{"distance": 2.5}, {"distance": 4.0}],
            "routes": [{"geometry": {"coordinates": [[-5.0, 42.0], [-4.999, 42.001]]}}],
        }
        client = OSRMClient(opener=lambda *args, **kwargs: _Response(payload))
        route, metadata = generate_osrm_route([(42.0, -5.0), (42.001, -4.999)], client)
        self.assertEqual(len(route), 2)
        self.assertEqual(metadata["max_snap_distance_m"], 4.0)

    def test_osrm_failure_is_wrapped(self) -> None:
        client = OSRMClient(opener=lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline")))
        with self.assertRaisesRegex(ExternalRoutingError, "OSRM request failed"):
            client.route([(42.0, -5.0), (42.001, -4.999)])


if __name__ == "__main__":
    unittest.main()
