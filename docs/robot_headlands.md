# Robot-aware coverage and headland turns

## Quick start

From the repository root, with the virtual environment activated:

```bash
python -m geozigzag.coverage_cli
python -m geozigzag.coverage_cli --robot differential
python -m geozigzag
```

The CLI prints a newly created timestamped output directory. To name a run, add
`--out outputs/my-new-run`; an existing directory causes an error, preserving
its files. `--config configs/robot_coverage_demo.json` supplies the fixed demo.
The JSON schema is also the body accepted by `POST /api/coverage`.

Open `http://localhost:8000/?mode=coverage`. Select a field or press **Load
reproducible field demo**, choose the robot, and press **Generate route**.
Green lines are working rows, orange lines are headland connections, and the
dashed polygon bounds the working area. Every field/robot edit invalidates
all downloads until regeneration. CSV/YAML contain the executable action
sequence; Plan JSON includes the complete plan, request and metrics.

## Kinematics

| Profile | Headland strategy | Parameters |
| --- | --- | --- |
| Differential | Stop, rotate in place, translate to next row, stop and rotate | Body clearance radius, headland inset |
| Skid-steer / tracked | Same ideal stop-and-rotate geometry | Body clearance radius, headland inset |
| Ackermann | Forward-only circular arcs and straight segments, tangent at joins | Wheelbase, maximum steering angle, body clearance radius, headland inset |

For Ackermann, `R_min = wheelbase / tan(max_steering_angle)`. All six Dubins
words (LSL, LSR, RSL, RSR, RLR, LRL) are tested, in increasing length order.
The shortest candidate that passes the geometric containment checks is used.
This is a connection planner for a fixed row ordering, not a global coverage
optimizer. The implementation integrates exact circular primitives; tests
check endpoints, headings and curvature for all six words.

Reference: S. M. LaValle, *Planning Algorithms*,
[section 15.3.1](https://lavalle.pl/planning/node821.html).
The formulas are implemented locally; no upstream source code is copied.

## Geometry and clearance

Inputs are WGS84 `(latitude, longitude)`. Existing `geometry.py` converts them
to a field-scale local ENU approximation, with x east, y north, and yaw
counterclockwise from east. The planning reference is the rotation centre for
differential/skid-steer and the rear axle centre for Ackermann.

**Clearance radius must enclose the complete vehicle about that reference**,
including implements and a desired margin. It is not simply half the width
for a long vehicle. The planner uses a disk footprint, so body orientation does
not change that clearance. This is conservative for a properly enclosed body.

The field polygon is eroded by clearance to obtain the allowed reference-point
area, and by clearance + headland inset to obtain the working area. Parallel
sweep lines are clipped to that working polygon. Spacing is centred across the
sweep width so no tiny extra gap appears at its far edge. The chosen start
corner selects the closest of four alternating row orders.

Connections must stay in the remaining headland region. Dubins curves are
sampled at at most 0.2 m and 0.05 radians per arc step. Sampled chords are
buffered by an upper bound on arc sagitta, so containment covers the circular
arc between samples. A 1 mm geometric tolerance is allowed at the working
boundary to accommodate the connection endpoints. Straight differential
connections are checked as continuous segments, including in concave fields.
Shapely supplies polygon inset, clipping and containment instead of duplicating
fragile browser geometry.

If the working inset splits into disconnected polygons, or no candidate
connection fits, generation fails with actionable parameter guidance. It does
not silently connect through a concavity, leave the field or export a partial
route. A failure is not proof that no possible route exists: changing row
ordering or using reverse motion could admit other solutions.

## Exports and consumer contract

Each waypoint has WGS84 latitude/longitude, ENU yaw and a planar quaternion.
Robot-aware CSV/YAML/GeoJSON additionally retain `action` (`drive` or `rotate`)
and `phase` (`row` or `headland`). Legacy route exports retain their schema.

A `rotate` record intentionally repeats the preceding position with a new
yaw. **Consumers must stop at the preceding pose and execute the stationary
rotation before driving again**. Do not deduplicate these positions or feed
them to a follower that ignores orientation/actions. The exports do not
provide timing, velocities, accelerations, ROS frame transforms or a robot
controller. YAML uses the same nested `orientation` schema in web and CLI.

`plan.json` contains row paths, headland paths, maneuver index ranges and Dubins
words, robot parameters, local origin, and area/working area, row count,
waypoint count, route length, average row length, spacings, direction and start
corner. CLI also saves `request.json`; browser Plan JSON embeds its request.
Use separate directories for repeated experiments.

## Limits and checks

Skid-steer slip, soil deformation, dynamic stability and steering rate are not
modelled. Ackermann does not use reverse, Reeds–Shepp, skipped-row ordering or
continuous-curvature steering transitions. This mode checks the selected field
and crop working boundary; it does not fuse the mission router's buildings,
semantic zones or DEM. An inset leaves an unworked border, so row planning is
not a guarantee of complete area coverage. The fixed demo is a geometric
fixture, not a survey of actual crop/obstacle suitability at those coordinates.

Requests are limited to 500 vertices, 5 km field extent, 2,000 sweep lines,
100,000 waypoints, a 128 kB HTTP body and at least 0.1 m input spacing. The
HTTP service is for local use; it is not a production hosting/auth service.

```bash
python -m unittest discover -s tests
python -m unittest discover -s tests/integration
```

Unit checks exercise curvature/pose continuity, all robot profiles, containment,
headland exclusion, all start corners, arbitrary bearings, concave and
infeasible fields, invalid inputs and rotation-preserving exports. Integration
checks start an ephemeral local HTTP server, compare API and CLI results,
verify static assets and error responses, and ensure repeated CLI output paths
cannot overwrite data. No Gazebo, ROS or external map services are required.
