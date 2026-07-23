from dataclasses import dataclass

import pytest

from odrive_4wd_controller.drivetrain import (
    DriveLimits,
    Drivetrain,
    TwoWheelBenchDrive,
)
from odrive_4wd_controller.odrive_device import AxisTelemetry
from odrive_4wd_controller.safety import DriveState


@dataclass
class FakeWheel:
    name: str
    device: object | None = None
    command: float = 0.0
    armed: bool = False
    idle_calls: int = 0

    def telemetry(self):
        return AxisTelemetry(
            state=8 if self.armed else 1,
            position_turns=0.0,
            velocity_turns_s=self.command,
            current_a=0.1,
            motor_temperature_c=None,
            controller_temperature_c=None,
            calibrated=True,
            encoder_ready=True,
            errors={"axis": 0, "motor": 0, "encoder": 0, "controller": 0},
        )

    def apply_limits(self, *_args, **_kwargs):
        pass

    def arm(self):
        self.armed = True

    def set_velocity(self, value):
        self.command = value

    def stop(self):
        self.command = 0.0

    def idle(self):
        self.command = 0.0
        self.armed = False
        self.idle_calls += 1


@dataclass
class FakeBusDevice:
    voltage: float
    serial: str = "TEST"

    def bus_voltage(self):
        return self.voltage


def make_drive():
    names = ("front_left", "rear_left", "front_right", "rear_right")
    wheels = {name: FakeWheel(name) for name in names}
    limits = make_limits()
    return Drivetrain(
        wheels,
        wheel_radius_m=0.1,
        track_width_m=0.5,
        limits=limits,
        max_side_difference_turns_s=0.1,
    )


def make_limits(**overrides):
    values = {
        "max_linear_mps": 0.2,
        "max_angular_rad_s": 0.5,
        "max_wheel_turns_s": 0.2,
        "hardware_velocity_turns_s": 0.2,
        "acceleration_turns_s2": 0.1,
        "deceleration_turns_s2": 0.2,
        "motor_current_a": 1.0,
        "calibration_current_a": 1.5,
        "command_timeout_s": 0.3,
        "enable_command_grace_s": 3.0,
        "idle_after_timeout_s": 0.5,
        "max_motion_duration_s": 3.0,
        "minimum_bus_voltage_v": 8.0,
        "maximum_bus_voltage_v": 59.92,
        "bus_monitor_period_s": 0.1,
    }
    values.update(overrides)
    return DriveLimits(**values)


def test_explicit_enable_and_directional_commands():
    drive = make_drive()
    drive.initialize()
    assert drive.safety.state == DriveState.READY
    drive.enable()
    drive.set_command(0.05, 0.0, now=1.0)
    drive.last_step_time = 1.0
    drive.step(now=1.1)
    assert all(w.command > 0 for w in drive.wheels.values())
    drive.safe_shutdown()
    assert all(w.command == 0 for w in drive.wheels.values())


def test_stale_command_decelerates_and_idles():
    drive = make_drive()
    drive.initialize()
    drive.enable()
    drive.set_command(0.05, 0.0, now=1.0)
    drive.last_step_time = 1.0
    drive.step(now=1.1)
    drive.step(now=1.31)
    drive.step(now=2.0)
    assert drive.safety.state == DriveState.READY
    assert all(w.command == 0 for w in drive.wheels.values())


def test_invalid_command_faults_and_idles():
    drive = make_drive()
    drive.initialize()
    drive.enable()
    with pytest.raises(ValueError):
        drive.set_command(float("nan"), 0.0)
    assert drive.safety.state == DriveState.FAULT
    assert all(w.command == 0 for w in drive.wheels.values())


def test_two_wheel_bench_accepts_linear_and_ignores_angular():
    wheels = {
        name: FakeWheel(name) for name in ("front", "rear")
    }
    limits = make_limits(max_linear_mps=0.08, max_wheel_turns_s=0.15)
    drive = TwoWheelBenchDrive(wheels, wheel_radius_m=0.08255, limits=limits)
    drive.initialize()
    drive.enable()
    drive.set_command(0.01, 0.0, now=1.0)
    drive.last_step_time = 1.0
    drive.step(now=1.1)
    assert all(wheel.command > 0 for wheel in wheels.values())
    drive.set_command(0.0, 0.1, now=1.2)
    assert drive.ignored_angular_rad_s == pytest.approx(0.1)
    drive.safe_shutdown()
    assert all(wheel.command == 0 for wheel in wheels.values())


def test_two_wheel_enable_grace_forces_old_target_to_zero():
    wheels = {
        name: FakeWheel(name) for name in ("front", "rear")
    }
    limits = make_limits()
    drive = TwoWheelBenchDrive(wheels, wheel_radius_m=0.08255, limits=limits)
    drive.initialize()
    drive.target_turns_s = 0.05
    drive.enable()
    start = drive.last_step_time
    drive.step(now=start + 1.0)
    assert drive.safety.state == DriveState.ENABLED
    assert all(wheel.command == 0.0 for wheel in wheels.values())


def test_two_wheel_maximum_motion_window_latches_stop():
    wheels = {name: FakeWheel(name) for name in ("front", "rear")}
    limits = make_limits(max_motion_duration_s=0.5)
    drive = TwoWheelBenchDrive(wheels, wheel_radius_m=0.085, limits=limits)
    drive.initialize()
    drive.enable()
    drive.set_command(0.05, 0.0, now=1.0)
    drive.last_step_time = 1.0
    drive.step(now=1.1)
    drive.set_command(0.05, 0.0, now=1.4)
    drive.step(now=1.51)
    assert drive.motion_limit_reached is True
    drive.set_command(0.05, 0.0, now=1.52)
    assert drive.target_turns_s == 0.0


def test_out_of_range_bus_blocks_initialization_and_idles_both():
    device = FakeBusDevice(60.0)
    wheels = {
        name: FakeWheel(name, device=device) for name in ("front", "rear")
    }
    drive = TwoWheelBenchDrive(
        wheels, wheel_radius_m=0.085, limits=make_limits()
    )
    with pytest.raises(RuntimeError, match="DC bus"):
        drive.initialize()
    assert drive.safety.state == DriveState.FAULT
    assert all(not wheel.armed for wheel in wheels.values())
