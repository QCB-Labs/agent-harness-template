#!/usr/bin/env python3
"""Fail-closed preflight for a scheduled wake. Standard library only."""
from pathlib import Path
import sys

# Direct execution may otherwise shadow stdlib secrets with tools/secrets.py.
if __package__ in (None, ""):
    sys.path.pop(0)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.stop_marker import honour, marker_present


def main() -> int:
    try:
        root = Path.cwd()
        if not (root / "GEMINI.md").is_file() or not (root / "tools" / "harness_mcp.py").is_file():
            print("NOT THE REPO ROOT - DO NOT WAKE")
            return 2
        if marker_present(root):
            honour(root, "stop_check.py")
            print("STOP MARKER PRESENT - DO NOT WAKE")
            return 1
        print("clear to wake")
        return 0
    except Exception:
        print("STOP CHECK FAILED - DO NOT WAKE")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
