#!/usr/bin/env python3
"""End the operator session immediately. Authority is revoked on return."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.paths import session_token_path


def lock() -> int:
    token = session_token_path()
    if token.exists():
        token.unlink()
        print("[OK] Operator session locked. Authority revoked.")
    else:
        print("No active operator session.")
    return 0


if __name__ == "__main__":
    raise SystemExit(lock())
