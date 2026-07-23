from types import SimpleNamespace

import pytest

from odrive_4wd_controller.odrive_device import ODriveDevice


class FakeAxis:
    def __init__(self):
        self.clear_count = 0

    def clear_errors(self):
        self.clear_count += 1


class FakeLegacyODrive:
    def __init__(self):
        self.axis0 = FakeAxis()
        self.axis1 = FakeAxis()


def test_legacy_error_clear_is_explicit_and_per_axis():
    wrapper = ODriveDevice("ABC")
    wrapper.device = FakeLegacyODrive()
    wrapper.clear_errors_once()
    assert wrapper.device.axis0.clear_count == 1
    assert wrapper.device.axis1.clear_count == 1


def test_power_configuration_requires_exact_armed_two_ohm_resistor():
    wrapper = ODriveDevice("ABC")
    wrapper.device = SimpleNamespace(
        config=SimpleNamespace(
            brake_resistance=2.0,
            dc_max_negative_current=-0.000001,
        ),
        brake_resistor_armed=True,
    )
    wrapper.validate_power_configuration(
        brake_resistance_ohm=2.0, max_regen_current_a=0.0
    )
    wrapper.device.config.brake_resistance = 1.9
    with pytest.raises(RuntimeError, match="expected exactly 2.0"):
        wrapper.validate_power_configuration(
            brake_resistance_ohm=2.0, max_regen_current_a=0.0
        )
