"""Encoder-based skid-steer odometry."""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean

from .kinematics import wheel_turns_s_to_linear_mps


@dataclass
class Pose2D:
    x: float = 0.0
    y: float = 0.0
    yaw: float = 0.0


def robust_side_average(values: list[float], max_difference: float) -> float:
    valid = [float(v) for v in values if math.isfinite(v)]
    if not valid:
        raise ValueError("no valid wheel measurement on side")
    if len(valid) == 2 and abs(valid[0] - valid[1]) > max_difference:
        raise ValueError("same-side wheel velocity disagreement")
    return mean(valid)


@dataclass
class SkidSteerOdometry:
    wheel_radius_m: float
    track_width_m: float
    max_side_difference_turns_s: float
    pose: Pose2D = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.pose = Pose2D()

    def reset(self) -> None:
        self.pose = Pose2D()

    def update(
        self,
        left_turns_s: list[float],
        right_turns_s: list[float],
        dt: float,
    ) -> tuple[Pose2D, float, float]:
        if dt <= 0:
            return self.pose, 0.0, 0.0
        left = robust_side_average(left_turns_s, self.max_side_difference_turns_s)
        right = robust_side_average(right_turns_s, self.max_side_difference_turns_s)
        v_left = wheel_turns_s_to_linear_mps(left, self.wheel_radius_m)
        v_right = wheel_turns_s_to_linear_mps(right, self.wheel_radius_m)
        linear = (v_right + v_left) * 0.5
        angular = (v_right - v_left) / self.track_width_m
        mid_yaw = self.pose.yaw + angular * dt * 0.5
        self.pose.x += linear * math.cos(mid_yaw) * dt
        self.pose.y += linear * math.sin(mid_yaw) * dt
        self.pose.yaw = math.atan2(
            math.sin(self.pose.yaw + angular * dt),
            math.cos(self.pose.yaw + angular * dt),
        )
        return self.pose, linear, angular
