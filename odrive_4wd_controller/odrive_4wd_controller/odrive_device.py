"""Legacy dual-axis ODrive access bound permanently to a serial number."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

AXIS_STATE_IDLE = 1
AXIS_STATE_CLOSED_LOOP_CONTROL = 8
CONTROL_MODE_VELOCITY = 2
INPUT_MODE_VEL_RAMP = 2


class ODriveCommunicationError(RuntimeError):
    pass


@dataclass(frozen=True)
class AxisTelemetry:
    state: int
    position_turns: float
    velocity_turns_s: float
    current_a: float
    motor_temperature_c: float | None
    controller_temperature_c: float | None
    calibrated: bool
    encoder_ready: bool
    errors: dict[str, int]

    @property
    def healthy(self) -> bool:
        return self.calibrated and self.encoder_ready and not any(self.errors.values())


class ODriveDevice:
    """A single ODrive connection; discovery order is never used as identity."""

    def __init__(
        self,
        serial: str,
        *,
        expected_hardware: str = "3.6",
        expected_firmware: str = "0.5.1",
        communication_timeout_s: float = 0.25,
    ):
        if not serial or serial.startswith("REQUIRED_"):
            raise ValueError("a real ODrive serial is required")
        self.serial = serial.upper()
        self.expected_hardware = expected_hardware
        self.expected_firmware = expected_firmware
        self.communication_timeout_s = communication_timeout_s
        self.device: Any | None = None
        self.last_communication_time: float | None = None

    def connect(self, timeout: float = 10.0) -> None:
        import odrive

        device = odrive.find_sync(
            serial_number=self.serial,
            timeout=timeout,
            interfaces=["usb"],
        )
        if device is None:
            raise ODriveCommunicationError(f"ODrive {self.serial} not found")
        actual = f"{int(device.serial_number):012X}".upper()
        if actual != self.serial:
            raise ODriveCommunicationError(
                f"serial mismatch: requested {self.serial}, received {actual}"
            )
        hardware = f"{int(device.hw_version_major)}.{int(device.hw_version_minor)}"
        firmware = (
            f"{int(device.fw_version_major)}.{int(device.fw_version_minor)}."
            f"{int(device.fw_version_revision)}"
        )
        if hardware != self.expected_hardware:
            raise ODriveCommunicationError(
                f"{self.serial}: hardware {hardware}, expected {self.expected_hardware}"
            )
        if firmware != self.expected_firmware:
            raise ODriveCommunicationError(
                f"{self.serial}: firmware {firmware}, expected {self.expected_firmware}"
            )
        self.device = device
        self.last_communication_time = time.monotonic()

    @property
    def connected(self) -> bool:
        return self.device is not None

    def axis(self, axis_number: int) -> Any:
        if self.device is None:
            raise ODriveCommunicationError(f"ODrive {self.serial} is disconnected")
        if axis_number not in (0, 1):
            raise ValueError("axis must be 0 or 1")
        return getattr(self.device, f"axis{axis_number}")

    @staticmethod
    def _optional_float(root: Any, path: str) -> float | None:
        value = root
        try:
            for component in path.split("."):
                value = getattr(value, component)
            result = float(value)
            return result if math.isfinite(result) else None
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return None

    def read_axis(self, axis_number: int) -> AxisTelemetry:
        try:
            axis = self.axis(axis_number)
            result = AxisTelemetry(
                state=int(axis.current_state),
                position_turns=float(axis.encoder.pos_estimate),
                velocity_turns_s=float(axis.encoder.vel_estimate),
                current_a=float(axis.motor.current_control.Iq_measured),
                motor_temperature_c=self._optional_float(
                    axis, "motor.motor_thermistor.temperature"
                ),
                controller_temperature_c=self._optional_float(
                    axis, "motor.fet_thermistor.temperature"
                ),
                calibrated=bool(axis.motor.is_calibrated),
                encoder_ready=bool(axis.encoder.is_ready),
                errors={
                    "axis": int(axis.error),
                    "motor": int(axis.motor.error),
                    "encoder": int(axis.encoder.error),
                    "controller": int(axis.controller.error),
                },
            )
            self.last_communication_time = time.monotonic()
            return result
        except Exception as exc:
            self.device = None
            raise ODriveCommunicationError(
                f"{self.serial}/axis{axis_number} telemetry failed: {exc}"
            ) from exc

    def bus_voltage(self) -> float:
        try:
            if self.device is None:
                raise ODriveCommunicationError(f"ODrive {self.serial} is disconnected")
            value = float(self.device.vbus_voltage)
            self.last_communication_time = time.monotonic()
            return value
        except Exception as exc:
            self.device = None
            raise ODriveCommunicationError(f"{self.serial} bus read failed: {exc}") from exc

    def apply_axis_limits(
        self,
        axis_number: int,
        *,
        current_a: float,
        velocity_turns_s: float,
        acceleration_turns_s2: float,
    ) -> None:
        if current_a <= 0 or velocity_turns_s <= 0 or acceleration_turns_s2 <= 0:
            raise ValueError("hardware limits must be positive")
        try:
            axis = self.axis(axis_number)
            axis.motor.config.current_lim = current_a
            axis.controller.config.vel_limit = velocity_turns_s
            axis.controller.config.vel_ramp_rate = acceleration_turns_s2
            axis.controller.config.control_mode = CONTROL_MODE_VELOCITY
            axis.controller.config.input_mode = INPUT_MODE_VEL_RAMP
            axis.controller.input_vel = 0.0
            self.last_communication_time = time.monotonic()
        except Exception as exc:
            self.device = None
            raise ODriveCommunicationError(
                f"{self.serial}/axis{axis_number} limit update failed: {exc}"
            ) from exc

    def arm_axis(self, axis_number: int, timeout_s: float = 2.0) -> None:
        axis = self.axis(axis_number)
        status = self.read_axis(axis_number)
        if not status.healthy:
            raise RuntimeError(f"{self.serial}/axis{axis_number} is not healthy")
        axis.controller.input_vel = 0.0
        axis.requested_state = AXIS_STATE_CLOSED_LOOP_CONTROL
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if int(axis.current_state) == AXIS_STATE_CLOSED_LOOP_CONTROL:
                self.last_communication_time = time.monotonic()
                return
            if int(axis.error) != 0:
                break
            time.sleep(0.02)
        self.idle_axis(axis_number)
        raise RuntimeError(f"{self.serial}/axis{axis_number} failed to enter closed loop")

    def command_velocity(self, axis_number: int, turns_s: float) -> None:
        if not math.isfinite(turns_s):
            raise ValueError("velocity command must be finite")
        try:
            self.axis(axis_number).controller.input_vel = float(turns_s)
            self.last_communication_time = time.monotonic()
        except Exception as exc:
            self.device = None
            raise ODriveCommunicationError(
                f"{self.serial}/axis{axis_number} command failed: {exc}"
            ) from exc

    def idle_axis(self, axis_number: int) -> None:
        try:
            axis = self.axis(axis_number)
            axis.controller.input_vel = 0.0
            axis.requested_state = AXIS_STATE_IDLE
            self.last_communication_time = time.monotonic()
        except Exception as exc:
            self.device = None
            raise ODriveCommunicationError(
                f"{self.serial}/axis{axis_number} idle failed: {exc}"
            ) from exc

    def safe_idle_all(self) -> None:
        for axis_number in (0, 1):
            try:
                self.idle_axis(axis_number)
            except Exception:
                pass

    def clear_errors_once(self) -> None:
        """Explicit operator action only; never called automatically."""
        if self.device is None:
            raise ODriveCommunicationError(f"ODrive {self.serial} is disconnected")
        for axis_number in (0, 1):
            self.axis(axis_number).clear_errors()
