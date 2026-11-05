"""Reproducible robot coverage CLI: python -m geozigzag.coverage_cli."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .export import export_route_bundle
from .robot_coverage import coverage_request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "configs/robot_coverage_demo.json",
    )
    parser.add_argument("--robot", choices=["differential", "skid_steer", "ackermann"])
    parser.add_argument(
        "--out",
        type=Path,
        help="New output directory; existing directories are never overwritten.",
    )
    args = parser.parse_args()
    try:
        payload = json.loads(args.config.read_text())
        if args.robot:
            payload.setdefault("robot", {})["kind"] = args.robot
        result = coverage_request(payload)
        output = args.out or Path("outputs") / (
            "coverage-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        )
        output.mkdir(parents=True, exist_ok=False)
        export_route_bundle(result["waypoints"], output)
        (output / "plan.json").write_text(
            json.dumps(result, indent=2, allow_nan=False) + "\n"
        )
        (output / "request.json").write_text(
            json.dumps(payload, indent=2, allow_nan=False) + "\n"
        )
        print(
            json.dumps(
                {
                    "output": str(output),
                    "robot": result["robot"],
                    "metrics": result["metrics"],
                },
                indent=2,
            )
        )
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(2, f"Coverage failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
