"""Command-line entry point for the publication evaluation."""

from __future__ import annotations

import argparse
import json

from .evaluation import run_evaluation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run reproducible GeoZigzag experiments.")
    parser.add_argument("--config", default="configs/evaluation.yaml", help="Evaluation YAML file.")
    parser.add_argument("--out", default="outputs/evaluation", help="Generated artifact directory.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    summary = run_evaluation(args.config, args.out)
    print(json.dumps({"results": len(summary["results"]), "output": args.out}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
