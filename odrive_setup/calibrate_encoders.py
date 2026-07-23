#!/usr/bin/env python3
"""Safely calibrate incremental encoder offsets, one ODrive axis at a time.

This script is intended for the verified ODrive v3.6 / firmware 0.5.1 setup in
this directory. Calibration moves the selected wheel. It never runs merely by
launching the file: the operator must supply the explicit physical-safety
confirmation flag.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from odrive_common import has_nonzero_error

AXIS_STATE_IDLE = 1
AXIS_STATE_ENCODER_OFFSET_CALIBRATION = 7
SAFE_CURRENT_A = 2.0
SAFE_RESISTANCE_CALIBRATION_V = 2.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("axis", choices=("axis0", "axis1", "all"))
    parser.add_argument("--safety-file", type=Path, required=True)
    parser.add_argument("--serial")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--confirm-lifted-and-clear",
        action="store_true",
        help="Required acknowledgement that wheels are lifted, area is clear, and E-stop is ready.",
    )
    return parser.parse_args()


def stop_all(device: Any) -> None:
    for axis in (device.axis0, device.axis1):
        try:
            axis.controller.input_vel = 0.0
        except Exception:
            pass
        try:
            axis.requested_state = AXIS_STATE_IDLE
        except Exception:
            pass


def calibrate_axis(name: str, axis: Any, other_axis: Any, timeout: float) -> None:
    if int(other_axis.current_state) != AXIS_STATE_IDLE:
        raise RuntimeError("the other axis is not IDLE")
    if not bool(axis.motor.is_calibrated):
        raise RuntimeError(f"{name} motor calibration is not valid")
    if has_nonzero_error(axis):
        raise RuntimeError(f"{name} has active errors; diagnose before calibration")

    axis.motor.config.current_lim = SAFE_CURRENT_A
    axis.motor.config.calibration_current = SAFE_CURRENT_A
    axis.motor.config.resistance_calib_max_voltage = SAFE_RESISTANCE_CALIBRATION_V
    start_count = int(axis.encoder.shadow_count)
    print(f"{name}: starting encoder offset calibration at {SAFE_CURRENT_A:.1f} A")
    axis.requested_state = AXIS_STATE_ENCODER_OFFSET_CALIBRATION

    deadline = time.monotonic() + timeout
    observed_busy = False
    while time.monotonic() < deadline:
        state = int(axis.current_state)
        if state != AXIS_STATE_IDLE:
            observed_busy = True
        if observed_busy and state == AXIS_STATE_IDLE:
            break
        if has_nonzero_error(axis):
            raise RuntimeError(f"{name} reported an error during calibration")
        if int(other_axis.current_state) != AXIS_STATE_IDLE:
            raise RuntimeError("the other axis left IDLE unexpectedly")
        time.sleep(0.05)
    else:
        raise TimeoutError(f"{name} calibration did not finish within {timeout:.1f} s")

    if has_nonzero_error(axis) or not bool(axis.encoder.is_ready):
        raise RuntimeError(
            f"{name} calibration failed: ready={axis.encoder.is_ready}, "
            f"axis={axis.error:#x}, motor={axis.motor.error:#x}, "
            f"encoder={axis.encoder.error:#x}, controller={axis.controller.error:#x}"
        )
    delta = int(axis.encoder.shadow_count) - start_count
    print(f"{name}: PASS; encoder ready, calibration travel={delta} counts")


def main() -> int:
    args = parse_args()
    if not args.confirm_lifted_and_clear:
        print(
            "REFUSED: calibration moves the wheel. Lift both wheels, clear the area, "
            "prepare the physical E-stop, then add --confirm-lifted-and-clear.",
            file=sys.stderr,
        )
        return 2

    safety = json.loads(args.safety_file.read_text(encoding="utf-8"))
    required = (
        "controller_identity_verified",
        "main_dc_power_verified",
        "motor_and_encoder_specs_verified",
        "wiring_verified",
        "wheels_or_load_lifted",
        "emergency_stop_available",
    )
    missing = [key for key in required if safety.get(key) is not True]
    if missing:
        print("REFUSED: unverified safety fields: " + ", ".join(missing), file=sys.stderr)
        return 2

    import odrive

    serial = args.serial or safety["serial_number"]
    device = odrive.find_sync(serial_number=serial, timeout=15, interfaces=["usb"])
    if device is None:
        print("ERROR: ODrive not found", file=sys.stderr)
        return 3
    actual_serial = f"{int(device.serial_number):012X}"
    if actual_serial.upper() != str(safety["serial_number"]).upper():
        print(f"ERROR: serial mismatch ({actual_serial})", file=sys.stderr)
        return 3

    selected = ("axis0", "axis1") if args.axis == "all" else (args.axis,)
    try:
        stop_all(device)
        time.sleep(0.2)
        for name in selected:
            axis = getattr(device, name)
            other = device.axis1 if name == "axis0" else device.axis0
            calibrate_axis(name, axis, other, args.timeout)
            axis.requested_state = AXIS_STATE_IDLE
            time.sleep(0.2)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 4
    finally:
        stop_all(device)
        print("Zero velocity and IDLE requested for both axes.")

    print("Calibration is valid in the current powered session.")
    print("Encoder pre_calibrated remains disabled; rerun after every main-power cycle.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
