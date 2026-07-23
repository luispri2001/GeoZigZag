"""Differential/skid-steer kinematics and ODrive unit conversions."""

from __future__ import annotations

import math
from dataclasses import dataclass


def clamp(value: float, lower: float, upper: float) -> float:
    if not math.isfinite(value):
        raise ValueError("command must be finite")
    return max(lower, min(upper, value))


def differential_drive(v_mps: float, w_rad_s: float, track_width_m: float) -> tuple[float, float]:
    if not all(math.isfinite(x) for x in (v_mps, w_rad_s, track_width_m)):
        raise ValueError("kinematic inputs must be finite")
    if track_width_m <= 0:
        raise ValueError("track_width_m must be positive")
    half = track_width_m * 0.5
    return v_mps - w_rad_s * half, v_mps + w_rad_s * half


def linear_mps_to_wheel_turns_s(linear_mps: float, radius_m: float) -> float:
    if radius_m <= 0 or not math.isfinite(radius_m):
        raise ValueError("wheel radius must be finite and positive")
    return linear_mps / (2.0 * math.pi * radius_m)


def wheel_turns_s_to_linear_mps(turns_s: float, radius_m: float) -> float:
    if radius_m <= 0 or not math.isfinite(radius_m):
        raise ValueError("wheel radius must be finite and positive")
    return turns_s * 2.0 * math.pi * radius_m


def wheel_rpm(linear_mps: float, radius_m: float) -> float:
    return linear_mps_to_wheel_turns_s(linear_mps, radius_m) * 60.0


def motor_rpm(linear_mps: float, radius_m: float, gear_ratio: float) -> float:
    if gear_ratio <= 0:
        raise ValueError("gear ratio must be positive")
    return wheel_rpm(linear_mps, radius_m) * gear_ratio


@dataclass
class SlewLimiter:
    acceleration: float
    deceleration: float
    value: float = 0.0

    def step(self, target: float, dt: float) -> float:
        if dt <= 0 or not math.isfinite(dt):
            return self.value
        target = float(target)
        reversing = target * self.value < 0
        if reversing:
            # A reversal is always split into two phases: decelerate exactly to
            # zero, then accelerate in the other direction on a later tick.
            target = 0.0
        slowing = abs(target) < abs(self.value)
        rate = self.deceleration if slowing else self.acceleration
        maximum_change = rate * dt
        change = clamp(target - self.value, -maximum_change, maximum_change)
        self.value += change
        if abs(self.value) < 1e-12:
            self.value = 0.0
        return self.value


@dataclass(frozen=True)
class WheelSetpoints:
    front_left: float
    rear_left: float
    front_right: float
    rear_right: float


def four_wheel_setpoints(
    v_mps: float,
    w_rad_s: float,
    *,
    track_width_m: float,
    wheel_radius_m: float,
    max_wheel_turns_s: float,
    left_scale: float = 1.0,
    right_scale: float = 1.0,
    front_left_scale: float = 1.0,
    rear_left_scale: float = 1.0,
    front_right_scale: float = 1.0,
    rear_right_scale: float = 1.0,
) -> WheelSetpoints:
    left_mps, right_mps = differential_drive(v_mps, w_rad_s, track_width_m)
    left = linear_mps_to_wheel_turns_s(left_mps, wheel_radius_m) * left_scale
    right = linear_mps_to_wheel_turns_s(right_mps, wheel_radius_m) * right_scale
    values = (
        clamp(left * front_left_scale, -max_wheel_turns_s, max_wheel_turns_s),
        clamp(left * rear_left_scale, -max_wheel_turns_s, max_wheel_turns_s),
        clamp(right * front_right_scale, -max_wheel_turns_s, max_wheel_turns_s),
        clamp(right * rear_right_scale, -max_wheel_turns_s, max_wheel_turns_s),
    )
    return WheelSetpoints(*values)
