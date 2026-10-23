#!/usr/bin/env python3
"""Operator-only removal of a stop marker; prints but never runs a commit."""
import argparse
import json
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.pop(0)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.stop_marker import lift, marker_reason, marker_present


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reason")
    args = parser.parse_args(argv)
    if not args.reason or not args.reason.strip():
        print("An operator reason is required. Nothing was changed.")
        return 2
    try:
        root = Path.cwd()
        if not (root / "AGENTS.md").is_file() or not (root / "tools" / "harness_mcp.py").is_file():
            print("NOT THE REPO ROOT - NO MARKER REMOVED")
            return 2
        if not marker_present(root):
            print("No STOP.md marker is standing. Nothing was changed.")
            return 1
        def show(marker, count):
            print(marker)
            print(f"Honoured rows: {count}.")
        marker, count = lift(root, args.reason, before=show)
        message = (f"Remove stop: marker reason {json.dumps(marker_reason(marker), ensure_ascii=False)}; "
                   f"operator reason {json.dumps(args.reason, ensure_ascii=False)}; honoured rows {count}")
        print("STOP.md was removed. The removal was recorded in STOP_LOG.md.")
        print("Suggested commit message (not run): " + message)
        print("Include STOP_LOG.md and the STOP.md deletion in the operator commit.")
        return 0
    except Exception:
        print("STOP REMOVAL FAILED - CHECK STOP.md AND STOP_LOG.md")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
