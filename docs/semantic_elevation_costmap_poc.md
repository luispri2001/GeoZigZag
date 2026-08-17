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

## Web integration with real elevation

The layer is integrated into the current **Mission route** screen and its
existing **Local costmap A\*** strategy. It is not a separate application.
Run the server and open the normal URL:

```bash
python3 scripts/osm_semantic_preload.py --serve-only
```

Open <http://localhost:8000/>. Mission mode, local A*, and **Real DEM
(server)** are the defaults. The browser version has no synthetic terrain
option.

Without an explicit DEM path, the server uses the real worldwide
[AWS Open Data Terrain Tiles dataset](https://registry.opendata.aws/terrain-tiles/)
in [Terrarium encoding](https://github.com/tilezen/joerd/blob/master/docs/formats.md)
at zoom 15. Downloaded PNG tiles are cached in `data/dem_cache/terrarium/`.

The **Terrain elevation** panel controls preferred slope, impassable slope,
slope penalty, and heatmap visibility. The map uses teal for gentle terrain,
yellow below the preferred limit, orange for penalised terrain, and red for
blocked terrain. Hovering a heatmap cell shows its maximum sampled slope. The
expanded map legend displays the current percentage boundaries. The route summary reports the
number of blocked cells, the configured robot limits, and the maximum sampled
slope. It also reports the selected path's ascent, descent, mean absolute
slope, maximum slope, and elevation range so that DEM use is directly
verifiable from **Route details**.

Public semantic data is loaded before the terrain-aware A* runs. In Spain,
Catastro INSPIRE Buildings is authoritative for building footprints and OSM
adds water, vegetation, and non-duplicate buildings. The normal example loads
270 Catastro polygons. Its 236-point route is approximately 550.6 m long and
was independently checked to have no footprint within the configured 2 m
safety margin. Source counts and both validation layers are recorded in Route
details.

Any real georeferenced single-band elevation GeoTIFF can replace the default:

```bash
python3 -m pip install -r requirements-dem.txt
python3 scripts/osm_semantic_preload.py \
  --serve-only \
  --dem-geotiff /path/to/elevation.tif
```

Its embedded CRS is used for WGS84 conversion. Elevation values are assumed to
be metres. Requests outside its bounds or over NoData cells stop route
generation with a visible error.

## Using gazebo_terrain_generator data

Install the optional Terrain-RGB reader and keep the generator's raw `dem/`
working directory:

```bash
python3 -m pip install -r requirements-dem.txt

# Serve the existing web UI with this DEM available to the browser.
python3 scripts/osm_semantic_preload.py \
  --serve-only \
  --terrain-world /path/to/world_name

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

The requested padding must remain inside the downloaded DEM coverage. Global
Terrain Tiles are appropriate for broad hills and terrain exclusions, not
small furrows, ditches or wheel-scale roughness. A CNIG MDT02 GeoTIFF can be
passed through `--dem-geotiff` when higher-resolution Spanish terrain is
required.

The browser never silently substitutes a synthetic or flat grid when the
terrain server is missing, a requested tile lies outside the downloaded area,
or sampling fails; route generation stops with a visible error.

The current slope magnitude is direction-independent and therefore
conservative. A later edge-cost model can distinguish ascent, descent and
cross-slope using the robot heading, but it requires validated vehicle limits.

## Next integration boundary

The web parameters and slope overlay are now implemented. The resulting path
remains a global planning route; CropFollow can provide local crop-row steering
only after the global route has selected a traversable corridor. The next step
is persisting these parameters in scenario YAML. Before robot use, add
inflation for footprint/turning radius and a separate lateral-slope check.
