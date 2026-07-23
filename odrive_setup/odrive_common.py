"""Read-only helpers shared by the ODrive setup tools.

The helpers deliberately tolerate API differences between legacy and current
ODrive firmware. Missing fields are reported as unavailable, never guessed.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Any, Iterable


MISSING = "<unavailable>"


def read_path(root: Any, path: str, default: Any = MISSING) -> Any:
    value = root
    try:
        for part in path.split("."):
            value = getattr(value, part)
        return value
    except (AttributeError, KeyError, RuntimeError, TypeError):
        return default


def first_path(root: Any, paths: Iterable[str], default: Any = MISSING) -> Any:
    for path in paths:
        value = read_path(root, path)
        if value != MISSING:
            return value
    return default


def printable(value: Any) -> Any:
    if isinstance(value, Enum):
        return {"name": value.name, "value": value.value}
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, (bool, float, int, str)) or value is None:
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value)


def find_axes(device: Any) -> list[tuple[str, Any]]:
    axes: list[tuple[str, Any]] = []
    for name in ("axis0", "axis1"):
        axis = read_path(device, name, None)
        if axis is not None:
            axes.append((name, axis))
    return axes


def error_snapshot(axis: Any) -> dict[str, Any]:
    fields = {
        "axis_error": ("error", "active_errors"),
        "disarm_reason": ("disarm_reason",),
        "motor_error": ("motor.error",),
        "encoder_error": ("encoder.error",),
        "controller_error": ("controller.error",),
        "procedure_result": ("procedure_result",),
    }
    return {
        key: printable(first_path(axis, candidates))
        for key, candidates in fields.items()
    }


def has_nonzero_error(axis: Any) -> bool:
    for value in error_snapshot(axis).values():
        if isinstance(value, dict):
            value = value.get("value")
        if isinstance(value, int) and value != 0:
            return True
    return False


def calibration_snapshot(axis: Any) -> dict[str, Any]:
    return {
        "motor_calibrated": printable(
            first_path(axis, ("motor.is_calibrated", "config.motor.calibration_valid"))
        ),
        "encoder_ready": printable(
            first_path(
                axis,
                (
                    "encoder.is_ready",
                    "config.load_encoder",
                    "pos_vel_mapper.config.circular",
                ),
            )
        ),
    }


def legacy_calibration_is_valid(axis: Any) -> bool:
    """Return True only when the dual-axis/legacy calibration flags are explicit."""
    motor_ok = read_path(axis, "motor.is_calibrated", None)
    encoder_ok = read_path(axis, "encoder.is_ready", None)
    return motor_ok is True and encoder_ok is True


def axis_snapshot(axis: Any) -> dict[str, Any]:
    fields = {
        "current_state": ("current_state",),
        "requested_state": ("requested_state",),
        "motor_type": ("motor.config.motor_type", "config.motor.motor_type"),
        "pole_pairs": ("motor.config.pole_pairs", "config.motor.pole_pairs"),
        "current_limit": (
            "motor.config.current_lim",
            "config.motor.current_soft_max",
        ),
        "velocity_limit": (
            "controller.config.vel_limit",
            "controller.config.vel_limit_tolerance",
            "config.vel_limit",
        ),
        "encoder_cpr": ("encoder.config.cpr",),
        "encoder_mode": ("encoder.config.mode", "config.load_encoder"),
        "control_mode": ("controller.config.control_mode", "config.control_mode"),
        "input_mode": ("controller.config.input_mode", "config.input_mode"),
        "position_estimate": (
            "encoder.pos_estimate",
            "pos_vel_mapper.pos_rel",
            "pos_estimate",
        ),
        "velocity_estimate": (
            "encoder.vel_estimate",
            "pos_vel_mapper.vel",
            "vel_estimate",
        ),
        "iq_measured": ("motor.current_control.Iq_measured", "motor.foc.Iq_measured"),
        "iq_setpoint": ("motor.current_control.Iq_setpoint", "motor.foc.Iq_setpoint"),
    }
    result = {
        key: printable(first_path(axis, candidates))
        for key, candidates in fields.items()
    }
    result["calibration"] = calibration_snapshot(axis)
    result["errors"] = error_snapshot(axis)
    return result


def device_snapshot(device: Any) -> dict[str, Any]:
    serial = read_path(device, "serial_number")
    if isinstance(serial, int):
        serial = f"{serial:012X}"
    version_fields = (
        "hw_version_major",
        "hw_version_minor",
        "hw_version_revision",
        "hw_version_variant",
        "fw_version_major",
        "fw_version_minor",
        "fw_version_revision",
        "fw_version_unreleased",
        "bootloader_version",
        "vbus_voltage",
        "ibus",
        "brake_resistor_armed",
        "config.enable_brake_resistor",
        "config.brake_resistance",
        "config.dc_bus_undervoltage_trip_level",
        "config.dc_bus_overvoltage_trip_level",
        "config.dc_max_positive_current",
        "config.dc_max_negative_current",
    )
    result = {"serial_number": printable(serial)}
    result.update({field: printable(read_path(device, field)) for field in version_fields})
    result["axes"] = {
        name: axis_snapshot(axis) for name, axis in find_axes(device)
    }
    return result
