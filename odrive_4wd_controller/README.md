# ODrive four-wheel skid-steer controller

ROS 2 Humble and standalone control stack for four independently driven wheels
on two dual-axis ODrive controllers. Controllers are always bound by permanent
ODrive serial number. USB discovery order and `/dev/ttyUSB*` names are never
used.

## Current commissioning status

The software supports two controllers and four wheels, but only one controller
is physically present:

| Controller | Serial | Hardware | Firmware | Axes | Status |
|---|---|---|---|---:|---|
| Primary | `335C33513235` | ODrive v3.6, 56 V variant | 0.5.1 | 2 | Connected and bench-tested |
| Secondary | `REQUIRED_SECOND_ODRIVE_SERIAL` | Required | Required | 2 | Not connected |

The current M0 → axis0 and M1 → axis1 correspondence is verified. For temporary
raised-wheel testing, the operator has assigned M1/axis1 to `front_left` and
M0/axis0 to `rear_left`, both with provisional controller-positive direction.
`bench_test.launch.py` can use these two wheels. The full four-wheel mapping
remains incomplete and normal 4WD mode cannot enable.

## Safety model

- Explicit enable is required.
- Every axis is checked for calibration and errors before closed loop.
- Hardware limits are applied before enabling.
- Commands are finite-checked, clamped, converted, wheel-clamped and slew-limited.
- A stale `/cmd_vel` ramps to zero and then puts every axis in IDLE.
- USB loss or any axis/encoder/controller error faults and idles all available axes.
- Reconnection never resumes movement automatically.
- `try/finally`, shutdown hooks and signal handling command zero and IDLE.
- Error clearing is an explicit service and is never repeated automatically.

The physical emergency stop remains the authoritative emergency mechanism.
Software stopping does not electrically isolate the battery or power stage.

## Build

```bash
cd /home/robotica/GeoZigZag
source /opt/ros/humble/setup.bash
colcon build --base-paths odrive_4wd_controller \
  --packages-select odrive_4wd_controller
source install/setup.bash
```

Run unit tests without hardware:

```bash
pytest -q odrive_4wd_controller/test
```

## Current two-motor bench operation

Discover the connected controller without movement:

```bash
python3 odrive_4wd_controller/scripts/odrive_discovery.py \
  --serial 335C33513235
```

After each main-power cycle, calibrate the incremental encoder offsets:

```bash
python3 /home/robotica/odrive_setup/calibrate_encoders.py all \
  --safety-file /home/robotica/odrive_setup/hardware_identification.json \
  --serial 335C33513235 \
  --confirm-lifted-and-clear
```

Test one axis, bounded to ±0.02 turn/s, 2 A and one second maximum:

```bash
python3 odrive_4wd_controller/scripts/test_single_wheel.py \
  --serial 335C33513235 --axis 0 \
  --confirm-lifted-and-clear
```

The legacy interactive command also works from the home directory because
`/home/robotica/odrive_setup` is a stable link to the repository folder:

```bash
cd /home/robotica
python3 odrive_setup/control_odrive.py \
  --safety-file odrive_setup/hardware_identification.json \
  --serial 335C33513235 \
  --max-velocity 0.2 --max-acceleration 0.1 \
  --max-configured-current 2
```

Inside the prompt, `stop`, then `idle`, then `quit`. `Ctrl+C` also invokes the
zero-and-IDLE cleanup.

## Complete the four-wheel mapping

Connect the second ODrive and record its serial:

```bash
python3 odrive_4wd_controller/scripts/odrive_discovery.py \
  --serial 335C33513235 --serial SECOND_SERIAL
```

With all wheels raised, identify one controller at a time:

```bash
python3 odrive_4wd_controller/scripts/identify_wheels.py \
  --serial 335C33513235 \
  --output /tmp/primary-wheel-map.yaml \
  --confirm-lifted-and-clear
```

Repeat for the second serial, merge only the physically confirmed entries into
`config/wheel_mapping.yaml`, and set `mapping_status: COMPLETE`. Never infer a
corner or direction from motor wiring.

Measure effective wheel radius under load and track width between left/right
contact lines, then replace the corresponding `REQUIRED_MEASUREMENT` fields in
`config/robot.yaml`.

## ROS 2 operation

The two-wheel raised-bench launch connects only the primary ODrive. It accepts
linear `/cmd_vel`, rejects angular velocity, publishes two wheel joints, and
does not publish odometry or TF. The wheel-speed ceiling is 0.05 turn/s and
motor phase current remains limited to 2 A:

```bash
ros2 launch odrive_4wd_controller bench_test.launch.py
```

Enable and send one short command:

```bash
ros2 service call /drivetrain/enable std_srvs/srv/SetBool '{data: true}'
ros2 topic pub -r 10 /cmd_vel geometry_msgs/msg/Twist \
  '{linear: {x: 0.03}, angular: {z: 0.0}}'
ros2 service call /drivetrain/disable std_srvs/srv/Trigger '{}'
```

Use `Ctrl+C`, not `Ctrl+X`, to stop the publisher. A publication rate above
3.4 Hz is required by the 0.30-second watchdog; 10 Hz is recommended.
After enable, the controller allows three seconds for the first message while
holding an explicit zero setpoint. It never reuses a command from an earlier
enable cycle.

After the complete mapping, geometry and four-wheel bench tests pass, use the
normal launch:

```bash
ros2 launch odrive_4wd_controller odrive_4wd.launch.py \
  config_dir:=/home/robotica/GeoZigZag/odrive_4wd_controller/config \
  limit_profile:=normal_operation
ros2 service call /drivetrain/enable std_srvs/srv/SetBool '{data: true}'
ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist \
  '{linear: {x: 0.03}, angular: {z: 0.0}}'
ros2 service call /drivetrain/disable std_srvs/srv/Trigger '{}'
```

Topics:

| Topic | Type | Direction |
|---|---|---|
| `/cmd_vel` | `geometry_msgs/Twist` | subscription |
| `/odom` | `nav_msgs/Odometry` | publication |
| `/joint_states` | `sensor_msgs/JointState` | publication |
| `/diagnostics` | `diagnostic_msgs/DiagnosticArray` | publication |
| `/tf` | `odom` → `base_link` | configurable publication |

Services:

| Service | Type | Purpose |
|---|---|---|
| `/drivetrain/enable` | `std_srvs/SetBool` | explicit enable or disable |
| `/drivetrain/disable` | `std_srvs/Trigger` | zero and IDLE |
| `/drivetrain/idle` | `std_srvs/Trigger` | zero and IDLE |
| `/drivetrain/clear_recoverable_errors` | `std_srvs/Trigger` | one explicit clear |
| `/drivetrain/run_diagnostics` | `std_srvs/Trigger` | publish current state |
| `/drivetrain/reset_odometry` | `std_srvs/Trigger` | reset x/y/yaw |

## Kinematics and units

Firmware 0.5.1 velocity input is motor turns per second:

```text
v_left  = v - omega * track_width / 2
v_right = v + omega * track_width / 2
wheel_turns_s = side_linear_velocity / (2*pi*effective_wheel_radius)
motor_turns_s = wheel_turns_s * gear_ratio
```

`Wheel.direction` makes a positive logical wheel velocity mean robot-forward.
The wheel object applies direction and gearing exactly once. The calculation
tool refuses unresolved geometry:

```bash
python3 odrive_4wd_controller/scripts/calculate_limits.py
```

## Odometry

Front and rear encoder velocities are averaged per side only when they agree
within the configured threshold. One invalid wheel can be rejected; two invalid
measurements on one side stop integration. Skid-steer wheel odometry accumulates
substantial error during turns and on loose soil. The output is prepared for
future `robot_localization` fusion, but no IMU or GNSS data is silently fused.

## Regenerative braking

The detected controller uses a configured 2 Ω brake resistor and a 59.92 V
overvoltage trip. Its resistor power rating and battery charge-acceptance limit
are unresolved. Therefore the bench profile uses gentle 0.05 turn/s²
acceleration, 0.10 turn/s² deceleration, and zero assumed regenerative battery
current. Do not enable higher profiles until resistor power, battery limits,
fuses and cabling are documented.

Motor phase current produces torque and is not the same as DC battery current.
During acceleration, phase current can exceed battery current; during braking,
mechanical energy can raise DC-bus voltage even while the command approaches
zero.

## USB production suitability

USB is suitable for controlled development but vulnerable to connector
vibration, electrical noise, ground loops and cable removal. This implementation
mitigates enumeration changes using serial binding and faults on communication
loss, but USB cannot guarantee a stop after the cable is physically removed.
For field deployment, migrate both v3.6 controllers to isolated CAN with unique
node IDs, verified termination, heartbeat monitoring and the same safety state
machine. The migration must not reuse the WILDBOAR four-node assumptions until
the real two-controller mapping is confirmed.

See [fault handling](docs/fault_handling.md),
[parameters](docs/parameter_reference.md), and
[test status](docs/test_report.md).
