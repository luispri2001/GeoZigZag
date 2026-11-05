# Application architecture

```text
Browser: field / robot / mission targets / semantic zones
    |
    +-- Coverage --> POST /api/coverage
    |                  robot_coverage.py: polygon inset + clipped rows
    |                  robot.py: vehicle parameters
    |                  headland.py: pivot or bounded-radius Dubins turns
    |
    +-- Mission --> browser direct / semantic A* / optional public OSRM
                       |
                       +-- GET /api/osm/semantic: bounded cached OSM polygons
                       +-- GET /api/catastro/buildings: Spanish Cadastre
                       +-- GET /api/dem/status and /api/dem/grid: elevation
    |
    +-- Preview and CSV / YAML / Plan JSON download
```

`python -m geozigzag` starts `web_server.py`. The root HTML redirects to the
single application page, `web/index.html`. Presentation and interaction live in
`web/static/`. Coverage UI requests use the same Python planner as
`coverage_cli.py`; mission routing runs in `studio.js`. There is no second
Python implementation of browser mission routing.

`catastro.py` handles WFS/GML and ETRS89/UTM queries. `elevation.py` reads real
Terrarium, optional GeoTIFF and raw Terrain-RGB data. `web_server.py` bounds
public queries, parses/clips semantic polygons and manages on-demand caches.
No fixed-area preload step is needed to start the application.

## Coordinates and route contract

Geographic inputs use `(latitude, longitude)` in WGS84 degrees. Field-scale
calculations use the local equirectangular ENU approximation in `geometry.py`:
x east, y north, yaw counterclockwise from east. A planar quaternion is
`(0, 0, sin(yaw/2), cos(yaw/2))`. GeoJSON uses longitude/latitude ordering.

Coverage reserves a headland and disk footprint clearance, clips parallel rows
and checks every connection. See [headland planning](robot_headlands.md) for
reference points, continuous-arc checks and the stationary rotation contract.
Exports prepare routes; they do not implement tracking, localization, TF,
robot execution or dynamic feasibility.

## Mission semantics

Buildings/water are hard obstacles for local A*. Forest/scrub increase grid
cost. DEM adds slope costs and blocks cells above the configured threshold.
Catastro takes precedence over duplicate OSM buildings in Spain. Vegetation
polygons can create resource targets. A final route intersection check enforces
the configured mapped-obstacle margin. OSRM Balanced/Strict can validate
buildings/water, but the public OSRM service does not receive custom vegetation
or terrain costs. Direct interpolation does not apply those layers.

Mission A* is a discrete geometric planner; the Coverage robot selector does
not make mission paths satisfy a steering radius. Satellite imagery is context,
not automatic obstacle detection. The app is intended for local use and has no
hosted authentication or multi-user service layer.
