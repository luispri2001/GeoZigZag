# GeoZigzag Studio

A local web application for agricultural field coverage and geographic mission
planning. Draw a field on the map, configure rows and robot kinematics, preview
the route, and download waypoints and plan metadata.

## Install and run

Requires Python 3.10 or newer.

```bash
git clone git@github.com:luispri2001/GeoZigZag.git
cd GeoZigZag
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m geozigzag
```

Open [localhost:8000](http://localhost:8000/). The server prints the actual port
and tries the next one if the default is busy. `make serve` is equivalent.
Do not use `file://` or a standalone static server: coverage and geospatial
features require the included Python APIs.

```bash
python -m geozigzag --port 8080 --no-auto-port
python -m geozigzag --help
```

Runtime dependencies are Shapely (field geometry), Pillow (DEM tiles) and pyproj
(Catastro coordinate transforms). Leaflet and map imagery load from public
services. Uncached elevation, Catastro, OSM and optional OSRM requests require
internet access; downloaded geospatial responses are cached locally.

## Field coverage

Open [Coverage](http://localhost:8000/?mode=coverage), draw a rectangle or polygon,
then configure row spacing, waypoint spacing, bearing and start corner.

The **Robot & headland turns** panel supports:

- **Differential drive:** stop, rotate in place, move to the next row, rotate.
- **Skid-steer / tracked:** the same ideal geometry; soil slip is not modelled.
- **Ackermann:** forward Dubins turns; minimum radius comes from wheelbase and
  maximum steering angle.

Set the body clearance radius and headland inset. Clearance must enclose the
complete robot and implement about the rotation centre, or rear axle for
Ackermann. Press **Generate route**: working rows are green and turns orange.
A turn that cannot fit inside the checked field/headland prevents export.
Changing parameters invalidates downloads until regeneration.

**Load reproducible field demo** provides a fixed geometric example. It is not a
survey of real land use or obstacles at those coordinates. The same planner is
available from the command line:

```bash
python -m geozigzag.coverage_cli
python -m geozigzag.coverage_cli --robot differential
python -m geozigzag.coverage_cli --config configs/robot_coverage_demo.json
```

Each run creates a new timestamped directory under `outputs/`. `--out outputs/my-new-run` selects an explicit directory; existing directories are
rejected to prevent overwriting results.

See [robot headland planning](docs/robot_headlands.md) for parameters, geometry,
actions and limitations.

## Mission routing

The normal URL opens **Mission route**, with local semantic A* and the real DEM.
Use the waypoint drawer or click the map to add ordered targets. Strategies are
Direct interpolation, Local costmap A*, and optional OSRM paths.

Local A* downloads a bounded corridor of public semantic data before generation.
Spanish Cadastre supplies buildings where available; OSM adds buildings, water,
forest and scrub. Buildings/water are blocked, forest/scrub add traversal cost,
and DEM slope adds cost or blocks steep cells. Manual semantic zones are also
supported. Missing mandatory data or invalid obstacle clearance produces a
visible error. DEM failures never silently substitute synthetic or flat terrain.

This is geographic route preparation. Mission paths do not enforce the robot
headland kinematics used in Coverage.

### Elevation inputs

The default real DEM uses public AWS Terrarium tiles, cached under
`data/dem_cache/`. Alternative inputs:

```bash
# GeoTIFF support is optional.
python -m pip install -r requirements-dem.txt
python -m geozigzag --dem-geotiff /path/to/elevation.tif

# Existing raw Terrain-RGB tiles with metadata.json and dem/.
python -m geozigzag --terrain-world /path/to/terrain-directory
```

`--no-dem` disables server elevation; also select **Off** in the UI to plan
without it. `--no-catastro` is available for locations outside its coverage or
explicit diagnostics. Catastro caches use `data/catastro_cache/`, and OSM caches
use `web/osm_semantic_cache/`. Caches and route outputs are not committed.

## Exports

Web downloads: CSV, YAML, Mission Planner/ArduPilot `.waypoints` and Plan JSON.
Coverage CLI also exports GeoJSON and `.waypoints`.
Coordinates are WGS84 latitude/longitude; yaw is in radians counterclockwise
from east. Quaternions use the same planar ENU orientation.

Coverage exports retain `action` (`drive` or `rotate`) and `phase` (`row` or
`headland`). A stationary rotation intentionally repeats a position with a new
yaw. Consumers must stop and execute that action rather than deduplicating it.
The ArduPilot WPL export is a position-only mission at 0 m relative altitude:
it removes consecutive duplicate positions and does not encode yaw or stationary
`rotate` actions. ArduPilot's vehicle controller therefore determines how turns
are driven. Plan JSON includes robot settings, route geometry and metrics. No
ROS/Gazebo controller, timing, dynamic model or frame conversion is provided.

See [obstacle evaluation](docs/obstacle_evaluation.md) for the current planner
scope and why obstacle count alone cannot define an optimality threshold.

## Tests

```bash
python -m pip install -r requirements-dev.txt
make test
make test-integration
```

Tests cover coverage kinematics, containment, exports, DEM decoding, Catastro,
OSM parsing/caches, web contracts, API/CLI consistency and output protection.
They do not require live map services, ROS or Gazebo. The optional GeoTIFF test
is skipped unless rasterio is installed.

## Repository layout

```text
web/                  HTML, CSS and browser mission/map logic
geozigzag/            HTTP APIs, geospatial sources and robot coverage planner
configs/             Field fixture used by the web demo and CLI
tests/               Unit and HTTP/CLI integration checks
docs/                Architecture and headland/consumer documentation
outputs/              Generated CLI routes (ignored)
data/                 Downloaded DEM and Catastro caches (ignored)
```

[Architecture and coordinates](docs/architecture.md)

## Troubleshooting

- **API unavailable:** start `python -m geozigzag` from the repository root and
  use the port printed by the server.
- **No turn fits:** increase headland, change row direction/spacing, or review
  the vehicle dimensions and steering limits.
- **DEM/semantic request fails:** check connectivity and the visible error.
  Cached areas can be reused; uncached public data needs a working service.
- **Stale downloads:** regenerate after changing the field, robot or routing
  parameters.
