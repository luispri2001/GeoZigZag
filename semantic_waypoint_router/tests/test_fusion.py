import numpy as np

from public_map_generator.config import TerrainConfig, WeightsConfig
from public_map_generator.fusion import fuse_layers
from public_map_generator.terrain import derive_terrain_layers


def test_building_is_blocked_and_road_reduces_cost():
    elevation = np.zeros((50, 50), dtype=np.float32)
    terrain = derive_terrain_layers(elevation, 1.0, 5.0, 9.0)
    zeros = np.zeros_like(elevation)
    osm = {
        "buildings": zeros.copy(),
        "roads": zeros.copy(),
        "water": zeros.copy(),
        "waterways": zeros.copy(),
        "wetlands": zeros.copy(),
        "forest": zeros.copy(),
        "farmland": zeros.copy(),
        "grass": zeros.copy(),
        "scrub": zeros.copy(),
        "barriers": zeros.copy(),
    }
    osm["buildings"][10:15, 10:15] = 1.0
    osm["roads"][25:30, :] = 1.0
    fused = fuse_layers(
        terrain,
        osm,
        TerrainConfig(),
        WeightsConfig(),
        1.0,
        source_flags={"mdt": True, "osm": True},
    )
    assert np.all(fused.traversability_prior[10:15, 10:15] == 1.0)
    assert np.nanmean(fused.traversability_prior[25:30]) <= np.nanmean(
        fused.traversability_prior[0:5]
    )


def test_semantic_risks_are_bounded_and_increase_near_water():
    elevation = np.zeros((40, 40), dtype=np.float32)
    terrain = derive_terrain_layers(elevation, 1.0, 5.0, 9.0)
    zeros = np.zeros_like(elevation)
    osm = {
        name: zeros.copy()
        for name in [
            "buildings",
            "roads",
            "water",
            "waterways",
            "wetlands",
            "forest",
            "farmland",
            "grass",
            "scrub",
            "barriers",
        ]
    }
    osm["water"][18:22, 18:22] = 1.0
    osm["farmland"][:, :] = 1.0

    fused = fuse_layers(terrain, osm, TerrainConfig(), WeightsConfig(), 1.0)

    assert np.nanmin(fused.mud_risk) >= 0.0
    assert np.nanmax(fused.mud_risk) <= 1.0
    assert np.nanmin(fused.water_accumulation_risk) >= 0.0
    assert np.nanmax(fused.water_accumulation_risk) <= 1.0
    assert fused.water_accumulation_risk[20, 20] > fused.water_accumulation_risk[0, 0]
