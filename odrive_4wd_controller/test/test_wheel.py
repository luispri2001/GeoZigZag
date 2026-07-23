from odrive_4wd_controller.odrive_device import AxisTelemetry
from odrive_4wd_controller.wheel import Wheel


class FakeDevice:
    def __init__(self):
        self.last_command = None

    def command_velocity(self, _axis, value):
        self.last_command = value

    def read_axis(self, _axis):
        return AxisTelemetry(
            state=1,
            position_turns=2.0,
            velocity_turns_s=3.0,
            current_a=0.0,
            motor_temperature_c=None,
            controller_temperature_c=None,
            calibrated=True,
            encoder_ready=True,
            errors={"axis": 0, "motor": 0, "encoder": 0, "controller": 0},
        )


def test_negative_direction_applied_to_command_and_feedback():
    device = FakeDevice()
    wheel = Wheel("front_right", device, 0, -1, 0.1)
    wheel.set_velocity(0.2)
    assert device.last_command == -0.2
    telemetry = wheel.telemetry()
    assert telemetry.position_turns == -2.0
    assert telemetry.velocity_turns_s == -3.0
