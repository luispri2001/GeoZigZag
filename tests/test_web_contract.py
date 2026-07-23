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


if __name__ == "__main__":
    unittest.main()
