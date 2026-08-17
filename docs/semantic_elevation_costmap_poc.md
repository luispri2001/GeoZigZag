# DEM layer for semantic navigation

## Intended behaviour

The DEM is a traversability layer in the existing local semantic A* planner.
It does not choose crop-row orientation and it does not treat altitude itself
as dangerous. A flat area at 1,500 m has no extra terrain cost; a steep or
abrupt area does.

The fused cell cost is:

```text
semantic_cost
+ slope_cost_multiplier * (slope_pct / preferred_slope_pct)^2
```

If `slope_pct > max_slope_pct`, the cell is impassable. This is combined with
the current policies:

- building and water polygons: impassable;
- forest and scrub: finite semantic penalties;
- DEM slope: finite quadratic penalty or impassable;
- A*: minimum accumulated fused cost.

`preferred_slope_pct` and `max_slope_pct` are robot-specific safety parameters,
not universal agricultural thresholds. They must be derived from Jabalí's
stability, traction and braking envelope before physical deployment.

## Reproducible POC

Run the synthetic hill example:

```bash
python3 -m geozigzag.semantic_elevation_cli \
  --demo \
  --out outputs/semantic-elevation-poc
```

It compares the original semantic route with the fused semantic+DEM route. The
second path avoids the red cells whose slope exceeds the configured limit.
Outputs include CSV/YAML/GeoJSON route bundles, `summary.json`, and
`costmap_preview.png`.

## Using gazebo_terrain_generator data

Install the optional Terrain-RGB reader and keep the generator's raw `dem/`
working directory:

```bash
python3 -m pip install -r requirements-dem.txt

python3 -m geozigzag.semantic_elevation_cli \
  --terrain-world /path/to/world_name \
  --geojson data/points.geojson \
  --targets start target_1 target_2 \
  --resolution 5.0 \
  --padding 10.0 \
  --preferred-slope-pct 5.0 \
  --max-slope-pct 18.0 \
  --out outputs/real-semantic-dem
```

The requested padding must remain inside the downloaded DEM coverage. Mapbox
terrain data is appropriate for broad hills and terrain exclusions, not small
furrows, ditches or wheel-scale roughness.

The current slope magnitude is direction-independent and therefore
conservative. A later edge-cost model can distinguish ascent, descent and
cross-slope using the robot heading, but it requires validated vehicle limits.

## Next integration boundary

The same parameters should be exposed in the web UI and scenario YAML. The
resulting path remains a global planning route; CropFollow can provide local
crop-row steering only after the global route has selected a traversable
corridor. Before robot use, add inflation for footprint/turning radius and a
separate lateral-slope check.
