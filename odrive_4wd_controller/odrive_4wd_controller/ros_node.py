"""ROS 2 Humble interface for the serial-bound four-wheel drivetrain."""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

import rclpy
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from geometry_msgs.msg import TransformStamped, Twist
from nav_msgs.msg import Odometry
from rclpy.node import Node
from sensor_msgs.msg import JointState
from std_srvs.srv import SetBool, Trigger
from tf2_ros import TransformBroadcaster

from .config import (
    ConfigurationError,
    WHEEL_NAMES,
    load_yaml,
    package_config_dir,
    require_number,
    validate_wheel_mapping,
)
from .drivetrain import DriveLimits, Drivetrain, TwoWheelBenchDrive
from .odometry import SkidSteerOdometry
from .odrive_device import ODriveDevice
from .safety import DriveState
from .wheel import Wheel


def _quaternion_from_yaw(yaw: float) -> tuple[float, float]:
    return math.sin(yaw * 0.5), math.cos(yaw * 0.5)


def _covariance_from_diagonal(values: list[float]) -> list[float]:
    if len(values) != 6:
        raise ConfigurationError("odometry covariance diagonal must have 6 values")
    covariance = [0.0] * 36
    for index, value in enumerate(values):
        covariance[index * 6 + index] = float(value)
    return covariance


class ODrive4WDNode(Node):
    def __init__(self) -> None:
        super().__init__("odrive_4wd_controller")
        self.declare_parameter("config_dir", str(package_config_dir()))
        self.declare_parameter("limit_profile", "bench_test")
        self.declare_parameter("hardware_mode", "four_wheel")
        self.config_dir = Path(
            self.get_parameter("config_dir").get_parameter_value().string_value
        )
        self.profile_name = (
            self.get_parameter("limit_profile").get_parameter_value().string_value
        )
        self.hardware_mode = (
            self.get_parameter("hardware_mode").get_parameter_value().string_value
        )
        self.drivetrain: Drivetrain | TwoWheelBenchDrive | None = None
        self.odometry: SkidSteerOdometry | None = None
        self.devices: dict[str, ODriveDevice] = {}
        self.configuration_error: str | None = None
        self.last_telemetry: dict[str, object] = {}
        self.last_update = time.monotonic()

        self.odom_pub = self.create_publisher(Odometry, "/odom", 10)
        self.joints_pub = self.create_publisher(JointState, "/joint_states", 10)
        self.diagnostics_pub = self.create_publisher(
            DiagnosticArray, "/diagnostics", 10
        )
        self.tf_broadcaster = TransformBroadcaster(self)
        self.create_subscription(Twist, "/cmd_vel", self._cmd_vel, 10)
        self.create_service(SetBool, "/drivetrain/enable", self._enable)
        self.create_service(Trigger, "/drivetrain/disable", self._disable)
        self.create_service(Trigger, "/drivetrain/idle", self._disable)
        self.create_service(
            Trigger, "/drivetrain/clear_recoverable_errors", self._clear_errors
        )
        self.create_service(Trigger, "/drivetrain/run_diagnostics", self._diagnostics)
        self.create_service(Trigger, "/drivetrain/reset_odometry", self._reset_odometry)

        try:
            self._load_configuration()
        except Exception as exc:
            self.configuration_error = str(exc)
            self.get_logger().error(
                f"Drivetrain remains disabled: {self.configuration_error}"
            )
        self.control_timer = self.create_timer(0.02, self._control_tick)
        self.diagnostic_timer = self.create_timer(0.5, self._publish_diagnostics)

    def _load_configuration(self) -> None:
        robot_document = load_yaml(self.config_dir / "robot.yaml")
        mapping = load_yaml(self.config_dir / "wheel_mapping.yaml")
        profiles = load_yaml(self.config_dir / "limit_profiles.yaml")
        electrical = load_yaml(self.config_dir / "electrical_limits.yaml")["electrical"]
        robot = robot_document["robot"]
        control = robot_document["control"]
        kinematics = robot_document["kinematics"]
        ratio = require_number(robot, "gear_ratio", positive=True)
        profile = profiles["profiles"][self.profile_name]
        if profile.get("enabled") is not True:
            raise ConfigurationError(f"profile {self.profile_name} is disabled")
        limits = DriveLimits(
            max_linear_mps=require_number(
                profile, "max_linear_velocity_mps", positive=True
            ),
            max_angular_rad_s=require_number(
                profile, "max_angular_velocity_rad_s", positive=True
            ),
            max_wheel_turns_s=require_number(
                profile, "max_wheel_velocity_turns_s", positive=True
            ),
            hardware_velocity_turns_s=require_number(
                profile, "hardware_velocity_limit_turns_s", positive=True
            ),
            acceleration_turns_s2=require_number(
                profile, "max_wheel_acceleration_turns_s2", positive=True
            ),
            deceleration_turns_s2=require_number(
                profile, "max_wheel_deceleration_turns_s2", positive=True
            ),
            motor_current_a=require_number(profile, "max_motor_current_a", positive=True),
            calibration_current_a=require_number(
                profile, "calibration_current_a", positive=True
            ),
            command_timeout_s=require_number(profile, "command_timeout_s", positive=True),
            enable_command_grace_s=require_number(
                profile, "enable_command_grace_s", positive=True
            ),
            idle_after_timeout_s=require_number(
                control, "idle_after_timeout_s", positive=True
            ),
            max_motion_duration_s=require_number(
                profile, "max_motion_duration_s", positive=True
            ),
            minimum_bus_voltage_v=require_number(
                electrical, "dc_undervoltage_v", positive=True
            ),
            maximum_bus_voltage_v=require_number(
                electrical, "dc_overvoltage_v", positive=True
            ),
            bus_monitor_period_s=1.0
            / require_number(electrical, "bus_monitor_rate_hz", positive=True),
        )

        if self.hardware_mode == "bench_2wd":
            selected_names = ("front", "rear")
            radius = require_number(
                robot, "bench_assumed_wheel_radius_m", positive=True
            )
            identities: set[tuple[str, int]] = set()
            for name in selected_names:
                entry = mapping["wheels"][name]
                serial = entry.get("odrive_serial")
                axis = entry.get("axis")
                direction = entry.get("direction")
                if (
                    not isinstance(serial, str)
                    or serial.startswith("REQUIRED_")
                    or axis not in (0, 1)
                    or direction not in (-1, 1)
                    or entry.get("axis_mapping_confirmed") is not True
                    or entry.get("raised_wheel_bench_only") is not True
                ):
                    raise ConfigurationError(f"{name} bench mapping is incomplete")
                identity = (serial.upper(), int(axis))
                if identity in identities:
                    raise ConfigurationError(f"duplicate bench mapping {identity}")
                identities.add(identity)
        elif self.hardware_mode == "four_wheel":
            validate_wheel_mapping(mapping, require_complete=True)
            selected_names = WHEEL_NAMES
            radius = require_number(robot, "effective_wheel_radius_m", positive=True)
        else:
            raise ConfigurationError(f"unknown hardware_mode {self.hardware_mode!r}")

        serials = sorted(
            {
                str(mapping["wheels"][name]["odrive_serial"]).upper()
                for name in selected_names
            }
        )
        for serial in serials:
            device = ODriveDevice(
                serial,
                communication_timeout_s=float(profile["communication_timeout_s"]),
            )
            device.connect(8.0)
            device.validate_power_configuration(
                brake_resistance_ohm=require_number(
                    electrical, "brake_resistor_resistance_ohm", positive=True
                ),
                max_regen_current_a=require_number(
                    profile, "max_regen_current_a"
                ),
            )
            self.devices[serial] = device
        wheels: dict[str, Wheel] = {}
        for name in selected_names:
            entry = mapping["wheels"][name]
            wheels[name] = Wheel(
                name=name,
                device=self.devices[str(entry["odrive_serial"]).upper()],
                axis_number=int(entry["axis"]),
                direction=int(entry["direction"]),
                radius_m=radius,
                gear_ratio=ratio,
                scale=float(kinematics.get(f"{name}_scale", 1.0)),
            )

        if self.hardware_mode == "bench_2wd":
            self.drivetrain = TwoWheelBenchDrive(
                wheels, wheel_radius_m=radius, limits=limits
            )
            self.odometry = None
        else:
            track = require_number(robot, "track_width_m", positive=True)
            sync = profiles["synchronization"]
            scales = {
                "left": float(kinematics["left_velocity_scale"]),
                "right": float(kinematics["right_velocity_scale"]),
            }
            self.drivetrain = Drivetrain(
                wheels,
                wheel_radius_m=radius,
                track_width_m=track,
                limits=limits,
                scales=scales,
                max_side_difference_turns_s=float(
                    sync["max_velocity_difference_rad_s"]
                )
                / (2.0 * math.pi),
                mismatch_warning_s=float(sync["warning_duration_s"]),
                mismatch_fault_s=float(sync["fault_duration_s"]),
            )
            self.odometry = SkidSteerOdometry(
                radius,
                track,
                float(sync["max_velocity_difference_rad_s"]) / (2.0 * math.pi),
            )
        self.drivetrain.initialize()
        self.robot_config = robot_document
        self.configuration_error = None
        self.get_logger().info(
            f"{self.hardware_mode} hardware initialized; explicit enable required. "
            f"Motion is limited to {limits.max_motion_duration_s:.1f} s and "
            f"DC bus monitoring is active at "
            f"{1.0 / limits.bus_monitor_period_s:.1f} Hz."
        )
        if self.hardware_mode == "bench_2wd":
            self.get_logger().warning(
                "Bench front/rear placement and direction signs are provisional; "
                "angular.z is ignored."
            )

    def _cmd_vel(self, message: Twist) -> None:
        if self.drivetrain is None or self.drivetrain.safety.state != DriveState.ENABLED:
            return
        try:
            self.drivetrain.set_command(message.linear.x, message.angular.z)
        except Exception as exc:
            self.get_logger().error(f"Rejected /cmd_vel: {exc}")

    def _enable(self, request: SetBool.Request, response: SetBool.Response) -> SetBool.Response:
        try:
            if not request.data:
                if self.drivetrain:
                    self.drivetrain.disable()
                response.success = True
                response.message = "drivetrain disabled and IDLE"
                return response
            if self.configuration_error:
                raise RuntimeError(self.configuration_error)
            if self.drivetrain is None:
                self._load_configuration()
            assert self.drivetrain is not None
            self.drivetrain.enable()
            response.success = True
            response.message = "drivetrain enabled; watchdog active"
        except Exception as exc:
            response.success = False
            response.message = str(exc)
        return response

    def _disable(self, _request: Trigger.Request, response: Trigger.Response) -> Trigger.Response:
        if self.drivetrain:
            self.drivetrain.disable()
        response.success = True
        response.message = "zero velocity and IDLE requested"
        return response

    def _clear_errors(
        self, _request: Trigger.Request, response: Trigger.Response
    ) -> Trigger.Response:
        if self.drivetrain and self.drivetrain.safety.state == DriveState.ENABLED:
            response.success = False
            response.message = "disable drivetrain before clearing errors"
            return response
        try:
            for device in self.devices.values():
                device.clear_errors_once()
            response.success = True
            response.message = "one explicit clear sent; diagnose cause before re-enable"
        except Exception as exc:
            response.success = False
            response.message = str(exc)
        return response

    def _diagnostics(
        self, _request: Trigger.Request, response: Trigger.Response
    ) -> Trigger.Response:
        self._publish_diagnostics()
        response.success = self.configuration_error is None
        response.message = self.configuration_error or "diagnostics published"
        return response

    def _reset_odometry(
        self, _request: Trigger.Request, response: Trigger.Response
    ) -> Trigger.Response:
        if self.odometry:
            self.odometry.reset()
        response.success = True
        response.message = "odometry reset"
        return response

    def _control_tick(self) -> None:
        if self.drivetrain is None:
            return
        now = time.monotonic()
        try:
            self.last_telemetry = self.drivetrain.step(now)
            self._publish_motion(now - self.last_update)
            self.last_update = now
        except Exception as exc:
            self.get_logger().error(f"Drivetrain faulted and idled: {exc}")

    def _publish_motion(self, dt: float) -> None:
        if not self.last_telemetry:
            return
        names = list(self.last_telemetry)
        positions = [self.last_telemetry[n].position_turns * 2.0 * math.pi for n in names]
        velocities = [
            self.last_telemetry[n].velocity_turns_s * 2.0 * math.pi for n in names
        ]
        joint = JointState()
        joint.header.stamp = self.get_clock().now().to_msg()
        joint.name = [f"{name}_wheel_joint" for name in names]
        joint.position = positions
        joint.velocity = velocities
        self.joints_pub.publish(joint)
        if self.odometry is None:
            return
        try:
            pose, linear, angular = self.odometry.update(
                [
                    self.last_telemetry["front_left"].velocity_turns_s,
                    self.last_telemetry["rear_left"].velocity_turns_s,
                ],
                [
                    self.last_telemetry["front_right"].velocity_turns_s,
                    self.last_telemetry["rear_right"].velocity_turns_s,
                ],
                dt,
            )
        except ValueError as exc:
            self.get_logger().warning(str(exc), throttle_duration_sec=2.0)
            return
        stamp = self.get_clock().now().to_msg()
        odom_cfg = self.robot_config["odometry"]
        qz, qw = _quaternion_from_yaw(pose.yaw)
        msg = Odometry()
        msg.header.stamp = stamp
        msg.header.frame_id = str(odom_cfg["odom_frame"])
        msg.child_frame_id = str(odom_cfg["base_frame"])
        msg.pose.pose.position.x = pose.x
        msg.pose.pose.position.y = pose.y
        msg.pose.pose.orientation.z = qz
        msg.pose.pose.orientation.w = qw
        msg.twist.twist.linear.x = linear
        msg.twist.twist.angular.z = angular
        msg.pose.covariance = _covariance_from_diagonal(
            odom_cfg["position_covariance"]
        )
        msg.twist.covariance = _covariance_from_diagonal(
            odom_cfg["twist_covariance"]
        )
        self.odom_pub.publish(msg)
        if bool(odom_cfg["publish_tf"]):
            transform = TransformStamped()
            transform.header.stamp = stamp
            transform.header.frame_id = msg.header.frame_id
            transform.child_frame_id = msg.child_frame_id
            transform.transform.translation.x = pose.x
            transform.transform.translation.y = pose.y
            transform.transform.rotation.z = qz
            transform.transform.rotation.w = qw
            self.tf_broadcaster.sendTransform(transform)

    def _publish_diagnostics(self) -> None:
        array = DiagnosticArray()
        array.header.stamp = self.get_clock().now().to_msg()
        status = DiagnosticStatus()
        status.name = "odrive_4wd_controller"
        status.hardware_id = ",".join(sorted(self.devices)) or "unconfigured"
        if self.configuration_error:
            status.level = DiagnosticStatus.ERROR
            status.message = "CONFIGURATION_BLOCKED"
            status.values = [KeyValue(key="reason", value=self.configuration_error)]
        elif self.drivetrain:
            state = self.drivetrain.safety.state.value
            status.level = (
                DiagnosticStatus.ERROR
                if self.drivetrain.safety.state in (DriveState.FAULT, DriveState.EMERGENCY_STOP)
                else DiagnosticStatus.OK
            )
            status.message = state
            status.values = [KeyValue(key="state", value=state)]
            for serial, voltage in self.drivetrain.bus_voltages.items():
                status.values.append(
                    KeyValue(key=f"{serial}.dc_bus_voltage_v", value=f"{voltage:.3f}")
                )
            if isinstance(self.drivetrain, TwoWheelBenchDrive):
                status.values.extend(
                    [
                        KeyValue(
                            key="angular_z_ignored",
                            value=str(self.drivetrain.ignored_angular_rad_s),
                        ),
                        KeyValue(
                            key="motion_time_limit_reached",
                            value=str(self.drivetrain.motion_limit_reached),
                        ),
                    ]
                )
            for name, telemetry in self.last_telemetry.items():
                status.values.extend(
                    [
                        KeyValue(key=f"{name}.velocity_turns_s", value=str(telemetry.velocity_turns_s)),
                        KeyValue(key=f"{name}.current_a", value=str(telemetry.current_a)),
                        KeyValue(key=f"{name}.errors", value=str(telemetry.errors)),
                    ]
                )
        array.status = [status]
        self.diagnostics_pub.publish(array)

    def destroy_node(self) -> bool:
        if self.drivetrain:
            self.drivetrain.safe_shutdown()
        for device in self.devices.values():
            device.safe_idle_all()
        return super().destroy_node()


def main(args: list[str] | None = None) -> None:
    rclpy.init(args=args)
    node = ODrive4WDNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            node.destroy_node()
        except KeyboardInterrupt:
            # ros2 launch can deliver a second SIGINT while entities are being
            # destroyed. Hardware cleanup runs before Node.destroy_node().
            pass
        if rclpy.ok():
            rclpy.shutdown()
