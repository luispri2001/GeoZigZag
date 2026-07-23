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
