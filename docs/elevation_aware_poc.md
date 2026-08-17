# Elevation-aware crop-row planning POC

## Scope

This proof of concept compares fixed, parallel crop-row orientations inside a
selected field. It does not bend crop rows around individual DEM cells. That
constraint keeps the result agronomically interpretable and lets an experiment
compare distance, turns and slope without silently changing the field layout.

For every candidate orientation, the implementation:

1. reuses `generate_zigzag_polygon` to create the coverage path;
2. samples elevation at every waypoint;
3. computes segment grade, horizontal and three-dimensional distance, ascent,
   descent, maximum grade, mean absolute grade and RMS grade;
4. rejects the candidate if any row or row-change connector exceeds the
   configured grade limit; and
5. minimises

   `distance_3d + slope_weight * sum(distance_xy * grade^2) + turn_penalty * turns`.

The weights are experimental parameters, not calibrated energy coefficients.
They must be fixed before collecting comparative results.

## Offline deterministic demo

The built-in source is an explicitly labelled tilted plane. It verifies the
pipeline without network access and is not evidence about a real field:

```bash
python3 -m geozigzag.elevation_cli \
  --demo \
  --out outputs/elevation-poc
```

Outputs are:

- `summary.json`: selected orientation, constraints, DEM provenance and all
  candidate metrics;
- `candidates.csv`: compact comparison table;
- `waypoints_3d.csv`: WGS84, absolute elevation, local Gazebo-style `z`, grade,
  yaw and quaternion;
- `route_3d.geojson`: three-dimensional route for GIS inspection.
- `preview.png`: route coloured by elevation and orientation-cost comparison.

If every candidate exceeds `--max-grade-pct`, the command records the rejected
candidates, does not emit a selected route and exits with status 2.

## gazebo_terrain_generator working data

Install the small optional image dependency:

```bash
python3 -m pip install -r requirements-dem.txt
```

Keep the terrain generator working directory containing both `metadata.json`
and the raw `dem/` tiles. Then run:

```bash
python3 -m geozigzag.elevation_cli \
  --polygon data/field.geojson \
  --terrain-world /path/to/gazebo_terrain_generator/output/world_name \
  --row-spacing 4.0 \
  --point-spacing 2.0 \
  --angle-step 15 \
  --max-grade-pct 18.0 \
  --out outputs/world-name-elevation
```

The adapter reads the upstream `[zoom,tile_y,tile_x].png` naming scheme and
decodes Mapbox Terrain-RGB using `-10000 + encoded_value * 0.1` metres. It uses
the `dem_resolution` field in the upstream metadata and bilinear sampling
within each tile.

Use the raw Terrain-RGB tiles for planning, not only the normalised Gazebo
`mesh/height_map.png`: the latter no longer carries absolute elevation directly.
The route's `z_local_m` is relative to its first waypoint; the final Gazebo/ROS
integration must instead apply one declared scenario origin consistently.

## Current limitations

- Mapbox's source resolution, not PNG bit depth or resized heightmap dimensions,
  limits the terrain detail. It is unsuitable for detecting small furrows,
  drainage ditches or wheel-scale roughness.
- The POC samples one elevation surface and does not model soil, vegetation,
  obstacles, roll stability, traction or vehicle dynamics.
- Grade is evaluated along the centreline. Vehicle footprint and lateral slope
  need a second sampling pass on both sides of the path.
- The current output is a planning artifact. It has not yet been consumed by
  WILDBOAR, CropFollow or a running ROS 2/Gazebo system.

The next useful extension is a `GeoTiffElevationModel` for local LiDAR or drone
DEMs, followed by lateral-slope and robot-specific traversability checks.
