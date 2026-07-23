# Command log

Date: 2026-07-23 (Europe/Madrid)

Only inspection commands were executed. No CAN interface was brought up, no CAN
frame was sent, no ODrive configuration was written, no calibration was run,
and no movement was commanded.

```text
uname -a
cat /etc/os-release
python3 --version
lsusb
python3 -m pip show odrive fibre
command -v odrivetool
command -v dfu-util
id
find /etc/udev/rules.d /usr/lib/udev/rules.d ... '*odrive*' '*1209*'
ls -l /dev/bus/usb/*/*
journalctl -k -n 120 --no-pager | rg -i 'usb|odrive|1209|dfu'
timeout 20s odrivetool
ip -details -statistics link show
ps -ef | rg -i 'odrive|candump|cansend|slcan'
lsmod | rg '^(gs_usb|can|can_raw|slcan)'
lsusb -v -d 1d50:606f
journalctl -k -b --no-pager | rg -i '1209|0483|odrive|STM32|DFU|usb'
lsusb -t
python3 -m pip show python-can
odrivetool --help
odrivetool backup-config --help
curl -fsSL https://cdn.odriverobotics.com/files/odrive-udev-rules.rules
sha256sum <downloaded-rule>
sudo -n true
```

Observed official udev rule SHA-256:
`b574486e0fcdf13f2faa7165f5634d466353274547ab4d3a36b75f67c7124d3c`.

`sudo -n true` failed because an interactive password is required. The rule was
therefore not installed in `/etc/udev/rules.d`.

## Follow-up after photographs

The user installed the supplied rule interactively; it was confirmed at
`/etc/udev/rules.d/91-odrive.rules`. Three photographs were inspected. They show
an energized ODrive v3.x-form-factor dual-axis board, two ZLTECH hub motors with
built-in encoders, and a Makerbase CANable V2.0 Pro connected between the
computer and the board's CAN pins. The board's native Micro-USB port is not
connected and no exact board or motor model label is visible.

Additional inspection commands:

```text
lsusb
ip -details -statistics link show can0
ls -l /etc/udev/rules.d/91-odrive.rules
journalctl -k -n 50 --no-pager | rg -i 'usb|can|gs_usb|odrive'
timeout 5s odrivetool --no-usb --can can0 shell
sudo -n true
```

`can0` remained `DOWN/STOPPED`. No CAN frames were sent.

## Native USB identification and backup

The native Micro-USB connection enumerated as `1209:0d32`, product
`ODrive 3.6 CDC Interface`, serial `335C33513235`. The official backup command
created:

```text
odrive_setup/configuration_exports/original-335C33513235-20260723T110941Z.json
SHA-256 046f7dab52a3d41cfcc6f6d56d8eecc0b104dd36e46aa2a48f81f351f8ad4835
```

The read-only diagnostic reported hardware 3.6 variant 56, firmware 0.5.1 with
the unreleased flag set, and a 41.73–41.81 V DC bus. Both axes were IDLE with
`AxisError.INVALID_STATE`, no motor/encoder/controller sub-errors, motor
calibrated, and encoder not ready.

The original configuration had `startup_closed_loop_control=true` on both
axes. After verifying IDLE state and zero measured current, only these two flags
were set to false and saved. A second export was created:

```text
odrive_setup/configuration_exports/safety-interim-335C33513235-20260723T111049Z.json
SHA-256 e4bbd4c836b796cfc041cd0a766b917526353075de733fe6d1cf4055c8332e9a
```

A recursive JSON comparison confirmed that the two startup flags are the only
differences. A 30-second stationary sample (579 observations per axis) found
zero measured current. Axis 0 position span was 0.000140518 turn with maximum
absolute velocity estimate 0.00755310 turn/s; axis 1 position span and velocity
were both zero. Manual encoder correspondence has not yet been tested.

Two subsequent read-only tests requested manual rotation of the M0-connected
wheel for 20 seconds and the M1-connected wheel for 25 seconds. Neither encoder
changed by one count: axis 0 stayed at shadow count 2304 and axis 1 at -1014.
Both axes remained IDLE. Operator confirmation of whether the wheels were
physically rotated is pending; no calibration or powered movement was attempted.

After coordinating the timing with the operator, an M1-only window changed
axis1 by 216 counts while axis0 remained fixed. A separate M0-only retest left
axis0 fixed but changed axis1 across a 15,308-count span. This fails the required
motor-to-encoder correspondence check and indicates crossed/misidentified
encoder wiring. Both axes remained IDLE; no powered movement was attempted.

The operator later reported that the wrong physical wheels were probably
rotated, so the apparent crossing is recorded as inconclusive. The supplied
archive `Odrive config for zltech motors.tar.gz` (SHA-256
`575fb05f2f9ca8b0ab9a8f39843d9400bfbf7fdf10575f984a6f229395c9fe40`)
was checked for unsafe paths and extracted for review under
`reference_from_colleague/`. None of its Python files were executed.

A separate read-only query found stored phase resistance/inductance values of
0.397511 Ω / 0.840055 mH for axis0 and 0.395728 Ω / 0.827940 mH for axis1.
The close agreement supports that both configured motors are the same type, but
does not replace the physical axis-correspondence test.

## WILDBOAR firmware repository inspection

The local `wildboar_jetson_firmware` checkout was found under
`/home/robotica/wildboar_doc/wildboar_ws/src`. Remote refs were fetched without
checking out or merging them. Remote `main` was
`43101f8c03373879408ca0ff3da7a92b8126de1c`; branch `odrive` was
`3fa71b56276329a71aaf27b35f3b76692d646a80`.

The `odrive` branch contains no ODrive firmware image or source build. It is a
ROS 2 CANSimple controller explicitly adapted to firmware 0.5.1, already
installed on the connected board. It assumes two boards/four motors, auto-init,
and node IDs 0–3. No firmware was flashed and no branch was checked out.

## Verified mapping, calibration, and movement

After the operator corrected the physical wheel identification, manual encoder
tests established M0 → axis0 (+5914 counts with axis1 unchanged) and M1 →
axis1 (9379-count span / −8045 net counts with axis0 unchanged).

The operator explicitly confirmed that the safety setup was complete. Both
wheels remained lifted. Conservative settings were applied to each axis:

```text
current_lim = 2.0 A
calibration_current = 2.0 A
resistance_calib_max_voltage = 2.0 V
vel_limit = 0.2 turn/s
vel_ramp_rate = 0.1 turn/s²
control_mode = VELOCITY_CONTROL
input_mode = VEL_RAMP
all startup actions = false
motor.pre_calibrated = true
encoder.pre_calibrated = false
```

Encoder offset calibration state 7 ran one axis at a time. Axis0 travelled
66164 counts and axis1 65810 counts; both completed ready with all error fields
zero. No full motor calibration was needed because the matching stored motor
calibration was already valid.

Each axis then received separate short +0.03 and −0.03 turn/s pulses, followed
by zero and IDLE. Both passed without errors; maximum measured Iq was 0.373 A.
A simultaneous +0.02/+0.02 turn/s test also passed. A differential
+0.02/−0.02 test had no errors and maximum Iq 0.236 A; axis1 moved negatively,
while axis0 did not overcome static friction during that extremely small pulse.
Axis0 had already passed both directions independently. Every powered test used
a `finally` block to send zero and IDLE to both axes.

The working configuration was saved and exported:

```text
odrive_setup/configuration_exports/working-safe-335C33513235-20260723T113547Z.json
SHA-256 4c2f08fcb28b09a3779f53a7bf9e233f6026fb2e99dbd25ca9e8f4fa6a9e1398
```

After save/reconnect, both axes were IDLE, both encoders remained ready in the
current powered session, all errors were zero, and the conservative limits
persisted. Main-power-cycle validation is still pending.

## Main-power-cycle validation

The operator removed and restored main DC power. A fresh read-only diagnostic
at approximately 41.79 V confirmed that serial, firmware, brake resistor,
2 A current limits, 0.2 turn/s velocity limits, velocity-control configuration,
motor calibration, IDLE state, and zero errors persisted. Encoder readiness
reset to false, as expected for the intentionally non-pre-calibrated incremental
encoders.

`calibrate_encoders.py all --confirm-lifted-and-clear` then calibrated axis0
and axis1 sequentially. Both completed ready with no errors and the script
returned both axes to IDLE. A final simultaneous +0.02 turn/s pulse for 0.8 s
passed after reconnection. Maximum measured Iq was 0.237 A on axis0 and
0.249 A on axis1. Both finished IDLE, encoder-ready, and error-free.
