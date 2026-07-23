# Parameter reference

Every `REQUIRED_MEASUREMENT` or `REQUIRED_DATASHEET_VALUE` blocks the operation
that depends on it. Raw measurements and empirical correction factors remain
separate.

| Parameter | Current value | Unit | Source | Safety margin / effect |
|---|---:|---|---|---|
| `max_wheel_velocity_turns_s` | 0.15 | turn/s | User-specified initial two-axis limit; raised-wheel HIL passed | Below independent 0.20 turn/s hardware ceiling |
| `hardware_velocity_limit_turns_s` | 0.20 | turn/s | User-specified initial limit and ODrive verification | Independent overspeed protection |
| `max_wheel_acceleration_turns_s2` | 0.15 | turn/s² | User-specified initial two-axis limit | Software slew and ODrive ramp |
| `max_wheel_deceleration_turns_s2` | 0.15 | turn/s² | User-specified initial two-axis limit | Forced zero crossing before reversal |
| `max_motor_current_a` | 1.0 | phase A | User-specified initial limit | Hardware enforced; motor rated current remains unknown |
| `calibration_current_a` | 1.5 | phase A | User-specified initial limit | Written to each axis configuration |
| `max_linear_velocity_mps` | 0.080 | m/s | Derived from 0.15 turn/s and candidate 0.085 m radius | Bench-only until effective radius is measured |
| `max_angular_velocity_rad_s` | ignored | rad/s | Only one robot side is connected | `angular.z` is recorded in diagnostics but not applied |
| `max_motion_duration_s` | 3.0 | s | User-specified initial two-axis limit | Latches stop until explicit re-enable |
| `command_timeout_s` | 0.30 | s | Safety design | Software watchdog |
| `enable_command_grace_s` | 3.0 | s | Allows ROS CLI publisher startup while setpoint is forced to zero | Applies only before the first command |
| `idle_after_timeout_s` | 1.0 | s | Safety design | Software stop then hardware IDLE |
| `dc_undervoltage_v` | 8.0 | V | Existing ODrive configuration | Hardware enforced |
| `dc_overvoltage_v` | 59.92 | V | Existing ODrive configuration | Hardware enforced |
| `bus_monitor_rate_hz` | 10.0 | Hz | Runtime safety design | Checked and published continuously while the node runs |
| `brake_resistor_resistance_ohm` | 2.0 | Ω | Verified with `odrivetool` | Driver checks exact value and armed state at startup |
| `encoder.cpr` | 16384 | counts/rev | Existing config and colleague file | Verified by calibration/feedback |
| `pole_pairs` | 15 | pairs | Existing candidate configuration | Exact motor SKU/datasheet still required |

Correction-factor measurement:

- Effective radius: command a measured straight distance; multiply the old
  effective radius by `actual_distance / reported_distance`.
- Track-width/skid correction: command a known rotation; multiply effective
  track width by `reported_yaw / actual_yaw`, then refine skid correction on the
  intended terrain.
- Left/right scale: drive straight over a measured course and apply only the
  smallest scale change needed to remove repeatable yaw drift.
- Front/rear scale: use logged encoder velocity under equal unloaded commands;
  correct mechanical causes before applying software scale.

Restart the node after YAML changes. Never tune direction using a scale: use
only the confirmed `direction` field (`-1` or `1`).
