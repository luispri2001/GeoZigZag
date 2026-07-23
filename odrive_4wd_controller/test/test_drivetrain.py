from dataclasses import dataclass

import pytest

from odrive_4wd_controller.drivetrain import DriveLimits, Drivetrain
from odrive_4wd_controller.odrive_device import AxisTelemetry
from odrive_4wd_controller.safety import DriveState


@dataclass
class FakeWheel:
    name: str
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


def make_drive():
    names = ("front_left", "rear_left", "front_right", "rear_right")
    wheels = {name: FakeWheel(name) for name in names}
    limits = DriveLimits(0.2, 0.5, 0.2, 0.1, 0.2, 2.0, 0.3, 0.5)
    return Drivetrain(
        wheels,
        wheel_radius_m=0.1,
        track_width_m=0.5,
        limits=limits,
        max_side_difference_turns_s=0.1,
    )


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
