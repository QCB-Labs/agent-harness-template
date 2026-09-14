#!/usr/bin/env python3
"""Unlock operator authority for this local session.

Run at the keyboard — an MCP client, a shell, or SSH into the cloud VM. Proves
possession of the Ed25519 private key; the harness then treats privileged
actions as authorised until the session is locked or times out.

A closed terminal is NOT detected. Run lock_session.py when you are done, or
wait for the idle timeout.
"""

from __future__ import annotations

import base64
import getpass
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.hazmat.primitives import serialization

from tools.authority import SESSION_ABSOLUTE_MAX, SESSION_IDLE_TIMEOUT
from tools.paths import REPO_ROOT, session_dir, session_token_path

PRIVATE_KEY_PATH = REPO_ROOT / "operator_private.pem"


def _restrict(path: Path) -> None:
    """Best-effort owner-only permissions.

    Not the security boundary — the Ed25519 signature inside the token is. This
    only keeps the token away from other accounts on a shared machine.
    """
    try:
        if os.name == "posix":
            os.chmod(path, 0o600)
        else:
            os.system(f'icacls "{path}" /inheritance:r /grant:r "%USERNAME%:F" >nul 2>&1')
    except Exception:
        pass


def unlock() -> int:
    print("=== Operator Session Unlock ===")
    if not PRIVATE_KEY_PATH.exists():
        print(f"Error: {PRIVATE_KEY_PATH.name} not found.")
        print("Run:  python tools/setup_operator_keys.py")
        return 1

    passphrase = getpass.getpass("Enter operator passphrase: ").encode("utf-8")
    try:
        private_key = serialization.load_pem_private_key(
            PRIVATE_KEY_PATH.read_bytes(), password=passphrase
        )
    except Exception:
        print("[FAIL] Invalid passphrase.")
        return 1

    session_id = os.urandom(16).hex()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    token_data = f"SESSION:{session_id}:{now}"

    session_dir().mkdir(parents=True, exist_ok=True)
    session_token_path().write_text(
        json.dumps({
            "session_id": session_id,
            "issued_utc": now,
            "last_used_utc": now,
            "token_data": token_data,
            "signature": base64.b64encode(
                private_key.sign(token_data.encode("utf-8"))
            ).decode("utf-8"),
        }),
        encoding="utf-8",
    )
    _restrict(session_token_path())

    idle_hours = int(SESSION_IDLE_TIMEOUT.total_seconds() // 3600)
    max_hours = int(SESSION_ABSOLUTE_MAX.total_seconds() // 3600)
    print("[OK] Operator session unlocked.")
    print(f"     Session:  {session_id[:8]}...")
    print(f"     Expires:  {idle_hours}h idle, {max_hours}h absolute.")
    print("     Closing this terminal does NOT end it — run lock_session.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(unlock())
