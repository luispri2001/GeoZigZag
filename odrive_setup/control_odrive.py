#!/usr/bin/env python3
"""Conservative interactive control for a verified legacy dual-axis ODrive.

Launching this program never commands movement. The operator must explicitly
arm an axis and then issue a velocity command. On every exit path, all available
axes receive zero velocity followed by IDLE.
"""

from __future__ import annotations

import argparse
import json
import math
import signal
import sys
import time
from pathlib import Path
from typing import Any

from odrive_common import (
    find_axes,
    first_path,
    has_nonzero_error,
    legacy_calibration_is_valid,
    read_path,
)

AXIS_STATE_IDLE = 1
AXIS_STATE_CLOSED_LOOP_CONTROL = 8
CONTROL_MODE_VELOCITY = 2
SUPPORTED_INPUT_MODES = {1, 2}  # PASSTHROUGH or VEL_RAMP


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--safety-file", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--serial")
    parser.add_argument("--interface", default="usb")
    parser.add_argument("--max-velocity", type=float, default=0.20, help="turn/s")
    parser.add_argument("--max-acceleration", type=float, default=0.20, help="turn/s²")
    parser.add_argument(
        "--max-configured-current",
        type=float,
        default=10.0,
        help="Refuse arming if the controller current limit exceeds this value [A].",
    )
    return parser.parse_args()


def load_safety_file(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    required_true = (
        "controller_identity_verified",
        "main_dc_power_verified",
        "motor_and_encoder_specs_verified",
        "wiring_verified",
        "wheels_or_load_lifted",
        "emergency_stop_available",
    )
    missing = [name for name in required_true if data.get(name) is not True]
    if missing:
        raise RuntimeError("safety prerequisites not verified: " + ", ".join(missing))
    required_text = ("serial_number", "hardware_version", "firmware_version")
    unknown = [name for name in required_text if not data.get(name)]
    if unknown:
        raise RuntimeError("identity values missing: " + ", ".join(unknown))
    for axis_name in ("axis0", "axis1"):
        axis = data.get(axis_name, {})
        fields = ("motor_model", "rated_voltage_v", "rated_current_a", "pole_pairs",
                  "encoder_type", "encoder_resolution", "max_safe_current_a")
        absent = [f"{axis_name}.{name}" for name in fields if axis.get(name) in (None, "")]
        if absent:
            raise RuntimeError("motor/encoder values missing: " + ", ".join(absent))
    return data


def numeric(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class SafeController:
    def __init__(self, device: Any, args: argparse.Namespace, safety: dict[str, Any]):
        self.device = device
        self.args = args
        self.safety = safety
        self.axes = dict(find_axes(device))
        self.armed: set[str] = set()
        self.commanded_velocity = {name: 0.0 for name in self.axes}
        if set(self.axes) != {"axis0", "axis1"}:
            raise RuntimeError(
                f"expected a verified dual-axis device, found {sorted(self.axes)}"
            )
        actual_serial = read_path(device, "serial_number", None)
        if isinstance(actual_serial, int):
            actual_serial = f"{actual_serial:012X}"
        if str(actual_serial).upper() != str(safety["serial_number"]).upper():
            raise RuntimeError(
                f"serial mismatch: connected={actual_serial}, safety file="
                f"{safety['serial_number']}"
            )
        for label, prefix in (("hardware_version", "hw_version"),
                              ("firmware_version", "fw_version")):
            expected_parts = str(safety[label]).split(".")
            suffixes = ("major", "minor", "revision")[:len(expected_parts)]
            actual_parts = [
                read_path(device, f"{prefix}_{suffix}", None) for suffix in suffixes
            ]
            if any(part is None for part in actual_parts):
                raise RuntimeError(f"connected {label} is unavailable")
            connected = ".".join(str(int(part)) for part in actual_parts)
            if connected != str(safety[label]):
                raise RuntimeError(
                    f"{label} mismatch: connected={connected}, "
                    f"safety file={safety[label]}"
                )
        expected_variant = safety.get("hardware_variant")
        actual_variant = read_path(device, "hw_version_variant", None)
        if expected_variant is not None and int(actual_variant) != int(expected_variant):
            raise RuntimeError(
                f"hardware variant mismatch: connected={actual_variant}, "
                f"safety file={expected_variant}"
            )

    def status(self) -> None:
        for name, axis in self.axes.items():
            state = read_path(axis, "current_state")
            position = first_path(axis, ("encoder.pos_estimate", "pos_estimate"))
            velocity = first_path(axis, ("encoder.vel_estimate", "vel_estimate"))
            current = first_path(
                axis,
                ("motor.current_control.Iq_measured", "motor.foc.Iq_measured"),
            )
            print(
                f"{name}: state={state}, position={position}, velocity={velocity}, "
                f"Iq={current}, errors={has_nonzero_error(axis)}, "
                f"armed={name in self.armed}"
            )

    def validate_axis(self, name: str) -> Any:
        if name not in self.axes:
            raise RuntimeError(f"unknown axis {name!r}")
        axis = self.axes[name]
        if has_nonzero_error(axis):
            raise RuntimeError(f"{name} has an active error; diagnose it before arming")
        if not legacy_calibration_is_valid(axis):
            raise RuntimeError(f"{name} calibration flags are not both valid")
        current_limit = numeric(read_path(axis, "motor.config.current_lim", None))
        if current_limit is None:
            raise RuntimeError(f"{name} current limit is unavailable")
        manifest_limit = float(self.safety[name]["max_safe_current_a"])
        allowed_current = min(self.args.max_configured_current, manifest_limit)
        if current_limit > allowed_current:
            raise RuntimeError(
                f"{name} configured current limit {current_limit} A exceeds "
                f"allowed {allowed_current} A; configure safely outside this runtime tool"
            )
        velocity_limit = numeric(read_path(axis, "controller.config.vel_limit", None))
        if velocity_limit is None or velocity_limit > self.args.max_velocity:
            raise RuntimeError(
                f"{name} configured velocity limit {velocity_limit} turn/s exceeds "
                f"runtime ceiling {self.args.max_velocity}; lower it before arming"
            )
        mode = numeric(read_path(axis, "controller.config.control_mode", None))
        input_mode = numeric(read_path(axis, "controller.config.input_mode", None))
        if mode != CONTROL_MODE_VELOCITY or input_mode not in SUPPORTED_INPUT_MODES:
            raise RuntimeError(
                f"{name} must already use velocity control and a supported input mode; "
                f"found control_mode={mode}, input_mode={input_mode}"
            )
        return axis

    def arm(self, name: str) -> None:
        axis = self.validate_axis(name)
        axis.controller.input_vel = 0.0
        axis.requested_state = AXIS_STATE_CLOSED_LOOP_CONTROL
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if int(axis.current_state) == AXIS_STATE_CLOSED_LOOP_CONTROL:
                self.armed.add(name)
                self.commanded_velocity[name] = 0.0
                print(f"{name} armed at zero velocity")
                return
            time.sleep(0.05)
        self.idle(name)
        raise RuntimeError(f"{name} did not enter closed-loop control")

    def set_velocity(self, name: str, target: float) -> None:
        if name not in self.armed:
            raise RuntimeError(f"{name} is not armed")
        if not math.isfinite(target) or abs(target) > self.args.max_velocity:
            raise RuntimeError(
                f"velocity must be finite and within ±{self.args.max_velocity} turn/s"
            )
        axis = self.axes[name]
        value = self.commanded_velocity[name]
        period = 0.05
        step = self.args.max_acceleration * period
        while abs(target - value) > 1e-9:
            delta = max(-step, min(step, target - value))
            value += delta
            axis.controller.input_vel = value
            self.commanded_velocity[name] = value
            time.sleep(period)
        print(f"{name} command={target:.4f} turn/s")

    def stop(self, name: str | None = None) -> None:
        names = [name] if name else list(self.axes)
        for axis_name in names:
            axis = self.axes.get(axis_name)
            if axis is None:
                continue
            try:
                axis.controller.input_vel = 0.0
                self.commanded_velocity[axis_name] = 0.0
            except Exception as exc:
                print(f"WARNING: zero command failed for {axis_name}: {exc}", file=sys.stderr)

    def idle(self, name: str | None = None) -> None:
        names = [name] if name else list(self.axes)
        self.stop(name)
        for axis_name in names:
            axis = self.axes.get(axis_name)
            if axis is None:
                continue
            try:
                axis.requested_state = AXIS_STATE_IDLE
            except Exception as exc:
                print(f"WARNING: IDLE failed for {axis_name}: {exc}", file=sys.stderr)
            self.armed.discard(axis_name)

    def shell(self) -> None:
        print("No movement has been commanded. Type 'help' for explicit commands.")
        while True:
            try:
                parts = input("odrive-safe> ").strip().split()
            except EOFError:
                break
            if not parts:
                continue
            command = parts[0].lower()
            try:
                if command == "help":
                    print("status | arm AXIS | vel AXIS TURN_PER_S | stop [AXIS] | idle [AXIS] | quit")
                elif command == "status" and len(parts) == 1:
                    self.status()
                elif command == "arm" and len(parts) == 2:
                    self.arm(parts[1])
                elif command == "vel" and len(parts) == 3:
                    self.set_velocity(parts[1], float(parts[2]))
                elif command == "stop" and len(parts) <= 2:
                    self.stop(parts[1] if len(parts) == 2 else None)
                elif command == "idle" and len(parts) <= 2:
                    self.idle(parts[1] if len(parts) == 2 else None)
                elif command in {"quit", "exit"} and len(parts) == 1:
                    break
                else:
                    print("Invalid command. Type 'help'.")
            except Exception as exc:
                print(f"REFUSED: {exc}", file=sys.stderr)


def main() -> int:
    args = parse_args()
    if args.max_velocity <= 0 or args.max_acceleration <= 0 or args.max_configured_current <= 0:
        print("ERROR: safety limits must be positive.", file=sys.stderr)
        return 2
    try:
        safety = load_safety_file(args.safety_file)
        import odrive

        device = odrive.find_sync(
            serial_number=args.serial or safety["serial_number"],
            timeout=args.timeout,
            interfaces=[args.interface],
        )
        if device is None:
            raise RuntimeError("no ODrive discovered")
        controller = SafeController(device, args, safety)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3

    interrupted = False

    def request_stop(_signum: int, _frame: Any) -> None:
        nonlocal interrupted
        interrupted = True
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        controller.shell()
    except KeyboardInterrupt:
        print("\nInterrupt received; stopping and returning both axes to IDLE.")
    finally:
        controller.stop()
        controller.idle()
        print("Zero velocity and IDLE requested for both axes.")
    return 130 if interrupted else 0


if __name__ == "__main__":
    raise SystemExit(main())
