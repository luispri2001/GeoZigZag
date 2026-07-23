# Safety review of colleague scripts

Source archive:
`/home/robotica/Downloads/Odrive config for zltech motors.tar.gz`

SHA-256:
`575fb05f2f9ca8b0ab9a8f39843d9400bfbf7fdf10575f984a6f229395c9fe40`

Status: **reference only — do not execute on the connected robot**

## Configuration evidence

`config.py` matches the configuration found on serial `335C33513235`:

- 15 motor pole pairs
- incremental encoder, 16384 CPR
- 16 A current limit
- 5 A calibration current
- 12 V resistance-calibration ceiling
- 3.4 turn/s velocity limit
- position/velocity gains 20, 0.08, and 0.16

This is useful provenance but does not prove that the physical motor/encoder
wiring currently corresponds to axis 0 and axis 1.

## Critical findings

### `calibrate.py`

- `calibration_ok` is initialized to `True` and is never set from the reported
  errors or calibration flags.
- It can therefore set both `motor.config.pre_calibrated` and
  `encoder.config.pre_calibrated` even after a failed calibration.
- The `all` option calibrates both axes concurrently.
- The wait loop has no timeout.
- Broad exception handling can hide save/disconnect failures.
- There is no `try/finally` safety path for interruption.

### `mover_ruedas.py`

- It requests closed-loop while the saved controller mode is position control,
  then changes to velocity control afterward. This ordering can cause a
  position transient.
- It does not check active errors, motor calibration, or encoder readiness.
- It commands 1.0 turn/s with the saved 16 A current limit.
- It controls only axis 0.
- It has no velocity ramp, timeout, watchdog, Ctrl+C handler, or `try/finally`.
- Closing the window does not explicitly return the axis to IDLE.
- An exception while `W` is held can leave the last non-zero setpoint active.

### `config.py`

- It writes relatively high first-test limits without validating motor or
  power-supply ratings.
- It does not explicitly configure motor type, brake resistor, DC limits,
  control mode, watchdog, or encoder direction.
- Reboot errors are silently ignored.

### `temp.py`

This script is read-only and is the only supplied script that is suitable for
inspection, although the existing `diagnose_odrive.py` provides more complete
and version-aware diagnostics.

## Required replacement behavior

Use the guarded tools in the parent directory. Before powered calibration:

1. Trace the three phase wires from M0 and M1 to label the physical wheels.
2. Rotate each labelled wheel manually and prove that only its matching encoder
   count changes.
3. Keep the other axis IDLE.
4. Use a bounded one-axis calibration with conservative limits and verify every
   error before setting any pre-calibrated flag.
5. Use explicit zero command and IDLE in a `finally` block.
