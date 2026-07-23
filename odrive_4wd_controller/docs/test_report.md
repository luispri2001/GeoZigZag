# Test report — 2026-07-23

## Hardware results

Primary ODrive `335C33513235`, both axes:

- serial-bound USB discovery: PASS
- firmware/hardware validation: PASS
- configuration persistence after DC power cycle: PASS
- motor and encoder calibration: PASS
- individual low positive/negative motion: PASS
- both axes equal-positive motion: PASS
- differential-sign motion without errors: PASS
- Ctrl+C/finally zero and IDLE behavior: PASS
- maximum observed commissioning current: 0.373 A

The new `scripts/test_single_wheel.py` was also exercised on both current axes:
axis0 reached 0.241 A maximum and axis1 0.288 A maximum. Both produced
positive/negative encoder deltas with the commanded sign, reported zero errors,
and finished IDLE. Raw values are in `hardware_test_20260723.json`.

The temporary bench mapping is M1/axis1 → front-left and M0/axis0 → rear-left,
with provisional direction `+1` for both. This is an operator-approved
raised-wheel assumption, not a completed robot-forward calibration.

ROS 2 `bench_2wd` hardware test:

- launch and serial-bound initialization: PASS
- explicit `/drivetrain/enable`: PASS
- one `linear.x = 0.01 m/s`, `angular.z = 0` command: PASS
- command watchdog and explicit disable: PASS
- `/joint_states` contains exactly `front_left` and `rear_left`: PASS
- diagnostics returned `READY`, zero velocity and zero ODrive errors: PASS
- clean `ros2 launch` SIGINT shutdown: PASS
- post-test axis state: both IDLE, calibrated, encoder-ready, zero errors

An intermediate test set the ODrive hardware velocity limit equal to the
0.05 turn/s software command ceiling. Encoder-estimator transients caused axis0
to report `CONTROLLER_FAILED (0x200)`, `CONTROL_DEADLINE_MISSED (0x10)` and
`OVERSPEED (0x1)`. The fault was recorded before one explicit per-axis clear.
The correction separates the 0.05 turn/s software ceiling from the previously
validated 0.20 turn/s hardware overspeed limit.

The ROS CLI also needed time to create its publisher after `enable`. A
three-second initial grace period now holds a forced-zero setpoint until the
first command; after that first message, the normal 0.30-second watchdog
applies. Old setpoints are explicitly cleared at every enable.

Final reproduction of the operator sequence (`enable`, one-second CLI startup,
then 10 Hz commands) passed. Front-left advanced approximately 0.0027 turns,
rear-left 0.0174 turns, final currents were below 0.31 A, all errors were zero,
and both axes returned to IDLE/READY.

The second ODrive and remaining two motors are not present. Consequently,
four-wheel direction, same-side synchronization, complete drivetrain motion,
dual-USB failure handling and ground odometry are **NOT TESTED**.

## Software tests

Twenty-two unit tests cover:

- differential kinematics and unit conversion
- velocity saturation
- acceleration/deceleration limiting
- command timeout
- mapping uniqueness and confirmation
- odometry integration and side mismatch rejection
- fault state transitions
- explicit enable and guaranteed idle

Hardware-in-the-loop tests must be repeated after the second controller is
connected:

1. Discover both serials concurrently.
2. Identify each wheel and forward sign individually.
3. Test each wheel both directions.
4. Test each side.
5. Test four-wheel forward/reverse/rotations while raised.
6. Validate watchdog, USB removal, one-axis fault and E-stop.
7. Power-cycle both controllers.
8. Only then begin low-speed ground tests and odometry calibration.
