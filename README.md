# GeoZigZag

GeoZigZag is a lightweight route-planning tool for agricultural robot missions.
It joins two practical workflows in one repository:

- **Field coverage**: generate back-and-forth zigzag waypoints from field
  corners on a Leaflet map with GPS imagery and street-map views.
- **Mission routing**: connect GeoJSON targets over OpenStreetMap/OSRM paths,
  direct interpolation, or local cost-aware routes.

The tool exports latitude, longitude, yaw, and planar quaternion values so the
same route can be inspected in the browser and reused by ROS-style waypoint
followers.

## Screenshots

### Field Coverage

![Field coverage mode](docs/screenshots/coverage.png)

### Mission Route

![Mission route mode](docs/screenshots/mission-costmap.png)

## Features

- Static Leaflet web app: open it directly or serve it from a local HTTP
  server.
- GPS imagery and OpenStreetMap base-map toggle in both coverage and mission
  modes.
- Editable WGS84 field corners, row spacing, waypoint spacing, start corner, and
  row bearing.
- GeoJSON mission targets with land-cover labels.
- Mission route mode follows the GeoRoute Planner visit-order workflow,
  defaults to OpenStreetMap paths/tracks, and keeps available POIs in a
  collapsible right-side waypoint drawer.
- Custom mission waypoints can be created by clicking the map, naming the
  point, and assigning a land-cover/type label.
- Online OpenStreetMap/OSRM path routing, plus direct interpolation and local
  costmap A* fallback routes.
- CSV and YAML exports for downstream robot navigation.
- Dependency-light Python core using only the standard library.

## Current Scope

This repository currently provides the geospatial route-planning part of the
larger agricultural simulation workflow:

- covered now: map preview, field coverage generation, semantic mission routing
  with selectable and custom POIs, OSM path routing in the browser, clean
  collapsible UI panels, CSV/YAML route exports, reproducible CLI demo, and
  browser-based inspection.
- not included yet: Geo2Gazebo terrain generation, Gazebo world creation,
  WILDBOAR/Jabali simulation launch files, or ROS 2 crop-follow navigation
  launchers.

Use the exported waypoints as the stable interface for the next integration
layer. Do not treat this repository as a complete ROS/Gazebo launcher until
those adapters are added.

## Quick Start

### Clone

Clone the repository with SSH:

```bash
git clone git@github.com:luispri2001/GeoZigZag.git
cd GeoZigZag
```

Optional but recommended on Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

Optional but recommended on Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
py -3 -m pip install -r requirements.txt
```

`requirements.txt` intentionally has no third-party runtime packages. The
Python planner uses only the standard library. The web UI loads Leaflet,
OpenStreetMap tiles, GPS imagery tiles, and OSRM path routes from public web
services, so the browser needs internet access for the online map and
OSM path-routing mode.

### Web App

Open the file directly:

```text
web/index.html
```

Or serve the repository locally:

```bash
python3 -m http.server 8000 --bind 127.0.0.1
```

On Windows PowerShell:

```powershell
py -3 -m http.server 8000 --bind 127.0.0.1
```

Then open:

```text
http://127.0.0.1:8000/
```

The browser UI needs internet access for Leaflet, OpenStreetMap tiles, GPS
imagery tiles, and OSRM path routing. Coverage mode opens on the GPS imagery
view by default. Mission route mode opens on the OpenStreetMap view by default
and can switch to GPS imagery with the top-right view toggle.

Mission routing defaults to `OpenStreetMap paths`, which mirrors the original
GeoRoute Planner preference for walk/path-style routing. OSM routes are built
segment by segment and direct connector legs are inserted so the route reaches
the real POI coordinates instead of stopping only at OSRM's snapped road
locations.

The `Waypoints` button opens the right-side POI drawer, where targets can be
added without crowding the route settings panel. In mission mode, click anywhere
on the map to create a named waypoint with a type/land-cover label. Custom
waypoints are available immediately in the drawer and can be added to the route
for the current browser session. If the OSRM service is unavailable, the browser
falls back to the local costmap route.

If port `8000` is already in use, choose another port:

```bash
python3 -m http.server 8001 --bind 127.0.0.1
```

Then open `http://127.0.0.1:8001/`.

To open the mission route view directly:

```text
http://127.0.0.1:8000/?mode=mission&strategy=osm
```

The root redirect preserves query parameters, so both `/` and
`/web/index.html` links can be used for direct mode links.

### Command-Line Demo

From the repository root:

```bash
python3 -m geozigzag.cli --out outputs
```

On Windows PowerShell:

```powershell
py -m geozigzag.cli --out outputs
```

The demo reads `data/points.geojson` and writes reproducible route files to
`outputs/`.

## Output Files

The CLI generates:

- `outputs/coverage_zigzag.csv`
- `outputs/coverage_zigzag.yaml`
- `outputs/mission_direct.csv`
- `outputs/mission_direct.yaml`
- `outputs/mission_costmap.csv`
- `outputs/mission_costmap.yaml`
- `outputs/summary.json`

Generated output files are ignored by Git. The `outputs/.gitkeep` file only
keeps the folder available in fresh clones.

## Verification

Run the local test suite:

```bash
python3 -m unittest discover -s tests -p "test_*.py"
```

Run the CLI smoke test:

```bash
python3 -m geozigzag.cli --out outputs
```

The default demo should produce a `summary.json` with these stable values:

```json
{
  "coverage": {
    "coverage_rows": 16,
    "points": 224
  },
  "mission_direct": {
    "points": 54
  },
  "mission_costmap": {
    "points": 83
  }
}
```

Check that the web app is served:

```bash
python3 -m http.server 8000 --bind 127.0.0.1
curl -I http://127.0.0.1:8000/
```

The `curl` response should be `HTTP/1.0 200 OK`. Stop the server with
`Ctrl+C` when finished.

For browser smoke testing, open coverage and mission route mode and confirm:

- Coverage starts with the `GPS` view active.
- Mission route starts with the `Map` view active and `OSM paths` selected.
- The `Waypoints` button opens the right-side POI drawer.
- Clicking the map in Mission route mode opens a form for creating a named
  custom waypoint.
- Sidebar sections and the legend can be collapsed to keep the map readable.

## Output Schema

CSV exports include:

| Field | Meaning |
| --- | --- |
| `latitude` | WGS84 latitude in degrees |
| `longitude` | WGS84 longitude in degrees |
| `yaw` | Heading in radians |
| `qx`, `qy`, `qz`, `qw` | Planar quaternion for ROS-style consumers |

YAML exports use the same route information in a waypoint list.

## Project Structure

```text
GeoZigZag/
|-- data/
|   `-- points.geojson
|-- docs/
|   `-- screenshots/
|-- geozigzag/
|   |-- __init__.py
|   |-- cli.py
|   `-- planning.py
|-- outputs/
|   `-- .gitkeep
|-- web/
|   `-- index.html
|-- .gitignore
|-- index.html
|-- README.md
`-- requirements.txt
```

## Development Checks

Check that the public repository does not include generated route files:

```bash
git status --short --ignored
```

Generated files under `outputs/` should appear as ignored files. Source changes
should be limited to intentional edits.
