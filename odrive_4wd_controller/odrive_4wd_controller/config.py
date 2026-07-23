"""Configuration loading and strict validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REQUIRED_PREFIX = "REQUIRED_"
WHEEL_NAMES = ("front_left", "rear_left", "front_right", "rear_right")


class ConfigurationError(ValueError):
    pass


def load_yaml(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    if not isinstance(data, dict):
        raise ConfigurationError(f"{path}: YAML root must be a mapping")
    return data


def is_required(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(REQUIRED_PREFIX)


def require_number(data: dict[str, Any], key: str, *, positive: bool = False) -> float:
    value = data.get(key)
    if is_required(value):
        raise ConfigurationError(f"{key} is unresolved: {value}")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(f"{key} must be numeric")
    result = float(value)
    if positive and result <= 0:
        raise ConfigurationError(f"{key} must be > 0")
    return result


def unresolved_paths(value: Any, prefix: str = "") -> list[str]:
    found: list[str] = []
    if is_required(value):
        found.append(prefix or "<root>")
    elif isinstance(value, dict):
        for key, child in value.items():
            found.extend(unresolved_paths(child, f"{prefix}.{key}".strip(".")))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(unresolved_paths(child, f"{prefix}[{index}]"))
    return found


def validate_wheel_mapping(data: dict[str, Any], *, require_complete: bool = True) -> None:
    wheels = data.get("wheels")
    if not isinstance(wheels, dict) or set(wheels) != set(WHEEL_NAMES):
        raise ConfigurationError(f"wheels must contain exactly {WHEEL_NAMES}")
    seen: set[tuple[str, int]] = set()
    for name in WHEEL_NAMES:
        wheel = wheels[name]
        serial = wheel.get("odrive_serial")
        axis = wheel.get("axis")
        direction = wheel.get("direction")
        if is_required(serial) or not isinstance(serial, str) or not serial:
            if require_complete:
                raise ConfigurationError(f"{name}.odrive_serial is unresolved")
            continue
        if axis not in (0, 1):
            raise ConfigurationError(f"{name}.axis must be 0 or 1")
        if direction not in (-1, 1):
            if require_complete:
                raise ConfigurationError(f"{name}.direction must be -1 or 1")
            continue
        if require_complete and wheel.get("confirmed") is not True:
            raise ConfigurationError(f"{name} has not been physically confirmed")
        identity = (serial.upper(), axis)
        if identity in seen:
            raise ConfigurationError(f"duplicate ODrive axis mapping: {identity}")
        seen.add(identity)


def package_config_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "config"
