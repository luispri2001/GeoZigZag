# GeoZigZag

GeoZigZag is a lightweight route-planning tool for agricultural robot missions.
It combines two planning modes in one small repository:

- **Coverage planning**: generates back-and-forth zigzag waypoints inside a field.
- **Semantic routing**: connects GeoJSON mission targets using direct or cost-aware routes.

The planner exports latitude, longitude, yaw, and quaternion fields that can be
used by ROS-style waypoint followers.

## Structure

```text
GeoZigZag/
|-- data/points.geojson
|-- geozigzag/
|   |-- cli.py
|   `-- planning.py
|-- outputs/
|-- web/index.html
`-- requirements.txt
```

## Web App

Open `web/index.html` in a browser.

The interface has two tabs:

- `Coverage`: edit field corners, row spacing, waypoint spacing, start corner,
  and row direction.
- `Mission route`: choose semantic GeoJSON target IDs and generate direct or
  cost-aware routes.

CSV and YAML downloads are available from the UI.

## Command-Line Demo

From the repository root:

```powershell
py -m geozigzag.cli --out outputs
```

This generates reproducible demo files in `outputs/`.

## Dependencies

The core planner uses only the Python standard library. `requirements.txt`
contains optional runtime helpers.
