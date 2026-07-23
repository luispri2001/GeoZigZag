# Safe ODrive bring-up

From `/home/robotica`, the relative command path is supported by the symlink
`/home/robotica/odrive_setup -> /home/robotica/GeoZigZag/odrive_setup`.

This directory contains the host-side tooling and evidence for a cautious
two-motor ODrive commissioning process. Both axes have now passed conservative
calibration and low-speed tests. Native USB identifies an ODrive v3.6 dual-axis
56 V variant, serial `335C33513235`, running firmware 0.5.1 with the unreleased
flag set. Configuration persistence and operation after a physical main-power
cycle have also been validated.

Keep wheels or loads lifted and have the physical emergency stop ready whenever
calibrating or commanding motion. The exact ZLTECH motor voltage/SKU suffix was
not visible and remains explicitly unresolved. Earlier commissioning used the
existing 15-pole-pair/16384-CPR candidate configuration with a 2 A ceiling; the
exact model is not proven by those successful tests. The current runtime
ceiling is 1 A per axis, with 1.5 A calibration current.

## Current host findings

- Ubuntu 22.04.5 LTS, kernel 6.8.0-134-generic, Python 3.10.12.
- User-installed `odrive`/`odrivetool` 0.6.11.post0 is present.
- Native USB device `1209:0d32` is ODrive v3.6 serial `335C33513235`.
- The earlier USB device `1d50:606f` was a CANable 2.5 Candlelight adapter,
  serial `208131A7523050072`; it was unplugged to use native USB.
- The ODrive reports a 41.73–41.81 V DC bus.
- The official ODrive udev rule is installed. A downloaded, inspected copy is
  included as `91-odrive.rules`; its SHA-256 is recorded in `COMMAND_LOG.md`.
- `can-utils`, `python-can`, and `dfu-util` were not detected.

The USB descriptor and API identify the hardware family and variant. They do
not prove whether the physical board is original or a compatible clone.

Photographs added on 2026-07-23 show an energized ODrive v3.x-form-factor
dual-axis board and two ZLTECH 6.5-inch robot hub motors with built-in encoders.
The colleague configuration and successful correspondence tests validate 15
pole pairs and a 4096-line quadrature encoder (16384 counts/revolution). The
exact motor voltage/SKU suffix and board originality are not visible. The cable
from the computer is connected to the board's native Micro-USB port.

The original configuration is preserved in
`configuration_exports/original-335C33513235-20260723T110941Z.json`. A safety
change disabled automatic closed-loop startup on both axes, and the resulting
configuration is preserved as
`configuration_exports/safety-interim-335C33513235-20260723T111049Z.json`.
Recursive comparison confirmed these are the only changed fields.

The earlier tested conservative configuration is preserved as
`configuration_exports/working-safe-335C33513235-20260723T113547Z.json`.
It records the earlier 2 A commissioning setup. The current ROS bench driver
reapplies 1 A, 1.5 A calibration current, 0.2 turn/s hardware velocity limit,
and a 0.15 turn/s² ramp at every initialization.

## Safe connection and power-up

1. Mechanically lift both wheels/loads and make the work area clear.
2. Switch off and isolate the main DC supply.
3. Photograph/read the controller model, hardware revision, motor labels,
   encoder labels, brake resistor, and all terminal wiring.
4. Verify DC polarity and supply voltage with a meter before energizing.
5. Verify encoder supply voltage and pinout from its datasheet.
6. Confirm CAN-H/CAN-L/GND and termination if using CAN. Do not guess bitrate.
7. Connect native USB directly, without an unverified hub or charge-only cable.
8. Apply main DC power only according to the identified board manual. USB power
   alone is not sufficient evidence that the motor power stage is correctly
   powered.
9. Run `lsusb` and the read-only diagnostic. Do not proceed if identity differs
   from the inspected label.

The exact required power-up order remains unresolved until the model is known;
follow that model's official manual, not a generic sequence.

## Host setup

Install the inspected udev rule (requires Luis's sudo password):

```bash
sudo install -m 0644 odrive_setup/91-odrive.rules /etc/udev/rules.d/91-odrive.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Prefer an isolated environment, but do not select an ODrive package version
until hardware and firmware are read. For the currently installed tool only:

```bash
python3 -m venv odrive_setup/.venv
odrive_setup/.venv/bin/python -m pip install --upgrade pip
odrive_setup/.venv/bin/python -m pip install 'odrive==0.6.11.post0'
```

This version pin documents the host's current package; it is not yet a firmware
compatibility decision.

## Read-only diagnosis

Native USB:

```bash
python3 odrive_setup/diagnose_odrive.py --interface usb --timeout 15
```

After a serial is identified, save a new timestamped report:

```bash
stamp=$(date -u +%Y%m%dT%H%M%SZ)
python3 odrive_setup/diagnose_odrive.py \
  --interface usb --serial SERIAL \
  --output "odrive_setup/diagnostics-${SERIAL}-${stamp}.json"
```

The diagnostic reads identity, firmware, DC voltage, brake/DC-bus settings,
axis states, errors, calibration flags, position/velocity, and current estimates
where the connected firmware exposes them. `<unavailable>` is intentional: it
means the API or hardware did not expose the value, and must not be guessed.

Before any write, export the original configuration:

```bash
stamp=$(date -u +%Y%m%dT%H%M%SZ)
odrivetool -s SERIAL backup-config \
  "odrive_setup/configuration_exports/original-${SERIAL}-${stamp}.json"
```

Copy the backup off the robot before continuing.

## Stationary encoder and wiring validation

With both axes IDLE and no calibration running:

1. Capture stationary position readings for at least 30 seconds. They must not
   drift or jump.
2. Slowly rotate motor 0 by hand. Only axis 0 position may change, smoothly.
3. Stop it, then slowly rotate motor 1. Only axis 1 may change, smoothly.
4. Record sign, counts/turn, noise, cross-axis changes, and every error.

Stop if readings are unstable, the wrong encoder changes, or an axis leaves
IDLE. The current tools deliberately do not automate this physical validation.

## Calibration after power-up

These incremental encoders have no index and `encoder.pre_calibrated` is
deliberately false. After each main-power cycle, calibrate their offsets before
using closed-loop control. This moves one wheel at a time:

```bash
python3 odrive_setup/calibrate_encoders.py all \
  --safety-file odrive_setup/hardware_identification.json \
  --serial 335C33513235 \
  --confirm-lifted-and-clear
```

The script refuses to start without the explicit safety confirmation, keeps the
other axis IDLE, uses a 1 A motor ceiling and 1.5 A calibration current, has a
timeout, checks every error, and sends zero plus IDLE to both axes in `finally`.
It does not mark the incremental encoders permanently pre-calibrated.

## Explicit, low-speed runtime control

`control_odrive.py` is intentionally restricted to a verified legacy dual-axis
device. It refuses to arm unless:

- every physical safety flag and identification field is filled;
- connected serial matches `hardware_identification.json`;
- connected hardware and firmware version triplets exactly match the manifest
  (for example `3.6.0`, never a guessed marketing name);
- both legacy calibration flags are true and there are no active errors;
- existing controller current/velocity limits are already conservative;
- velocity control and a supported input mode are already configured.

Launching it does not move or arm either motor:

```bash
python3 odrive_setup/control_odrive.py \
  --safety-file odrive_setup/hardware_identification.json \
  --serial SERIAL \
  --max-velocity 0.20 \
  --max-acceleration 0.20 \
  --max-configured-current 1
```

Interactive commands:

```text
status
arm axis0
vel axis0 0.05
stop axis0
vel axis0 -0.05
stop axis0
idle axis0
```

Repeat independently for axis 1 only after axis 0 is proven safe. For a
verified differential drive, arm both and issue separate low commands. There is
no automatic motion test. `Ctrl+C`, EOF, `quit`, normal exceptions, and
termination signals run a `finally` path that requests zero velocity and IDLE
for both axes. A host command cannot replace a hardware emergency stop.

## Stop procedure

In the control prompt:

```text
stop
idle
quit
```

For abnormal behavior use the physical emergency stop/main power isolation
immediately. Do not rely on USB latency or a Python process during a hazard.

## Restore and firmware recovery

Restore only to the same identified controller/firmware family, after inspecting
the JSON and preserving both original and working backups:

```bash
odrivetool -s SERIAL restore-config path/to/original-SERIAL-TIMESTAMP.json
```

Firmware update is not authorized by the current evidence. If later required,
use only the official procedure and exact detected hardware target, after the
original backup. Never flash modern Pro/S1/Micro firmware onto ODrive v3.x.

DFU/bootloader recovery is model-specific. First confirm `0483:df11` with
`lsusb`, preserve the serial and logs, then follow the official page for the
identified hardware. Do not run `odrivetool dfu`, `legacy-dfu`, bootloader
installation, mass erase, or unlock speculatively.

## Power-cycle validation

Completion requires all of the following, with evidence recorded in new files:

1. Original and working configuration exports differ only as intended.
2. Configuration and calibration flags survive main-power cycling.
3. Native USB or the verified CAN link reconnects reliably.
4. Diagnostics work after USB disconnect/reconnect.
5. Each axis runs positive/stop/negative/stop/IDLE independently at low speed.
6. Both axes run together at low speed and stop reliably.
7. Encoder feedback is valid and no errors, vibration, heat, or excess current
   occurs.

## Troubleshooting

- **Not in `lsusb`:** verify native controller USB rather than only the CANable,
  main power as required by the board, data cable, direct port, and kernel log.
- **Visible but not in `odrivetool`:** install the udev rule, reconnect, inspect
  permissions and serial, remove hubs, and test a known data cable.
- **Only CANable visible:** identify the ODrive and its CAN bitrate/termination
  before bringing up `can0`; passive heartbeats cannot be read while it is down.
- **DFU ID visible:** do not flash until exact hardware and compatible image are
  proven.
- **Any non-zero error:** save the diagnostic, decode it for the detected
  firmware, fix its cause, then consider clearing it.
- **Unstable encoder:** remain IDLE; check supply, ground, shielding, pinout,
  resolution, and mechanical coupling.
- **Unexpected motion/noise/heat/current:** use the physical stop, isolate main
  power, and do not retry with higher limits.

## Known limitations and unresolved facts

- ODrive v3.6, 56 V variant, serial and firmware are identified; board
  originality remains unresolved.
- The motors are ZLTECH 6.5-inch robot hub motors, but the exact voltage/SKU
  suffix is not visible. Do not raise the tested limits from the documented
  1 A / 0.2 turn/s values without the exact label/datasheet and a suitable
  non-charger robot power source.
- CAN bitrate/termination were not needed for native-USB testing and remain
  unresolved.
- Both axes passed encoder mapping, encoder offset calibration, independent
  positive/negative movement, and simultaneous tests without active errors.
- Because the encoders have no index, offset calibration must be repeated after
  main power is removed.
- Main-power-cycle/reconnect validation passed. Encoder offset calibration must
  still be repeated after every future main-power cycle by design.
- The runtime script supports the requested two-axis case only after a real
  dual-axis legacy API is confirmed; Pro, S1, and Micro hardware must not be
  treated as dual-axis.

Official references:

- <https://docs.odriverobotics.com/v/latest/interfaces/odrivetool.html>
- <https://docs.odriverobotics.com/v/latest/manual/overview.html>
- <https://docs.odriverobotics.com/v/latest/guides/can-guide.html>
- <https://docs.odriverobotics.com/v/latest/guides/firmware-update.html>
