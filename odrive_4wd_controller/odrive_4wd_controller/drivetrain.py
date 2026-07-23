"""Four-wheel drivetrain control independent of ROS 2."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from .kinematics import SlewLimiter, WheelSetpoints, clamp, four_wheel_setpoints
from .safety import CommandWatchdog, DriveState, SafetyMachine
from .wheel import Wheel

WHEEL_NAMES = ("front_left", "rear_left", "front_right", "rear_right")


@dataclass(frozen=True)
class DriveLimits:
    max_linear_mps: float
    max_angular_rad_s: float
    max_wheel_turns_s: float
    acceleration_turns_s2: float
    deceleration_turns_s2: float
    motor_current_a: float
    command_timeout_s: float
    idle_after_timeout_s: float


class Drivetrain:
    def __init__(
        self,
        wheels: dict[str, Wheel],
        *,
        wheel_radius_m: float,
        track_width_m: float,
        limits: DriveLimits,
        scales: dict[str, float] | None = None,
        max_side_difference_turns_s: float = 0.08,
        mismatch_warning_s: float = 0.5,
        mismatch_fault_s: float = 1.5,
    ):
        if set(wheels) != set(WHEEL_NAMES):
            raise ValueError(f"four confirmed wheels are required: {WHEEL_NAMES}")
        self.wheels = wheels
        self.wheel_radius_m = wheel_radius_m
        self.track_width_m = track_width_m
        self.limits = limits
        self.scales = scales or {}
        self.max_side_difference = max_side_difference_turns_s
        self.mismatch_warning_s = mismatch_warning_s
        self.mismatch_fault_s = mismatch_fault_s
        self.safety = SafetyMachine(DriveState.IDLE)
        self.watchdog = CommandWatchdog(limits.command_timeout_s)
        self.limiters = {
            name: SlewLimiter(limits.acceleration_turns_s2, limits.deceleration_turns_s2)
            for name in WHEEL_NAMES
        }
        self.target = WheelSetpoints(0.0, 0.0, 0.0, 0.0)
        self.last_step_time: float | None = None
        self.timeout_started: float | None = None
        self.mismatch_started: dict[str, float | None] = {"left": None, "right": None}

    def initialize(self) -> None:
        self.safety.transition(DriveState.INITIALIZING)
        try:
            for wheel in self.wheels.values():
                status = wheel.telemetry()
                if not status.healthy:
                    raise RuntimeError(f"{wheel.name} is not calibrated/error-free")
                wheel.apply_limits(
                    self.limits.motor_current_a,
                    self.limits.max_wheel_turns_s,
                    self.limits.acceleration_turns_s2,
                )
                wheel.idle()
            self.safety.transition(DriveState.READY)
        except Exception as exc:
            self.safe_shutdown()
            self.safety.trip("INITIALIZATION_FAILED", str(exc), time.monotonic())
            raise

    def enable(self) -> None:
        if self.safety.state != DriveState.READY:
            raise RuntimeError(f"cannot enable from {self.safety.state}")
        armed: list[Wheel] = []
        try:
            for name in WHEEL_NAMES:
                wheel = self.wheels[name]
                wheel.arm()
                armed.append(wheel)
            self.safety.transition(DriveState.ENABLED)
            now = time.monotonic()
            self.watchdog.feed(now)
            self.last_step_time = now
        except Exception as exc:
            for wheel in armed:
                try:
                    wheel.idle()
                except Exception:
                    pass
            self.safety.trip("ENABLE_FAILED", str(exc), time.monotonic())
            raise

    def set_command(self, linear_mps: float, angular_rad_s: float, now: float | None = None) -> None:
        if self.safety.state != DriveState.ENABLED:
            raise RuntimeError("drivetrain is not enabled")
        if not math.isfinite(linear_mps) or not math.isfinite(angular_rad_s):
            self.fault("INVALID_COMMAND", "NaN or infinite command")
            raise ValueError("command must be finite")
        linear = clamp(
            linear_mps, -self.limits.max_linear_mps, self.limits.max_linear_mps
        )
        angular = clamp(
            angular_rad_s,
            -self.limits.max_angular_rad_s,
            self.limits.max_angular_rad_s,
        )
        self.target = four_wheel_setpoints(
            linear,
            angular,
            track_width_m=self.track_width_m,
            wheel_radius_m=self.wheel_radius_m,
            max_wheel_turns_s=self.limits.max_wheel_turns_s,
            left_scale=self.scales.get("left", 1.0),
            right_scale=self.scales.get("right", 1.0),
            front_left_scale=self.scales.get("front_left", 1.0),
            rear_left_scale=self.scales.get("rear_left", 1.0),
            front_right_scale=self.scales.get("front_right", 1.0),
            rear_right_scale=self.scales.get("rear_right", 1.0),
        )
        self.watchdog.feed(time.monotonic() if now is None else now)
        self.timeout_started = None

    def step(self, now: float | None = None) -> dict[str, object]:
        now = time.monotonic() if now is None else now
        dt = 0.0 if self.last_step_time is None else max(0.0, now - self.last_step_time)
        self.last_step_time = now
        if self.safety.state != DriveState.ENABLED:
            return self.telemetry()

        stale = self.watchdog.stale(now)
        if stale:
            self.target = WheelSetpoints(0.0, 0.0, 0.0, 0.0)
            if self.timeout_started is None:
                self.timeout_started = now

        try:
            for name in WHEEL_NAMES:
                target = float(getattr(self.target, name))
                command = self.limiters[name].step(target, dt)
                self.wheels[name].set_velocity(command)
            telemetry = self.telemetry()
            self._check_health_and_sync(telemetry, now)
            if (
                stale
                and self.timeout_started is not None
                and now - self.timeout_started >= self.limits.idle_after_timeout_s
                and all(abs(limiter.value) < 1e-4 for limiter in self.limiters.values())
            ):
                self.disable()
            return telemetry
        except Exception as exc:
            self.fault("RUNTIME_FAILURE", str(exc))
            raise

    def _check_health_and_sync(self, telemetry: dict[str, object], now: float) -> None:
        for name in WHEEL_NAMES:
            status = telemetry[name]
            if not status.healthy:  # type: ignore[union-attr]
                raise RuntimeError(f"{name} became unhealthy")
        for side, names in (
            ("left", ("front_left", "rear_left")),
            ("right", ("front_right", "rear_right")),
        ):
            velocities = [telemetry[name].velocity_turns_s for name in names]  # type: ignore[union-attr]
            mismatch = abs(velocities[0] - velocities[1])
            if mismatch > self.max_side_difference:
                started = self.mismatch_started[side]
                self.mismatch_started[side] = now if started is None else started
                if now - self.mismatch_started[side] >= self.mismatch_fault_s:
                    raise RuntimeError(
                        f"{side} wheel mismatch {mismatch:.4f} turn/s persisted"
                    )
            else:
                self.mismatch_started[side] = None

    def telemetry(self) -> dict[str, object]:
        return {name: wheel.telemetry() for name, wheel in self.wheels.items()}

    def disable(self) -> None:
        self.safety.state = DriveState.STOPPING
        self.safe_shutdown()
        self.safety.state = DriveState.READY

    def fault(self, code: str, message: str) -> None:
        self.safe_shutdown()
        self.safety.trip(code, message, time.monotonic())

    def safe_shutdown(self) -> None:
        for wheel in self.wheels.values():
            try:
                wheel.stop()
            except Exception:
                pass
        time.sleep(0.05)
        for wheel in self.wheels.values():
            try:
                wheel.idle()
            except Exception:
                pass
        for limiter in self.limiters.values():
            limiter.value = 0.0
