"""Forward bounded-curvature connections between planar poses.

Six Dubins words, evaluated analytically in a normalized start/goal frame.
Reference: LaValle, Planning Algorithms, section 15.3.1.
No reverse motion, steering-rate limits or vehicle dynamics are modelled.
"""

import math

from .geometry import normalize_angle

TAU = 2 * math.pi


def dubins_words(start, goal, radius):
    """Yield (word, normalized segment lengths) for every feasible word."""
    dx, dy = goal[0] - start[0], goal[1] - start[1]
    d = math.hypot(dx, dy) / radius
    theta = math.atan2(dy, dx)
    a, b = (start[2] - theta) % TAU, (goal[2] - theta) % TAU
    sa, sb, ca, cb = math.sin(a), math.sin(b), math.cos(a), math.cos(b)
    cab = math.cos(a - b)
    p2 = 2 + d * d - 2 * cab + 2 * d * (sa - sb)
    if p2 >= -1e-10:
        t = math.atan2(cb - ca, d + sa - sb)
        yield "LSL", ((t - a) % TAU, math.sqrt(max(0, p2)), (b - t) % TAU)
    p2 = 2 + d * d - 2 * cab + 2 * d * (sb - sa)
    if p2 >= -1e-10:
        t = math.atan2(ca - cb, d - sa + sb)
        yield "RSR", ((a - t) % TAU, math.sqrt(max(0, p2)), (t - b) % TAU)
    p2 = -2 + d * d + 2 * cab + 2 * d * (sa + sb)
    if p2 >= -1e-10:
        p = math.sqrt(max(0, p2))
        t = math.atan2(-ca - cb, d + sa + sb) - math.atan2(-2, p)
        yield "LSR", ((t - a) % TAU, p, (t - b) % TAU)
    p2 = d * d - 2 + 2 * cab - 2 * d * (sa + sb)
    if p2 >= -1e-10:
        p = math.sqrt(max(0, p2))
        t = math.atan2(ca + cb, d - sa - sb) - math.atan2(2, p)
        yield "RSL", ((a - t) % TAU, p, (b - t) % TAU)
    value = (6 - d * d + 2 * cab + 2 * d * (sa - sb)) / 8
    if abs(value) <= 1 + 1e-10:
        p = (TAU - math.acos(max(-1, min(1, value)))) % TAU
        t = (a - math.atan2(ca - cb, d - sa + sb) + p / 2) % TAU
        yield "RLR", (t, p, (a - b - t + p) % TAU)
    value = (6 - d * d + 2 * cab + 2 * d * (sb - sa)) / 8
    if abs(value) <= 1 + 1e-10:
        p = (TAU - math.acos(max(-1, min(1, value)))) % TAU
        t = (-a - math.atan2(ca - cb, d + sa - sb) + p / 2) % TAU
        yield "LRL", (t, p, (b - a - t + p) % TAU)


def sample_dubins(start, word, lengths, radius, spacing):
    """Integrate exact circular/straight primitives, including their boundaries."""
    if sum(lengths) * radius / spacing > 100_000:
        raise ValueError(
            "Turn exceeds sampling limit. Reduce field extent or robot radius."
        )
    poses = [tuple(start)]
    for mode, length in zip(word, lengths):
        x, y, yaw = poses[-1]
        steps = max(1, math.ceil(length * radius / spacing))
        for index in range(1, steps + 1):
            distance = length * radius * index / steps
            if mode == "S":
                pose = x + distance * math.cos(yaw), y + distance * math.sin(yaw), yaw
            else:
                curvature = (1 if mode == "L" else -1) / radius
                end_yaw = yaw + curvature * distance
                pose = (
                    x + (math.sin(end_yaw) - math.sin(yaw)) / curvature,
                    y + (-math.cos(end_yaw) + math.cos(yaw)) / curvature,
                    normalize_angle(end_yaw),
                )
            poses.append(pose)
    return poses


def pivot_connection(start, goal, spacing):
    """Stop, rotate to the connector, translate, stop and rotate to the row."""
    dx, dy = goal[0] - start[0], goal[1] - start[1]
    distance = math.hypot(dx, dy)
    if distance < 1e-9:
        return [start, goal]
    yaw = math.atan2(dy, dx)
    steps = max(1, math.ceil(distance / spacing))
    return [
        start,
        (start[0], start[1], yaw),
        *[
            (start[0] + dx * i / steps, start[1] + dy * i / steps, yaw)
            for i in range(1, steps + 1)
        ],
        goal,
    ]
