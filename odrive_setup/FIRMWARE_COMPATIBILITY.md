# Firmware compatibility decision

Date: 2026-07-23

Connected controller:

- ODrive v3.6 dual-axis, 56 V variant
- Serial `335C33513235`
- Firmware `0.5.1`, unreleased/custom flag set

Repository inspected:

- `Innovations-in-Smart-and-Secure-Systems/wildboar_jetson_firmware`
- Branch `odrive`
- Commit `3fa71b56276329a71aaf27b35f3b76692d646a80`

## Decision

**Do not flash.**

The `odrive` branch contains no `.hex`, `.bin`, `.elf`, DFU bundle, ODrive
source tree, firmware build manifest, hardware target metadata, or flashing
procedure. It is a ROS 2 host-side CAN controller whose source explicitly says
it is adapted for ODrive firmware 0.5.1. The connected board already reports
firmware 0.5.1.

There is therefore no repository firmware image whose hardware compatibility
or integrity can be verified. Flashing another image would add risk without
solving the current blocker, which is encoder readiness/calibration.

## Integration observations

The branch assumes:

- CANSimple at 250 kbit/s;
- two ODrive boards and four node IDs, 0 through 3;
- automatic error clearing and closed-loop initialization;
- configured limits up to 4 turn/s in `robot_params.yaml`;
- active position hold when stopped.

The current bench setup has one two-axis board with node IDs 0 and 1. The ROS
node must not be launched unmodified: it would transmit to nonexistent nodes 2
and 3 and automatically request closed-loop before this bring-up has validated
the encoders.

`wildboar_motor_firmware` is unrelated to ODrive flashing. It is Arduino
firmware for four DC motors driven through a ZSX-X11H PWM driver.

## Evidence needed before any future firmware update

1. An actual firmware image or reproducible source/build instructions.
2. Explicit ODrive v3.6 56 V target metadata.
3. Upstream base version and custom patch/commit.
4. Image checksum and expected USB/CAN protocol.
5. Recovery procedure tested with native USB/DFU.
6. Original configuration backup and a reason the existing communicating
   firmware cannot meet requirements.
