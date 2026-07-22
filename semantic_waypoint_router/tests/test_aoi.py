from public_map_generator.aoi import build_aoi
from public_map_generator.config import AOIConfig


def test_center_radius_builds_projected_aoi():
    aoi = build_aoi(
        AOIConfig(type="center_radius", lat=42.5987, lon=-5.5671, radius_m=100),
        "EPSG:25830",
    )
    minx, miny, maxx, maxy = aoi.bounds
    assert 190 <= maxx - minx <= 210
    assert 190 <= maxy - miny <= 210
    west, south, east, north = aoi.bounds_wgs84
    assert west < east
    assert south < north
