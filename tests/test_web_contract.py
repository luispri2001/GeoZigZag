import unittest
from pathlib import Path


class WebContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = Path("web/index.html").read_text(encoding="utf-8")

    def test_edits_mark_route_pending_without_auto_generation(self) -> None:
        schedule = self.source.split("function scheduleGenerate", 1)[1].split("function finishGenerationState", 1)[0]
        self.assertIn("markDirty(reason, false)", schedule)
        self.assertNotIn("generate()", schedule)

    def test_status_and_snap_feedback_are_visible(self) -> None:
        for text in ("Generated with warnings", "Route could not be generated", "worstSnap.toFixed(1)"):
            self.assertIn(text, self.source)

    def test_manual_semantic_zones_are_available_to_local_astar(self) -> None:
        for kind in ("building", "water", "forest", "scrub"):
            self.assertIn(f'<option value="{kind}">', self.source)
        self.assertIn("SEMANTIC_ZONE_STYLE", self.source)
        self.assertIn("semanticCosts", self.source)
        self.assertNotIn("Automatic building-footprint safety", self.source)

    def test_public_osm_zones_create_resource_waypoints(self) -> None:
        for tag in (
            '["building"]',
            '["natural"="water"]',
            '["landuse"="forest"]',
            '["natural"="scrub"]',
        ):
            self.assertIn(tag, self.source)
        self.assertIn("loadVisiblePublicSemanticZones", self.source)
        self.assertIn("rebuildPublicSemanticWaypoints", self.source)
        self.assertIn('["forest", "scrub"]', self.source)

    def test_dem_controls_feed_the_local_semantic_astar(self) -> None:
        for control_id in (
            "terrainMode",
            "terrainPreferredSlope",
            "terrainMaxSlope",
            "terrainCostMultiplier",
            "terrainShowLayer",
        ):
            self.assertIn(f'id="{control_id}"', self.source)
        for contract in (
            "/api/dem/status",
            "/api/dem/grid",
            "applyElevationGrid",
            "localAstarWithTerrain",
            "terrainCostLayer",
            "await generateCostRoute",
        ):
            self.assertIn(contract, self.source)

    def test_dem_loader_surfaces_errors_instead_of_assuming_flat_ground(self) -> None:
        loader = self.source.split("async function loadElevationGrid", 1)[1].split(
            "function applyElevationGrid", 1
        )[0]
        self.assertIn("throw new Error", loader)
        self.assertNotIn("catch (", loader)

    def test_normal_url_defaults_to_mission_cost_astar_and_real_dem(self) -> None:
        self.assertIn('<option value="cost" selected>Local costmap A*</option>', self.source)
        self.assertIn('<option value="server" selected>Real DEM (server)</option>', self.source)
        self.assertIn('params.get("mode") === "coverage"', self.source)
        self.assertIn('params.get("terrain")', self.source)
        self.assertNotIn("syntheticElevationGrid", self.source)
        self.assertNotIn('value="demo"', self.source)


if __name__ == "__main__":
    unittest.main()
