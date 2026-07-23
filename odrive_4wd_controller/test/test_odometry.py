import math

import pytest

from odrive_4wd_controller.odometry import SkidSteerOdometry, robust_side_average


def test_straight_odometry():
    odom = SkidSteerOdometry(0.1, 0.5, 0.2)
    pose, linear, angular = odom.update([1.0, 1.0], [1.0, 1.0], 1.0)
    assert pose.x == pytest.approx(2 * math.pi * 0.1)
    assert pose.y == pytest.approx(0.0)
    assert angular == pytest.approx(0.0)


def test_rotation_odometry():
    odom = SkidSteerOdometry(0.1, 0.5, 0.2)
    pose, linear, angular = odom.update([-0.1, -0.1], [0.1, 0.1], 1.0)
    assert linear == pytest.approx(0.0)
    assert angular > 0
    assert pose.yaw > 0


def test_side_disagreement_rejected():
    with pytest.raises(ValueError):
        robust_side_average([0.0, 1.0], 0.1)
