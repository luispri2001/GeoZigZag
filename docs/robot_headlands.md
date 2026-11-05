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
sampled at at most 0.2 m, 0.05 radians per arc step, and 0.25 mm of arc-to-chord
deviation. Sampled chords are buffered by that deviation and checked against
the robot-clearance inset. A tolerance of at most 0.75 mm is allowed where a
turn meets a row endpoint. Straight differential connections are checked as
continuous segments, including in concave fields. Shapely supplies polygon
inset, clipping and containment instead of duplicating fragile browser
geometry.

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
waypoint count, average row length, spacings, direction and start corner.
`total_route_length_m` reports the analytic length of the continuous Dubins
arcs. `waypoint_polyline_length_m` reports the length of the sampled straight
segments in the exported coordinates, and `curve_sampling_difference_m`
reports the small difference between them. In the 120-case configuration
matrix, that difference stayed below 2 cm. CLI also saves `request.json`;
browser Plan JSON embeds its request. Use separate directories for repeated
experiments.

## Checked example routes

These field-scale examples were generated with the same planner as the web UI.
Wheelbase/steering determine Ackermann radius; field shape and row bearing
change the turn space available.

| Field | Robot | Row bearing | Headland | Min radius | Rows | Waypoints | Route |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 30 × 60 m rectangle | Differential | 0° | 2 m | — | 13 | 873 | 741.6 m |
| 30 × 60 m rectangle | Skid-steer | 90° | 3 m | — | 44 | 1,507 | 1,046.0 m |
| 30 × 60 m rectangle | Ackermann, 1 m wheelbase, 35° steering | 0° | 8 m | 1.43 m | 7 | 1,166 | 342.7 m |
| 24 × 38 m rectangle | Ackermann, 0.8 m wheelbase, 50° steering | 35° | 4 m | 0.67 m | 24 | 2,797 | 438.3 m |
| 82 × 54 m rectangle | Ackermann, 2.4 m wheelbase, 30° steering | 0° | 8 m | 4.16 m | — | — | Rejected: turn does not fit |
| 82 × 54 m rectangle | Same Ackermann robot | 0° | 12 m | 4.16 m | 23 | 7,312 | 1,258.4 m |
| 42 × 58 m trapezoid | Differential | 35° | 3 m | — | 43 | 1,936 | 1,354.6 m |

The 120-case sweep varied five rectangular/trapezoidal/concave fields, three
bearings and two row spacings across differential, skid-steer, and two
Ackermann sizes. It produced 54 routes and rejected 66 geometrically infeasible
combinations. Every accepted route passed the field-clearance, row-turn
separation, and distance-metric checks. The unit suite keeps representative
rectangle and trapezoid cases plus a large-radius Ackermann case whose
headland must grow from 8 m to 12 m.

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
