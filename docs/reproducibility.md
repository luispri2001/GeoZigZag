# Reproducibility protocol

## Single command

```bash
make reproduce
```

The command performs three stages:

1. run all offline unit and contract tests;
2. execute `configs/evaluation.yaml` and generate route/metric/figure artifacts;
3. compile `paper/main.tex` into `paper/build/main.pdf`.

## Controlled inputs

- deterministic seed: `20260703`;
- three coverage scenarios;
- three semantic target orders and forbidden-zone layouts;
- five repetitions per primary planner/scenario combination;
- 4--5 m semantic-grid resolution;
- versioned cached OSRM driving responses from 2026-07-03;
- a 4-by-3 row/waypoint spacing sensitivity sweep.

The SHA-256 digest of the evaluation configuration is stored in
`summary.json`. Online tiles and routing services are excluded from the
reproduction path.

## Metrics

- `waypoints`: number of exported samples;
- `distance_m`: sum of local planar segment lengths;
- `rows`: number of clipped coverage intervals;
- `turns`: heading changes greater than or equal to 30 degrees;
- `computation_time_ms`: median wall-clock planner time, excluding export and plotting;
- `forbidden_zone_intersections`: route segments touching or crossing at least one zone;
- `max_snap_distance_m`: maximum OSRM input-to-network snap distance;
- `success_rate`: successful repetitions divided by configured repetitions;
- `area_m2` and `average_row_length_m`: coverage geometry descriptors.

Timing is machine-dependent. Route geometry and non-timing metrics are the
primary deterministic results.

## Offline and external tests

All default tests are offline:

```bash
python3 -m unittest discover -s tests -p "test_*.py"
```

Live OSRM and browser tile checks are optional external-service tests and are
not required to reproduce the paper.
