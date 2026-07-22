from pathlib import Path

from public_map_generator.config import load_config


def test_example_config_loads():
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "config" / "minimal_leon.yaml")
    assert config.aoi.radius_m == 500
    assert config.sources.ign_mdt.coverage == "Elevacion25830_5"
