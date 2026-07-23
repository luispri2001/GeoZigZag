"""Standalone command-line tools for discovery, testing and calculations."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .config import (
    ConfigurationError,
    WHEEL_NAMES,
    is_required,
    load_yaml,
    package_config_dir,
    require_number,
    unresolved_paths,
    validate_wheel_mapping,
)
from .kinematics import (
    differential_drive,
    linear_mps_to_wheel_turns_s,
    motor_rpm,
    wheel_rpm,
)
from .drivetrain import DriveLimits, Drivetrain
from .odrive_device import ODriveDevice
from .wheel import Wheel


def _config_argument(parser: argparse.ArgumentParser, name: str, filename: str) -> None:
    parser.add_argument(
        name,
        type=Path,
        default=package_config_dir() / filename,
        help=f"default: package config/{filename}",
    )


def _serials_from_mapping(path: Path) -> list[str]:
    data = load_yaml(path)
    serials = {
        str(wheel.get("odrive_serial")).upper()
        for wheel in data.get("wheels", {}).values()
        if isinstance(wheel, dict)
        and isinstance(wheel.get("odrive_serial"), str)
        and not is_required(wheel.get("odrive_serial"))
    }
    return sorted(serials)


def discovery_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Discover expected ODrives by serial.")
    _config_argument(parser, "--mapping", "wheel_mapping.yaml")
    parser.add_argument("--serial", action="append", default=[])
    parser.add_argument("--timeout", type=float, default=3.0)
    args = parser.parse_args(argv)
    serials = [s.upper() for s in args.serial] or _serials_from_mapping(args.mapping)
    if not serials:
        print("No real serials in mapping; pass --serial SERIAL.", file=sys.stderr)
        return 2
    results = []
    failed = False
    for serial in serials:
        device = ODriveDevice(serial)
        try:
            device.connect(args.timeout)
            axes = [device.read_axis(i) for i in (0, 1)]
            results.append(
                {
                    "serial": serial,
                    "connected": True,
                    "hardware": device.expected_hardware,
                    "firmware": device.expected_firmware,
                    "axis_count": 2,
                    "dc_bus_voltage_v": device.bus_voltage(),
                    "axes": [
                        {
                            "axis": i,
                            "state": status.state,
                            "calibrated": status.calibrated,
                            "encoder_ready": status.encoder_ready,
                            "errors": status.errors,
                        }
                        for i, status in enumerate(axes)
                    ],
                }
            )
        except Exception as exc:
            failed = True
            results.append({"serial": serial, "connected": False, "error": str(exc)})
    print(json.dumps({"controllers": results}, indent=2))
    return 1 if failed else 0


def _movement_device(args: argparse.Namespace) -> ODriveDevice:
    if not args.confirm_lifted_and_clear:
        raise RuntimeError(
            "movement refused: add --confirm-lifted-and-clear after lifting wheels "
            "and preparing the physical emergency stop"
        )
    if not (0 < args.velocity <= 0.02):
        raise RuntimeError("velocity must be in (0, 0.02] turn/s")
    if not (0 < args.duration <= 1.0):
        raise RuntimeError("duration must be in (0, 1.0] s")
    device = ODriveDevice(args.serial)
    device.connect(10.0)
    device.apply_axis_limits(
        args.axis,
        current_a=2.0,
        velocity_turns_s=0.2,
        acceleration_turns_s2=0.05,
    )
    status = device.read_axis(args.axis)
    if not status.healthy:
        raise RuntimeError("axis is not calibrated and error-free")
    return device


def _pulse(device: ODriveDevice, axis: int, velocity: float, duration: float) -> dict[str, Any]:
    start = device.read_axis(axis)
    max_current = 0.0
    max_velocity = 0.0
    try:
        device.arm_axis(axis)
        device.command_velocity(axis, velocity)
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            status = device.read_axis(axis)
            if not status.healthy:
                raise RuntimeError(f"axis fault during pulse: {status.errors}")
            max_current = max(max_current, abs(status.current_a))
            max_velocity = max(max_velocity, abs(status.velocity_turns_s))
            time.sleep(0.02)
        device.command_velocity(axis, 0.0)
        time.sleep(0.25)
    finally:
        try:
            device.command_velocity(axis, 0.0)
            device.idle_axis(axis)
        except Exception:
            pass
    end = device.read_axis(axis)
    return {
        "position_delta_turns": end.position_turns - start.position_turns,
        "max_abs_velocity_turns_s": max_velocity,
        "max_abs_current_a": max_current,
        "errors": end.errors,
        "final_state": end.state,
    }


def test_single_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safely test exactly one ODrive axis.")
    parser.add_argument("--serial", required=True)
    parser.add_argument("--axis", type=int, choices=(0, 1), required=True)
    parser.add_argument("--velocity", type=float, default=0.02)
    parser.add_argument("--duration", type=float, default=0.6)
    parser.add_argument("--confirm-lifted-and-clear", action="store_true")
    args = parser.parse_args(argv)
    try:
        device = _movement_device(args)
        result = {
            "positive": _pulse(device, args.axis, args.velocity, args.duration),
            "negative": _pulse(device, args.axis, -args.velocity, args.duration),
        }
        print(json.dumps(result, indent=2))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


def identify_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Identify physical wheels one axis at a time.")
    parser.add_argument("--serial", required=True)
    parser.add_argument("--axes", nargs="+", type=int, choices=(0, 1), default=(0, 1))
    parser.add_argument("--velocity", type=float, default=0.015)
    parser.add_argument("--duration", type=float, default=0.5)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--confirm-lifted-and-clear", action="store_true")
    args = parser.parse_args(argv)
    assignments: dict[str, Any] = {}
    try:
        for axis in args.axes:
            args.axis = axis
            device = _movement_device(args)
            print(f"\nPulsing {args.serial}/axis{axis} in positive controller direction.")
            result = _pulse(device, axis, args.velocity, args.duration)
            print(json.dumps(result, indent=2))
            wheel = input(f"Which wheel moved? {', '.join(WHEEL_NAMES)}: ").strip()
            if wheel not in WHEEL_NAMES or wheel in assignments:
                raise RuntimeError("invalid or duplicate wheel name")
            forward = input("Was that robot-forward direction? [y/N]: ").strip().lower()
            assignments[wheel] = {
                "odrive_serial": args.serial.upper(),
                "axis": axis,
                "direction": 1 if forward == "y" else -1,
                "confirmed": True,
            }
    except (EOFError, KeyboardInterrupt):
        print("Identification cancelled; no mapping written.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}; no mapping written.", file=sys.stderr)
        return 2
    output = {
        "mapping_status": "PARTIAL" if len(assignments) < 4 else "COMPLETE",
        "wheels": {
            name: assignments.get(
                name,
                {
                    "odrive_serial": "REQUIRED_IDENTIFICATION",
                    "axis": "REQUIRED_IDENTIFICATION",
                    "direction": "REQUIRED_IDENTIFICATION",
                    "confirmed": False,
                },
            )
            for name in WHEEL_NAMES
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(yaml.safe_dump(output, sort_keys=False), encoding="utf-8")
    print(f"Saved {args.output}")
    return 0


def calculate_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Calculate drivetrain conversions and limits.")
    _config_argument(parser, "--robot", "robot.yaml")
    _config_argument(parser, "--motors", "motor_parameters.yaml")
    parser.add_argument("--linear-velocity", type=float, default=0.05)
    parser.add_argument("--angular-velocity", type=float, default=0.0)
    args = parser.parse_args(argv)
    try:
        robot = load_yaml(args.robot)["robot"]
        radius = require_number(robot, "effective_wheel_radius_m", positive=True)
        track = require_number(robot, "track_width_m", positive=True)
        ratio = require_number(robot, "gear_ratio", positive=True)
        left, right = differential_drive(args.linear_velocity, args.angular_velocity, track)
        result = {
            "odrive_velocity_unit": "motor turns per second on firmware 0.5.1",
            "left_linear_velocity_mps": left,
            "right_linear_velocity_mps": right,
            "left_wheel_turns_s": linear_mps_to_wheel_turns_s(left, radius),
            "right_wheel_turns_s": linear_mps_to_wheel_turns_s(right, radius),
            "wheel_rpm_at_linear_command": wheel_rpm(args.linear_velocity, radius),
            "motor_rpm_at_linear_command": motor_rpm(
                args.linear_velocity, radius, ratio
            ),
            "wheel_angular_velocity_rad_s": args.linear_velocity / radius,
            "encoder_counts_per_wheel_turn": 16384 * ratio,
        }
        motors = load_yaml(args.motors)["defaults"]
        torque_constant = motors.get("torque_constant_nm_a")
        if not is_required(torque_constant):
            current = float(motors["calibration_current_a"])
            efficiency = robot.get("mechanical_efficiency")
            result["motor_torque_nm_at_calibration_current"] = float(torque_constant) * current
            if not is_required(efficiency):
                wheel_torque = float(torque_constant) * current * ratio * float(efficiency)
                result["wheel_torque_nm"] = wheel_torque
                result["traction_force_n"] = wheel_torque / radius
        print(json.dumps(result, indent=2))
        return 0
    except (ConfigurationError, KeyError, ValueError) as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2


def export_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export host and ODrive configurations.")
    _config_argument(parser, "--mapping", "wheel_mapping.yaml")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--include-odrive-backups", action="store_true")
    args = parser.parse_args(argv)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = args.output / f"odrive-4wd-export-{stamp}"
    destination.mkdir(parents=True, exist_ok=False)
    for path in package_config_dir().glob("*.yaml"):
        shutil.copy2(path, destination / path.name)
    if args.include_odrive_backups:
        for serial in _serials_from_mapping(args.mapping):
            target = destination / f"odrive-{serial}.json"
            command = ["odrivetool", "-s", serial, "backup-config", str(target)]
            subprocess.run(command, check=True)
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "unresolved_values": {
            path.name: unresolved_paths(load_yaml(path))
            for path in package_config_dir().glob("*.yaml")
        },
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(destination)
    return 0


def _add_complete_test_arguments(parser: argparse.ArgumentParser) -> None:
    _config_argument(parser, "--mapping", "wheel_mapping.yaml")
    _config_argument(parser, "--robot", "robot.yaml")
    _config_argument(parser, "--profiles", "limit_profiles.yaml")
    parser.add_argument("--profile", default="bench_test")
    parser.add_argument("--duration", type=float, default=0.6)
    parser.add_argument("--confirm-lifted-and-clear", action="store_true")


def _build_complete_drivetrain(args: argparse.Namespace) -> Drivetrain:
    if not args.confirm_lifted_and_clear:
        raise RuntimeError("add --confirm-lifted-and-clear after physical safety checks")
    if not 0 < args.duration <= 1.0:
        raise RuntimeError("duration must be in (0, 1.0] s")
    mapping = load_yaml(args.mapping)
    validate_wheel_mapping(mapping, require_complete=True)
    robot_document = load_yaml(args.robot)
    robot = robot_document["robot"]
    control = robot_document["control"]
    kinematics = robot_document["kinematics"]
    profile = load_yaml(args.profiles)["profiles"][args.profile]
    if profile.get("enabled") is not True:
        raise RuntimeError(f"profile {args.profile} is disabled")
    radius = require_number(robot, "effective_wheel_radius_m", positive=True)
    track = require_number(robot, "track_width_m", positive=True)
    ratio = require_number(robot, "gear_ratio", positive=True)
    devices: dict[str, ODriveDevice] = {}
    wheels: dict[str, Wheel] = {}
    for name, entry in mapping["wheels"].items():
        serial = str(entry["odrive_serial"]).upper()
        if serial not in devices:
            devices[serial] = ODriveDevice(serial)
            devices[serial].connect(8.0)
        wheels[name] = Wheel(
            name,
            devices[serial],
            int(entry["axis"]),
            int(entry["direction"]),
            radius,
            ratio,
            float(kinematics.get(f"{name}_scale", 1.0)),
        )
    limits = DriveLimits(
        require_number(profile, "max_linear_velocity_mps", positive=True),
        require_number(profile, "max_angular_velocity_rad_s", positive=True),
        require_number(profile, "max_wheel_velocity_turns_s", positive=True),
        require_number(profile, "max_wheel_acceleration_turns_s2", positive=True),
        require_number(profile, "max_wheel_deceleration_turns_s2", positive=True),
        require_number(profile, "max_motor_current_a", positive=True),
        require_number(profile, "command_timeout_s", positive=True),
        require_number(control, "idle_after_timeout_s", positive=True),
    )
    drive = Drivetrain(
        wheels,
        wheel_radius_m=radius,
        track_width_m=track,
        limits=limits,
        scales={
            "left": float(kinematics["left_velocity_scale"]),
            "right": float(kinematics["right_velocity_scale"]),
        },
    )
    drive.initialize()
    return drive


def _run_command(drive: Drivetrain, linear: float, angular: float, duration: float) -> None:
    drive.set_command(linear, angular)
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        drive.step()
        time.sleep(0.02)
    drive.set_command(0.0, 0.0)
    stop_deadline = time.monotonic() + 0.5
    while time.monotonic() < stop_deadline:
        drive.step()
        time.sleep(0.02)


def test_side_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Test both confirmed wheels on one side.")
    _add_complete_test_arguments(parser)
    parser.add_argument("--side", choices=("left", "right"), required=True)
    args = parser.parse_args(argv)
    drive: Drivetrain | None = None
    try:
        drive = _build_complete_drivetrain(args)
        selected = [f"front_{args.side}", f"rear_{args.side}"]
        for name, wheel in drive.wheels.items():
            if name not in selected:
                wheel.idle()
        for name in selected:
            drive.wheels[name].arm()
        speed = min(0.02, drive.limits.max_wheel_turns_s)
        results = {}
        for label, command in (("positive", speed), ("negative", -speed)):
            start = {name: drive.wheels[name].telemetry().position_turns for name in selected}
            try:
                for name in selected:
                    drive.wheels[name].set_velocity(command)
                time.sleep(args.duration)
            finally:
                for name in selected:
                    drive.wheels[name].stop()
                time.sleep(0.25)
            results[label] = {
                name: drive.wheels[name].telemetry().position_turns - start[name]
                for name in selected
            }
        print(json.dumps(results, indent=2))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        if drive:
            drive.safe_shutdown()


def test_drivetrain_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a bounded four-wheel bench sequence.")
    _add_complete_test_arguments(parser)
    args = parser.parse_args(argv)
    drive: Drivetrain | None = None
    try:
        drive = _build_complete_drivetrain(args)
        drive.enable()
        linear = min(0.03, drive.limits.max_linear_mps)
        angular = min(0.10, drive.limits.max_angular_rad_s)
        for label, v, w in (
            ("forward", linear, 0.0),
            ("reverse", -linear, 0.0),
            ("rotate_left", 0.0, angular),
            ("rotate_right", 0.0, -angular),
        ):
            print(label)
            _run_command(drive, v, w, args.duration)
        print("PASS")
        return 0
    except KeyboardInterrupt:
        print("Interrupted; stopping.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        if drive:
            drive.safe_shutdown()
