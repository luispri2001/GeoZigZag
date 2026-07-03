# GeoZigzag Studio

GeoZigzag Studio is a reproducible route-preparation workflow for agricultural
robotics. It combines:

- dense boustrophedon coverage inside a selected field; and
- sparse semantic georouting between ordered geographic targets.

The browser supports map-based editing, explicit route generation, manual
forbidden zones, direct/local-cost/OSRM routing, visible success and warning
states, and CSV/YAML export. The Python package provides the deterministic
implementation used by the tests, benchmark, figures, tables, and paper.

## Scope

This repository prepares and validates route files. It does not claim closed-
loop navigation, Gazebo completion, or physical robot validation. CSV/YAML
files are ROS-style interchange artifacts; a robot-side consumer must still
perform coordinate-frame conversion, localization, path tracking, and safety
checks.

See [the upstream integration audit](docs/upstream_integration_audit.md) for the
verified boundary with Geo2Gazebo, Wildboar, and Jabali CropFollow.

## Installation

```bash
git clone git@github.com:luispri2001/GeoZigZag.git
cd GeoZigZag
git switch sim-integration

python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

Python 3.10 or newer is recommended. The planner uses standard-library
geometry; PyYAML reads experiment files and Matplotlib generates publication
figures.

Compiling the manuscript also requires Make, `latexmk`, IEEEtran, TikZ, and the
usual LaTeX graphics packages. On Ubuntu 22.04/24.04:

```bash
sudo apt install make latexmk texlive-latex-extra texlive-pictures
```

## Web interface

Serve the repository:

```bash
python3 -m http.server 8000 --bind 127.0.0.1
```

Open <http://127.0.0.1:8000/>. Useful direct links are:

```text
http://127.0.0.1:8000/?mode=coverage
http://127.0.0.1:8000/?mode=mission&strategy=cost
http://127.0.0.1:8000/?mode=mission&strategy=osm&preset=balanced
```

Map tiles and live OSRM routing need internet access. Direct and local
cost-aware routing do not. Editing a field, target list, spacing, route mode, or
forbidden zone marks the existing route as pending and disables stale exports;
press **Generate route** to create a new result.

## Reproduce the paper results

```bash
make reproduce
```

This command runs the offline tests, evaluates the versioned scenarios,
generates route artifacts and figures, and compiles the IEEE manuscript. Main
outputs are:

```text
outputs/evaluation/results.csv
outputs/evaluation/sensitivity.csv
outputs/evaluation/summary.json
outputs/evaluation/paper_results.tex
outputs/evaluation/figures/*.png
outputs/evaluation/routes/<scenario>/<strategy>/
paper/build/main.pdf
```

The evaluation never calls a live routing service. It uses the OSRM response
fixture in `data/osrm_fixture.json`, including its retrieval date, source,
profile, geometry, and snap distances.

Run individual stages with:

```bash
make test
make evaluate
make paper

python3 -m geozigzag.evaluate \
  --config configs/evaluation.yaml \
  --out outputs/evaluation
```

## CLI demo

The original compact demo remains available:

```bash
python3 -m geozigzag.cli --out outputs/demo
```

It generates coverage, direct mission, and local cost-aware mission routes.

## Waypoint schema

CSV and YAML routes contain:

| Field | Definition |
| --- | --- |
| `latitude`, `longitude` | WGS84 coordinates in degrees |
| `yaw` | ENU heading in radians, counter-clockwise from east |
| `qx`, `qy`, `qz`, `qw` | Planar quaternion derived from yaw |

GeoJSON bundles contain one route `LineString` plus indexed waypoint features.

## Package layout

```text
geozigzag/
├── geometry.py       # local projection, heading, polygon operations
├── coverage.py       # rectangle and polygon boustrophedon planners
├── routing.py        # direct, semantic-cost A*, OSRM adapters
├── metrics.py        # distance, turns, area, forbidden intersections
├── export.py         # CSV, YAML, and GeoJSON
├── evaluation.py     # scenario execution, metrics, figures, tables
├── evaluate.py       # python -m entry point
├── planning.py       # compatibility facade
└── cli.py            # compact legacy demo
```

Additional documentation:

- [Architecture and coordinate conventions](docs/architecture.md)
- [Reproducibility protocol](docs/reproducibility.md)
- [Geo2Gazebo/Wildboar/CropFollow audit](docs/upstream_integration_audit.md)

## Scientific interpretation

The local semantic-cost grid is a controlled planning proxy, not a calibrated
terrain model. Its point labels influence nearby cells and declared forbidden
polygons are impassable. Grid paths contain discrete corners and do not enforce
vehicle footprint, turning radius, headland width, or dynamics. Cached OSRM
routes are included to measure network detours and snap distance, not to assert
that mapped roads are safe or reachable by an agricultural robot.

Please cite the paper in `paper/main.tex` when bibliographic details are final.
