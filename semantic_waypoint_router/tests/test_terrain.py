import numpy as np

from public_map_generator.terrain import derive_terrain_layers


def test_plane_slope_is_constant():
    resolution = 1.0
    y, x = np.mgrid[0:50, 0:60]
    elevation = (100.0 + 0.1 * x).astype(np.float32)
    layers = derive_terrain_layers(elevation, resolution, 5.0, 9.0)
    expected = np.degrees(np.arctan(0.1))
    assert np.allclose(layers.slope[3:-3, 3:-3], expected, atol=1e-3)
    assert np.nanmax(layers.roughness) > 0


def test_nodata_is_preserved():
    elevation = np.ones((20, 20), dtype=np.float32)
    elevation[5:8, 5:8] = np.nan
    layers = derive_terrain_layers(elevation, 1.0, 5.0, 9.0)
    assert np.isnan(layers.elevation[6, 6])
    assert np.isnan(layers.slope[6, 6])
