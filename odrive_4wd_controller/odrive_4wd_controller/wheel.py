"""Physical wheel abstraction."""

from __future__ import annotations

from dataclasses import dataclass

from .odrive_device import AxisTelemetry, ODriveDevice


@dataclass
class Wheel:
    name: str
    device: ODriveDevice
    axis_number: int
    direction: int
    radius_m: float
    gear_ratio: float = 1.0
    scale: float = 1.0
    command_turns_s: float = 0.0

    def __post_init__(self) -> None:
        if self.direction not in (-1, 1):
            raise ValueError(f"{self.name}: direction must be -1 or 1")
        if self.radius_m <= 0 or self.gear_ratio <= 0 or self.scale <= 0:
            raise ValueError(f"{self.name}: geometry and scale must be positive")

    def apply_limits(
        self,
        current_a: float,
        calibration_current_a: float,
        velocity_turns_s: float,
        acceleration: float,
    ) -> None:
        self.device.apply_axis_limits(
            self.axis_number,
            current_a=current_a,
            calibration_current_a=calibration_current_a,
            velocity_turns_s=velocity_turns_s,
            acceleration_turns_s2=acceleration,
        )

    def arm(self) -> None:
        self.device.arm_axis(self.axis_number)

    def set_velocity(self, forward_turns_s: float) -> None:
        self.command_turns_s = float(forward_turns_s)
        self.device.command_velocity(
            self.axis_number,
            self.command_turns_s * self.direction * self.scale / self.gear_ratio,
        )

    def stop(self) -> None:
        self.command_turns_s = 0.0
        self.device.command_velocity(self.axis_number, 0.0)

    def idle(self) -> None:
        self.command_turns_s = 0.0
        self.device.idle_axis(self.axis_number)

    def telemetry(self) -> AxisTelemetry:
        raw = self.device.read_axis(self.axis_number)
        return AxisTelemetry(
            state=raw.state,
            position_turns=raw.position_turns * self.direction / self.gear_ratio,
            velocity_turns_s=raw.velocity_turns_s * self.direction / self.gear_ratio,
            current_a=raw.current_a,
            motor_temperature_c=raw.motor_temperature_c,
            controller_temperature_c=raw.controller_temperature_c,
            calibrated=raw.calibrated,
            encoder_ready=raw.encoder_ready,
            errors=raw.errors,
        )
