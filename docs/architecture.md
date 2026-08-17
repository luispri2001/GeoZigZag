# Architecture and coordinate conventions

## Workflow

```text
WGS84 field/targets + parameters + semantic zones
                         |
                         v
                 local ENU projection
                  /                 \
       coverage sweep planner    mission router
                  \                 /
                         v
        spacing/intersection/snap validation
                         |
                         v
     route + metrics + CSV/YAML/GeoJSON + provenance
                         |
                         v
             external ROS 2 consumer
```

The browser is an authoring interface. The Python modules are the reproducible
reference implementation used for evaluation.

## Coordinates and orientation

- Public geospatial coordinates are `(latitude, longitude)` in WGS84 degrees.
- Local calculations use an equirectangular tangent approximation centered on
  the scenario. `x` points east and `y` points north.
- This approximation is restricted to field-scale and sub-kilometre routes.
- Yaw follows ROS REP-103: radians counter-clockwise from east.
- The exported planar quaternion is
  `(0, 0, sin(yaw / 2), cos(yaw / 2))`.

Gazebo and ROS `map`, `odom`, and `base_link` coordinates are not inferred from
WGS84 exports. A downstream integration must define a surveyed origin, world
heading, altitude policy, and TF tree explicitly.

## Planner boundaries

`coverage.py` clips parallel sweep lines to a polygon and samples each interval.
Straight connectors between intervals are planning geometry; concave fields may
require an explicit in-polygon connector planner.

`routing.py` provides:

- direct interpolation between ordered targets;
- semantic-cost A* on an 8-connected local grid, optionally fused with a
  DEM-derived slope penalty/blocking layer; and
- live or cached OSRM adapters with snap-distance validation.

In the web workflow, the local server exposes bounded `/api/dem/status` and
`/api/dem/grid` endpoints. The default source is the real AWS Open Data
Terrarium dataset with a local tile cache. Adapters also sample a real
single-band GeoTIFF in its embedded CRS or raw Terrain-RGB data kept by
`gazebo_terrain_generator`. Absolute altitude is retained for provenance but
only local slope changes traversability. The browser renders the sampled
costmap and runs A* over the fused semantic and terrain grid. It has no
synthetic or flat fallback.

Manual and downloaded OpenStreetMap semantic polygons use a deliberately small
policy:

| Zone | Local A* policy | OSM Balanced/Strict |
| --- | --- | --- |
| Building | impassable | intersection validation/local fallback |
| Water | impassable | intersection validation/local fallback |
| Forest | high traversal cost (`80`) | visual context only |
| Scrub | medium traversal cost (`45`) | visual context only |

The base local-grid cost is `10`. Forest is penalized rather than universally
blocked because mapped woodland may include usable tracks or sparse areas.
This is a planning assumption, not a claim of measured traversability.
Previously stored untyped polygons are loaded as buildings.

The web application requests `building=*`, `natural=water`,
`landuse=reservoir`, `natural=wood`, `landuse=forest`, and `natural=scrub`
through the local `/api/osm/semantic` proxy, with public Overpass as a fallback.
Queries are limited to 9 km². Large OSM relations are clipped to the requested
bounds before display, costmap use, area measurement, or centroid calculation.
Forest and scrub components above the UI threshold create selectable resource
waypoints at their clipped polygon centers. Public targets are never added to a
mission without the user's selection.

`metrics.py` counts polyline heading changes of at least 30 degrees. This is not
a dynamically feasible steering or headland-turn metric.

## Stable interchange boundary

The route bundle is the stable interface between GeoZigzag and downstream
robot software. Consumers must validate coordinate frames, localization,
controller limits, obstacle layers, and achievable curvature before execution.
