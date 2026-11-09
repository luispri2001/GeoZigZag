# Obstacle count and route optimality

## Current implementation

Mission routing can avoid mapped buildings and water and assign additional
cost to vegetation and steep terrain. The local A* search is performed on a
discrete, weighted grid. The robot morphology selector and its checked
headland turns apply to Coverage; they are not used by Mission routing.
Coverage currently plans rows inside a field polygon and does not accept
interior obstacle polygons.

There is no obstacle-count threshold in the implementation. Count alone cannot
determine whether a route is optimal: obstacle size, shape, position, spacing,
the start and goal, grid resolution, safety margin and terrain/semantic costs
all change the feasible paths and their costs. A* optimality is relative to its
grid and cost function; a route may be optimal on that graph while differing
from a continuous-world shortest path.

## Reproducible evaluation protocol

To study how obstacle density affects route quality, keep the field, start and
goal, grid resolution, safety margin, obstacle dimensions and cost settings
fixed. Generate multiple obstacle placements per count using recorded random
seeds, and sweep counts from an obstacle-free case through cases where no route
exists. Record the obstacle polygons and planner configuration with each run.

Report at least:

- route-found rate and planning time;
- path length and detour ratio relative to the obstacle-free route;
- grid cells expanded and blocked;
- route-cost gap against a reference solution.

For a graph-optimality check, compare A* with Dijkstra on the same weighted
grid. To claim a gap against continuous geometry, define a separate continuous
reference or report results at multiple grid resolutions. The results should
be plotted against obstacle count and placement variability; a single count
cannot be presented as a general point where routes stop being optimal.

This evaluation has not been implemented or run. Until it is, the repository
does not support a numeric obstacle threshold or obstacle-aware zigzag rows.
