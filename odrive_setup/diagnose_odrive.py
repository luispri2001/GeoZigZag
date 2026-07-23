#!/usr/bin/env python3
"""Connect to one ODrive and print a read-only diagnostic snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from odrive_common import device_snapshot


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read ODrive identity, voltage, axes, errors, calibration and estimates."
    )
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--serial", help="Expected 12-digit hexadecimal serial number")
    parser.add_argument(
        "--interface",
        default="usb",
        help="ODrive interface string; default: usb. Do not use can:can0 until CAN is configured.",
    )
    parser.add_argument("--output", type=Path, help="Optional timestamped JSON output path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        import odrive
    except ImportError:
        print("ERROR: Python package 'odrive' is not installed.", file=sys.stderr)
        return 2

    print(
        f"Waiting up to {args.timeout:.1f}s on {args.interface!r}; "
        "this command does not calibrate, clear errors, save config, or move motors."
    )
    try:
        device = odrive.find_sync(
            serial_number=args.serial,
            timeout=args.timeout,
            interfaces=[args.interface],
        )
    except Exception as exc:
        print(f"ERROR: ODrive discovery failed: {exc}", file=sys.stderr)
        return 3
    if device is None:
        print("ERROR: no ODrive discovered.", file=sys.stderr)
        return 4

    report = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "interface": args.interface,
        "diagnostic_is_read_only": True,
        "device": device_snapshot(device),
    }
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.output.exists():
            print(f"ERROR: refusing to overwrite {args.output}", file=sys.stderr)
            return 5
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(f"Saved {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
