import pytest

from odrive_4wd_controller.config import ConfigurationError, validate_wheel_mapping


def mapping():
    return {
        "wheels": {
            "front_left": {
                "odrive_serial": "A",
                "axis": 0,
                "direction": 1,
                "confirmed": True,
            },
            "rear_left": {
                "odrive_serial": "A",
                "axis": 1,
                "direction": 1,
                "confirmed": True,
            },
            "front_right": {
                "odrive_serial": "B",
                "axis": 0,
                "direction": -1,
                "confirmed": True,
            },
            "rear_right": {
                "odrive_serial": "B",
                "axis": 1,
                "direction": -1,
                "confirmed": True,
            },
        }
    }


def test_complete_serial_mapping_is_valid():
    validate_wheel_mapping(mapping())


def test_duplicate_axis_is_rejected():
    value = mapping()
    value["wheels"]["rear_left"]["axis"] = 0
    with pytest.raises(ConfigurationError):
        validate_wheel_mapping(value)


def test_unconfirmed_mapping_is_rejected():
    value = mapping()
    value["wheels"]["front_left"]["confirmed"] = False
    with pytest.raises(ConfigurationError):
        validate_wheel_mapping(value)


def test_required_second_serial_is_rejected():
    value = mapping()
    value["wheels"]["front_right"]["odrive_serial"] = "REQUIRED_SECOND_ODRIVE_SERIAL"
    with pytest.raises(ConfigurationError):
        validate_wheel_mapping(value)
