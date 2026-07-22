import json

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin

from public_map_generator.route_planner import (
    PlanningProfile,
    SemanticRoutePlanner,
    astar,
    build_semantic_costmap,
    save_route_bundle,
)


def _write_layer(root, group, name, data):
    path = root / "layers" / group / f"{name}.tif"
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=data.shape[1],
        height=data.shape[0],
        count=1,
        dtype="float32",
        crs="EPSG:3857",
        transform=from_origin(0, 100, 5, 5),
        nodata=-9999,
    ) as dataset:
        dataset.write(data.astype("float32"), 1)
    return path


def _project(tmp_path, buildings=None, scrub=None, forest=None, water=None):
    zeros = np.zeros((20, 20), dtype=np.float32)
    _write_layer(tmp_path, "terrain", "elevation", np.full((20, 20), 100.0))
    _write_layer(tmp_path, "terrain", "slope_degrees", zeros)
    _write_layer(tmp_path, "terrain", "max_neighbor_step", zeros)
    _write_layer(tmp_path, "osm", "buildings", buildings if buildings is not None else zeros)
    _write_layer(tmp_path, "osm", "water", water if water is not None else zeros)
    for name in ("waterways", "barriers", "roads", "wetlands"):
        _write_layer(tmp_path, "osm", name, zeros)
    _write_layer(tmp_path, "osm", "scrub", scrub if scrub is not None else zeros)
    _write_layer(tmp_path, "osm", "forest", forest if forest is not None else zeros)
    _write_layer(tmp_path, "fusion", "obstacle_probability", zeros)
    return tmp_path


def _wgs84_polygon():
    transformer = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    coordinates = [
        transformer.transform(x, y) for x, y in [(10, 10), (90, 10), (90, 90), (10, 90), (10, 10)]
    ]
    return {"type": "Polygon", "coordinates": [coordinates]}


def test_astar_avoids_hard_obstacle():
    costs = np.ones((12, 12), dtype=np.float32)
    costs[:, 6] = np.inf
    costs[2, 6] = 1.0
    path = astar(costs, (8, 1), (8, 10))
    assert path
    assert (2, 6) in path
    assert all(np.isfinite(costs[cell]) for cell in path)


def test_default_costmap_ignores_scrub(tmp_path):
    project = _project(tmp_path, scrub=np.ones((20, 20), dtype=np.float32))
    costs, blocked, metadata = build_semantic_costmap(project, PlanningProfile(clearance_m=0))
    assert "scrub" not in metadata["used_layers"]
    assert "scrub" in metadata["ignored_layers"]
    assert not blocked.any()
    assert np.allclose(costs, 1.0)


def test_route_and_coverage_export(tmp_path):
    buildings = np.zeros((20, 20), dtype=np.float32)
    buildings[:, 10] = 1.0
    buildings[4, 10] = 0.0
    project = _project(tmp_path, buildings=buildings)
    planner = SemanticRoutePlanner(project, PlanningProfile(clearance_m=0))
    to_wgs84 = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    start = to_wgs84.transform(10, 40)
    goal = to_wgs84.transform(90, 40)
    plan = planner.plan_line([list(start), list(goal)], waypoint_spacing_m=5)
    assert plan.metrics["distance_m"] > 80
    assert plan.metrics["waypoint_count"] > 2
    assert all({"yaw", "qz", "qw"} <= point.keys() for point in plan.waypoints)
    for point in plan.waypoints:
        xy = planner._wgs_to_xy(point["longitude"], point["latitude"])
        assert not planner.blocked[planner._xy_to_cell(xy)]

    clean_project = _project(tmp_path / "coverage")
    coverage_planner = SemanticRoutePlanner(
        clean_project,
        PlanningProfile(clearance_m=0),
        constraint_geometry=_wgs84_polygon(),
    )
    coverage = coverage_planner.plan_coverage(
        _wgs84_polygon(), row_spacing_m=20, waypoint_spacing_m=5, bearing_deg=90
    )
    assert coverage.metrics["coverage_rows"] == 4
    assert coverage.metrics["covered_area_m2"] == 6400.0

    destination = save_route_bundle(coverage, clean_project)
    assert (destination / "route.csv").exists()
    assert (destination / "route.yaml").exists()
    payload = json.loads((destination / "route.geojson").read_text(encoding="utf-8"))
    assert payload["features"][0]["geometry"]["type"] == "LineString"


def test_manual_and_semantic_waypoint_mission(tmp_path):
    forest = np.zeros((20, 20), dtype=np.float32)
    forest[3:6, 3:6] = 1.0
    forest[13:17, 13:17] = 1.0
    project = _project(tmp_path, forest=forest)
    planner = SemanticRoutePlanner(project, PlanningProfile(clearance_m=0))
    to_wgs84 = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    start = to_wgs84.transform(10, 40)
    manual = to_wgs84.transform(35, 40)
    plan = planner.plan_waypoint_mission(
        start,
        manual_waypoints=[{"name": "Entrada", "longitude": manual[0], "latitude": manual[1]}],
        semantic_layers=["forest"],
        waypoint_spacing_m=5,
        minimum_semantic_area_m2=25,
    )
    assert plan.metrics["manual_targets"] == 1
    assert plan.metrics["automatic_targets"] == 2
    assert [target["source"] for target in plan.targets].count("manual_waypoint") == 1
    assert [target.get("semantic_layer") for target in plan.targets].count("forest") == 2
    target_features = [
        feature
        for feature in plan.geojson()["features"]
        if feature["properties"].get("waypoint_type") == "mission_target"
    ]
    assert len(target_features) == 4  # origin, one manual target and two automatic targets


def test_water_resource_creates_safe_approach_waypoint(tmp_path):
    water = np.zeros((20, 20), dtype=np.float32)
    water[7:12, 7:12] = 1.0
    project = _project(tmp_path, water=water)
    planner = SemanticRoutePlanner(project, PlanningProfile(clearance_m=0))
    to_wgs84 = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    start = to_wgs84.transform(10, 40)
    plan = planner.plan_waypoint_mission(
        start,
        semantic_layers=["water"],
        waypoint_spacing_m=5,
        minimum_semantic_area_m2=25,
    )
    resource = next(target for target in plan.targets if target.get("semantic_layer") == "water")
    assert resource["target_category"] == "resource"
    assert resource["target_relation"] == "approach_to_feature"
    target_xy = planner._wgs_to_xy(resource["longitude"], resource["latitude"])
    assert not planner.blocked[planner._xy_to_cell(target_xy)]
