#!/usr/bin/env python3
"""Compatibility entry point; implementation lives in geozigzag.web_server."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from geozigzag.web_server import *  # noqa: F401,F403,E402

if __name__ == "__main__":
    main()
