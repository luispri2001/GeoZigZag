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

    def test_fused_public_zones_create_resource_waypoints(self) -> None:
        for tag in (
            '["building"]',
            '["natural"="water"]',
            '["landuse"="forest"]',
            '["natural"="scrub"]',
            '["natural"="shrubbery"]',
            '["natural"="heath"]',
            '["landcover"="shrubs"]',
        ):
            self.assertIn(tag, self.source)
        self.assertIn("loadVisiblePublicSemanticZones", self.source)
        self.assertIn("rebuildPublicSemanticWaypoints", self.source)
        self.assertIn('["forest", "scrub"]', self.source)
        self.assertIn("/api/catastro/buildings", self.source)
        self.assertIn("downloadCatastroBuildings", self.source)
        self.assertIn("droppedDuplicateOsmBuildings", self.source)

    def test_public_vegetation_waypoints_are_automatically_inserted(self) -> None:
        for control_id in ("publicAutoWaypoints", "publicAutoWaypointLimit"):
            self.assertIn(f'id="{control_id}"', self.source)
        for contract in (
            "insertMissionStopWithMinimumDetour",
            "autoWaypointCount",
            "automatically selected",
            "pointHitsSemanticZone(zone.center",
            "maximumDemCells = 39000",
        ):
            self.assertIn(contract, self.source)
        refresh = self.source.split("async function refreshMissionPublicSemanticZones", 1)[1].split(
            "function removePublicSemanticWaypoints", 1
        )[0]
        self.assertIn("refreshPublicSemanticZones(bbox, true)", refresh)

    def test_local_cost_route_requires_public_corridor_data_first(self) -> None:
        self.assertIn('id="publicRouteBuffer"', self.source)
        self.assertIn("function missionPublicSemanticBbox", self.source)
        self.assertIn("async function refreshMissionPublicSemanticZones", self.source)
        cost_branch = self.source.split('else if (strategy === "cost")', 1)[1].split(
            "} else {", 1
        )[0]
        self.assertLess(
            cost_branch.index("await refreshMissionPublicSemanticZones()"),
            cost_branch.index("await generateCostRoute"),
        )
        self.assertIn("Hard-obstacle validation:", self.source)
        self.assertIn('id="semanticSafetyBuffer"', self.source)
        self.assertIn("validateHardObstacleEndpoints", self.source)
        self.assertIn("publicSemanticCoverageBbox", self.source)
        self.assertIn("planningBbox", self.source)
        self.assertIn("no unsafe route was exported", self.source)

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
            "terrainRouteProfile",
            "localAstarWithTerrain",
            "terrainCostLayer",
            "Slope heatmap",
            "DEM slope: up to",
            "await generateCostRoute",
        ):
            self.assertIn(contract, self.source)

    def test_dem_heatmap_has_visible_slope_bands(self) -> None:
        for contract in (
            "terrain-low",
            "terrain-moderate",
            "terrain-cost",
            "terrain-blocked",
            "terrainSlopePane",
            "Costly slope",
            "Blocked slope >",
        ):
            self.assertIn(contract, self.source)

    def test_route_details_expose_dem_profile_and_robot_limits(self) -> None:
        for contract in (
            "routeAscentM",
            "routeDescentM",
            "routeMeanAbsoluteSlopePct",
            "routeMaxSlopePct",
            "blockedAboveSlopePct",
            "Route terrain profile:",
        ):
            self.assertIn(contract, self.source)

    def test_large_cost_routes_reuse_dem_and_binary_heap(self) -> None:
        for contract in (
            'location.protocol === "file:"',
            "pushHeap",
            "popHeap",
            "elevationGrids: new Map()",
            "renderedGridKeys: new Set()",
            "reusedElevationGrid",
            "terrainSlopeRenderer",
        ):
            self.assertIn(contract, self.source)
        astar = self.source.split("function astarGrid", 1)[1].split(
            "function buildLocalSemanticGrid", 1
        )[0]
        self.assertNotIn("heap.sort", astar)

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

    def test_default_destination_is_not_the_known_cadastral_building(self) -> None:
        self.assertIn("coordinates:[-6.200150,42.312900]", self.source)
        self.assertNotIn("coordinates:[-6.200150,42.312750]", self.source)


if __name__ == "__main__":
    unittest.main()
