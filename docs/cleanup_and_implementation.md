# Repository inspection and implementation record

Inspected upstream commit: `cea4ece6fe41413b77cfe81362d67ce69c4cf9b2`.
This task concerns GeoZigZag cleanup and robot headland planning. The broader
Geo2Gazebo/Wildboar/CropFollow integration remains separate; its existing audit
is preserved. Work is on `main`, following Luis's latest instruction.

## Findings and architecture

The Python package already separates geometry, coverage, mission routing,
exports, evaluation and geospatial adapters. `planning.py` is a compatibility
facade used by downstream callers. README, Makefile and tests document the
actual supported commands. No ROS/Gazebo controller is implemented here.

The principal maintenance issues were the 4,027-line web HTML containing styles
and JavaScript, duplicated browser coverage geometry, and the implementation of
the HTTP service living in a historically named script. The root `index.html`
is an intentional redirect, not a duplicate application.

Implementation sequence:

1. Move styles and browser logic into `web/static/`; move the server into
   `geozigzag/web_server.py` and retain both historical script entry points.
2. Add focused robot parameters, headland connections and polygon coverage
   modules. Add the CLI and fixed field fixture before connecting the web.
3. Route web coverage through the Python API; remove the unused browser sweep
   planner; add robot controls, coloured turns and rotation-preserving exports.
4. Add unit and HTTP/CLI integration checks and document the consumer contract.

New modules: `robot.py`, `headland.py`, `robot_coverage.py`, `coverage_cli.py`.
Changed modules: export and HTTP handler. Web assets: `studio.css`, `studio.js`,
`robot-coverage.js`. Added demo config, robot-headland documentation and tests.
Mission routing and historical evaluation APIs remain available.

## Cleanup boundary

Removed the duplicate browser sweep planner and its unused start-target helper.
Separated markup, presentation, browser interaction and server responsibilities.
Preserved manuscript sources, versioned paper tables/figures/PDF, data fixtures,
public adapter entry points and original planning APIs: these have documented
reproduction or compatibility purposes. No user data or research artifacts were
deleted. Downloaded caches, environments and generated runs remain ignored.

## Setup and verification commands

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
make test
make test-integration
make coverage-demo
make serve
```

Browser checks: load fixed demo, generate Ackermann coverage, switch to
Differential and verify stale exports are disabled, regenerate, reduce Ackermann
headland to 0.1 m and verify the error, then restore the demo. Existing mission
settings and map controls are retained. Tiles/Leaflet require internet; the
coverage API and numerical tests do not download geospatial data.
