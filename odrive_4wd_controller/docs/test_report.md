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

The second ODrive and remaining two motors are not present. Consequently,
four-wheel direction, same-side synchronization, complete drivetrain motion,
dual-USB failure handling and ground odometry are **NOT TESTED**.

## Software tests

Unit tests cover:

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
