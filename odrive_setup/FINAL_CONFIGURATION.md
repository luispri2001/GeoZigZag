# Final configuration record

Status: **commissioning and power-cycle validation passed**

Current runtime update (2026-07-23): the ROS bench driver now applies 1.0 A
motor current, 1.5 A calibration current, 0.20 turn/s hardware velocity,
0.15 turn/s software velocity, and 0.15 turn/s² acceleration/deceleration.
The earlier 2 A values below describe the archived commissioning export and
historical tests, not the current runtime limits.

## Controller

- Model: ODrive v3.6 dual-axis, 56 V variant
- Serial: `335C33513235`
- Firmware: 0.5.1, unreleased/custom flag set
- Original or compatible/clone: unresolved; USB descriptor says ODrive Robotics
- Measured DC bus: 41.73–41.81 V
- Brake resistor: configured and armed at 2 Ω; operator confirmed safety setup
- DC trips: 8.0 V undervoltage, 59.92 V overvoltage
- Original export: `original-335C33513235-20260723T110941Z.json`
- Interim export: `safety-interim-335C33513235-20260723T111049Z.json`
- Working export: `working-safe-335C33513235-20260723T113547Z.json`

The inspected WILDBOAR `odrive` branch contains host-side ROS 2 CANSimple code,
not an ODrive firmware image. It targets firmware 0.5.1, which is already
installed. No firmware was flashed.

## Motors and encoders

Both are ZLTECH 6.5-inch robot hub motors with built-in incremental magnetic
quadrature encoders. The exact voltage/SKU suffix is not visible. The existing
configuration, colleague files, and powered tests agree on:

- PMSM current control
- 15 pole pairs
- 4096 encoder lines / 16384 quadrature counts per revolution
- nominal current reported for the matched motor family: 6.5 A
- conservative commissioning ceiling actually used: 2.0 A

The unresolved voltage/SKU suffix is not guessed. Do not raise the limits
without reading the physical label or matching datasheet.

## Axis 0 / physical M0

- Phase resistance / inductance: 0.397511 Ω / 0.840055 mH
- Current / calibration current: 2.0 A / 2.0 A
- Resistance calibration maximum voltage: 2.0 V
- Velocity limit / ramp: 0.2 turn/s / 0.1 turn/s²
- Control / input: velocity / velocity ramp
- Startup calibration, index search, offset calibration, closed-loop: all false
- Motor pre-calibrated: true
- Encoder pre-calibrated: false
- Current-session encoder ready: true
- Independent +0.03 / −0.03 turn/s pulses: PASS
- Maximum observed current: 0.373 A
- Final errors: axis/motor/encoder/controller all zero

## Axis 1 / physical M1

- Phase resistance / inductance: 0.395728 Ω / 0.827940 mH
- Current / calibration current: 2.0 A / 2.0 A
- Resistance calibration maximum voltage: 2.0 V
- Velocity limit / ramp: 0.2 turn/s / 0.1 turn/s²
- Control / input: velocity / velocity ramp
- Startup calibration, index search, offset calibration, closed-loop: all false
- Motor pre-calibrated: true
- Encoder pre-calibrated: false
- Current-session encoder ready: true
- Independent +0.03 / −0.03 turn/s pulses: PASS
- Maximum observed current: 0.343 A
- Final errors: axis/motor/encoder/controller all zero

## Mapping and combined tests

Manual correspondence is unambiguous: M0 changes only axis0, and M1 changes
only axis1. Both axes passed an equal-positive simultaneous pulse at +0.02
turn/s. They also passed the safety/error portion of a differential pulse
(axis0 +0.02, axis1 −0.02); axis1 moved negatively, while axis0 only registered
a small velocity and did not overcome static friction during that extremely
small pulse. Axis0 had already demonstrated both directions independently.

All test exits commanded zero velocity and returned both axes to IDLE. Detailed
measurements are in `test_results-20260723.json`.

## Persistence policy

Motor calibration is retained. Encoder offset calibration is intentionally not
claimed as persistent because these incremental encoders have no index. After
each main-power cycle, run `calibrate_encoders.py` before `control_odrive.py`.
Automatic closed-loop startup remains disabled.

## Power-cycle validation

Main DC power was removed, restored, and the controller rediscovered over USB.
The serial, firmware, 2 A current limits, 0.2 turn/s velocity limits, velocity
control, brake-resistor setting, motor calibration, and zero-error state all
persisted. As designed, encoder-ready reset to false. Running the supplied
one-axis-at-a-time calibration restored both encoders without errors.

A final simultaneous +0.02 turn/s, 0.8-second pulse then passed. Maximum
measured current was 0.237 A on axis0 and 0.249 A on axis1. Both axes ended
IDLE, encoder-ready, and with all error fields zero.
