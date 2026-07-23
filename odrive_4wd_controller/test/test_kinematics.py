import math

import pytest

from odrive_4wd_controller.kinematics import (
    SlewLimiter,
    differential_drive,
    four_wheel_setpoints,
    linear_mps_to_wheel_turns_s,
    wheel_turns_s_to_linear_mps,
)


def test_straight_and_rotation_kinematics():
    left, right = differential_drive(1.0, 0.0, 0.6)
    assert left == pytest.approx(1.0)
    assert right == pytest.approx(1.0)
    left, right = differential_drive(0.0, 1.0, 0.6)
    assert left == pytest.approx(-0.3)
    assert right == pytest.approx(0.3)


def test_odrive_units_are_turns_per_second():
    turns = linear_mps_to_wheel_turns_s(0.5, 0.1)
    assert turns == pytest.approx(0.5 / (2 * math.pi * 0.1))
    assert wheel_turns_s_to_linear_mps(turns, 0.1) == pytest.approx(0.5)


def test_wheel_saturation():
    values = four_wheel_setpoints(
        10.0,
        0.0,
        track_width_m=0.5,
        wheel_radius_m=0.1,
        max_wheel_turns_s=0.2,
    )
    assert all(abs(value) <= 0.2 for value in values.__dict__.values())


def test_invalid_nonfinite_command_rejected():
    with pytest.raises(ValueError):
        differential_drive(float("nan"), 0.0, 0.5)


def test_separate_acceleration_and_deceleration():
    limiter = SlewLimiter(acceleration=1.0, deceleration=2.0)
    assert limiter.step(1.0, 0.1) == pytest.approx(0.1)
    assert limiter.step(0.0, 0.1) == pytest.approx(0.0)


def test_reversal_must_reach_zero_before_changing_sign():
    limiter = SlewLimiter(acceleration=1.0, deceleration=0.5, value=0.1)
    assert limiter.step(-1.0, 0.1) == pytest.approx(0.05)
    assert limiter.step(-1.0, 0.1) == pytest.approx(0.0)
    assert limiter.step(-1.0, 0.1) == pytest.approx(-0.1)
