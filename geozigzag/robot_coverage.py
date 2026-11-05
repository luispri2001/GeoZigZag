"""Shared CLI/web coverage planner with explicit robot headland connections."""

import math

from shapely.geometry import LineString, Polygon

from .geometry import ll_to_xy, local_origin, normalize_angle, xy_to_ll
from .headland import dubins_words, pivot_connection, sample_dubins
from .robot import RobotProfile

MAX_WAYPOINTS = 100_000


def generate_robot_coverage(
    vertices,
    row_spacing_m=0.75,
    point_spacing_m=1.0,
    row_direction_deg=None,
    start_corner="nw",
    robot=None,
):
    robot = robot or RobotProfile()
    for name, value in (
        ("Row spacing", row_spacing_m),
        ("Waypoint spacing", point_spacing_m),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0.1
        ):
            raise ValueError(f"{name} must be finite and at least 0.1 m.")
    if not 3 <= len(vertices) <= 500:
        raise ValueError("Supply 3–500 field vertices.")
    if any(
        len(p) != 2
        or not all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
            for v in p
        )
        or not -85 <= p[0] <= 85
        or not -180 <= p[1] <= 180
        for p in vertices
    ):
        raise ValueError("Invalid WGS84 latitude/longitude pair.")
    if start_corner not in {"nw", "ne", "sw", "se"}:
        raise ValueError("Start corner must be nw, ne, sw or se.")
    origin = local_origin(vertices)
    xy = [ll_to_xy(*p, *origin) for p in vertices]
    field = Polygon(xy)
    if (
        not field.is_valid
        or field.area < 1
        or max(field.bounds[2] - field.bounds[0], field.bounds[3] - field.bounds[1])
        > 5000
    ):
        raise ValueError(
            "Field must be a simple polygon of at least 1 m² and at most 5 km extent."
        )
    if row_direction_deg is None:
        a, b = max(zip(xy, xy[1:] + xy[:1]), key=lambda edge: math.dist(*edge))
        row_direction_deg = math.degrees(math.atan2(b[0] - a[0], b[1] - a[1]))
    if not isinstance(row_direction_deg, (int, float)) or not math.isfinite(
        row_direction_deg
    ):
        raise ValueError("Row direction must be finite.")
    angle = math.radians(row_direction_deg % 180)
    u, v = (math.sin(angle), math.cos(angle)), (math.cos(angle), -math.sin(angle))
    # Use a circumscribed disk about the planning reference point. For Ackermann
    # the reference is the rear axle; clearance must enclose the whole body there.
    allowed = field.buffer(-robot.clearance_m)
    work = field.buffer(-(robot.clearance_m + robot.headland_m))
    if work.is_empty or work.geom_type != "Polygon":
        raise ValueError(
            "Headland/clearance leaves no connected working area. Reduce them or enlarge the field."
        )
    projected = [p[0] * v[0] + p[1] * v[1] for p in work.exterior.coords]
    low, high = min(projected), max(projected)
    count = math.floor((high - low) / row_spacing_m) + 1
    if count > 2000:
        raise ValueError("Too many rows (maximum 2,000). Increase row spacing.")
    # Centre the sweep so the last gap is never a tiny extra row.
    offset = (low + high - (count - 1) * row_spacing_m) / 2
    rows = []
    extent = 10_000
    for i in range(count):
        s = offset + i * row_spacing_m
        line = LineString(
            [
                (s * v[0] - extent * u[0], s * v[1] - extent * u[1]),
                (s * v[0] + extent * u[0], s * v[1] + extent * u[1]),
            ]
        )
        clipped = work.intersection(line)
        parts = (
            [clipped]
            if clipped.geom_type == "LineString"
            else list(getattr(clipped, "geoms", []))
        )
        for part in parts:
            if part.geom_type == "LineString" and part.length > 0.1:
                ends = [tuple(part.coords[0]), tuple(part.coords[-1])]
                ends.sort(key=lambda p: p[0] * u[0] + p[1] * u[1])
                rows.append(ends)
    if not rows:
        raise ValueError("No usable crop rows remain.")
    minx, miny, maxx, maxy = field.bounds
    target = {
        "nw": (minx, maxy),
        "ne": (maxx, maxy),
        "sw": (minx, miny),
        "se": (maxx, miny),
    }[start_corner]
    orders = []
    for reverse_rows in (False, True):
        ordered = rows[::-1] if reverse_rows else rows
        for flip in (False, True):
            candidate = [
                row[::-1] if bool(i % 2) != flip else row
                for i, row in enumerate(ordered)
            ]
            orders.append(candidate)
    rows = min(orders, key=lambda candidate: math.dist(candidate[0][0], target))
    spacing = min(
        point_spacing_m,
        0.2,
        robot.minimum_radius_m * 0.05 if robot.minimum_radius_m else 0.2,
    )
    if robot.minimum_radius_m:
        # Bound arc-to-chord deviation so continuous-curve checks stay accurate
        # while still sampling larger turn radii more densely than the 0.2 m cap.
        max_arc_step = (
            robot.minimum_radius_m
            * 2
            * math.acos(
                1 - min(0.00025, robot.minimum_radius_m) / robot.minimum_radius_m
            )
        )
        spacing = min(spacing, max_arc_step)
    estimated = sum(math.dist(*r) for r in rows) / point_spacing_m
    if estimated > MAX_WAYPOINTS:
        raise ValueError("Route exceeds 100,000 waypoints. Increase waypoint spacing.")
    headland = allowed.difference(work.buffer(-0.001))
    poses, maneuvers = [], []
    route_length, row_length = 0.0, 0.0

    def append(new_poses, action):
        if len(poses) + len(new_poses) > MAX_WAYPOINTS:
            raise ValueError(
                "Route exceeds 100,000 waypoints. Adjust field or spacing."
            )
        for pose in new_poses:
            if (
                poses
                and math.dist(pose[:2], poses[-1][:2]) < 1e-8
                and abs(normalize_angle(pose[2] - poses[-1][2])) < 1e-8
            ):
                continue
            poses.append((*pose, action))

    for i, (a, b) in enumerate(rows):
        yaw = math.atan2(b[1] - a[1], b[0] - a[0])
        start = (*a, yaw)
        if poses:
            previous = poses[-1][:3]
            if robot.kind == "ackermann":
                candidates = sorted(
                    dubins_words(previous, start, robot.minimum_radius_m),
                    key=lambda item: sum(item[1]),
                )
                connection = None
                # Buffer sampled chords by their maximum arc sagitta to cover
                # the continuous circle, rather than checking only sample points.
                sagitta = (
                    robot.minimum_radius_m
                    * (1 - math.cos(spacing / (2 * robot.minimum_radius_m)))
                    + 1e-7
                )
                for word, lengths in candidates:
                    sampled = sample_dubins(
                        previous, word, lengths, robot.minimum_radius_m, spacing
                    )
                    if (
                        math.dist(sampled[-1][:2], start[:2]) > 1e-5
                        or abs(normalize_angle(sampled[-1][2] - yaw)) > 1e-6
                    ):
                        raise RuntimeError("Dubins endpoint verification failed.")
                    chord_path = LineString([p[:2] for p in sampled])
                    # At row endpoints the turn touches the working-area
                    # boundary by design. Buffering the chord path there makes
                    # every feasible U-turn appear to overlap the crop rows.
                    # Check the sampled centreline with a two-sagitta tolerance
                    # around that boundary, then check the entire chord envelope
                    # against the robot-clearance inset.
                    if headland.buffer(2 * sagitta + 1e-8).covers(
                        chord_path
                    ) and allowed.covers(chord_path.buffer(sagitta)):
                        connection = sampled
                        distance = sum(lengths) * robot.minimum_radius_m
                        break
            else:
                word = "pivot"
                connection = pivot_connection(previous, start, spacing)
                distance = math.dist(previous[:2], start[:2])
                if not headland.covers(LineString([p[:2] for p in connection])):
                    connection = None
            if connection is None:
                raise ValueError(
                    f"No checked {robot.kind} turn fits before row {i + 1}. Increase headland, change direction/spacing, or reduce robot radius. No route exported."
                )
            begin = len(poses) - 1
            append(connection, "headland")
            maneuvers.append(
                {
                    "from_index": begin,
                    "to_index": len(poses) - 1,
                    "word": word,
                    "length_m": distance,
                }
            )
            route_length += distance
        length = math.dist(a, b)
        steps = max(1, math.ceil(length / point_spacing_m))
        append(
            [
                (
                    a[0] + (b[0] - a[0]) * j / steps,
                    a[1] + (b[1] - a[1]) * j / steps,
                    yaw,
                )
                for j in range(steps + 1)
            ],
            "row",
        )
        row_length += length
    polyline_length = sum(
        math.dist(first[:2], second[:2]) for first, second in zip(poses, poses[1:])
    )
    ideal_route_length = route_length + row_length
    sampling_difference = ideal_route_length - polyline_length
    waypoints = []
    for i, (x, y, yaw, phase) in enumerate(poses):
        lat, lon = xy_to_ll(x, y, *origin)
        rotate = i > 0 and math.dist((x, y), poses[i - 1][:2]) < 1e-8
        waypoints.append(
            {
                "latitude": lat,
                "longitude": lon,
                "yaw": normalize_angle(yaw),
                "action": "rotate" if rotate else "drive",
                "phase": phase,
            }
        )
    metrics = {
        "area_m2": field.area,
        "working_area_m2": work.area,
        "coverage_rows": len(rows),
        "waypoints": len(waypoints),
        "total_route_length_m": ideal_route_length,
        "waypoint_polyline_length_m": polyline_length,
        "curve_sampling_difference_m": max(0.0, sampling_difference),
        "average_row_length_m": row_length / len(rows),
        "row_spacing_m": row_spacing_m,
        "waypoint_spacing_m": point_spacing_m,
        "turn_sample_spacing_m": spacing,
        "row_direction_deg": row_direction_deg % 180,
        "start_corner": start_corner,
    }

    def ll_line(points):
        return [list(xy_to_ll(*p[:2], *origin)) for p in points]

    return {
        "waypoints": waypoints,
        "rows": len(rows),
        "polygon": vertices,
        "row_paths": [ll_line(r) for r in rows],
        "headland_paths": [
            [
                [w["latitude"], w["longitude"]]
                for w in waypoints[m["from_index"] : m["to_index"] + 1]
            ]
            for m in maneuvers
        ],
        "working_polygon": ll_line(work.exterior.coords),
        "maneuvers": maneuvers,
        "metrics": metrics,
        "robot": robot.metadata(),
        "origin_wgs84": origin,
        "distance_m": metrics["total_route_length_m"],
        "message": f"Generated {len(rows)} rows with {robot.kind} headland turns.",
        "details": [
            f"Strategy: {robot.metadata()['strategy']}",
            f"Minimum radius: {robot.minimum_radius_m:.2f} m",
            f"Headland inset: {robot.headland_m:.2f} m; body clearance: {robot.clearance_m:.2f} m",
            "Geometric plan only. Rotate actions require a stop-and-rotate consumer."
            if robot.kind != "ackermann"
            else "Forward-only Dubins. Steering rate and dynamics are not modelled.",
        ],
    }


def coverage_request(payload):
    if not isinstance(payload, dict):
        raise ValueError("Expected a JSON object.")
    allowed = {
        "vertices",
        "row_spacing_m",
        "point_spacing_m",
        "row_direction_deg",
        "start_corner",
        "robot",
    }
    if set(payload) - allowed or "vertices" not in payload:
        raise ValueError("Supply vertices and supported coverage parameters only.")
    values = dict(payload)
    values["robot"] = RobotProfile(**values.get("robot", {}))
    return generate_robot_coverage(**values)
