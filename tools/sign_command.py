#!/usr/bin/env python3
"""Sign a command authorising one specific privileged action.

For the unattended case: sign tonight, a wake consumes it in the morning. At
the keyboard you do not need this — an unlocked session is authority enough.

The command text is prompted, never taken as a shell argument, so it does not
land in PowerShell or bash history.
"""

from __future__ import annotations

import base64
import getpass
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.hazmat.primitives import serialization

from tools.authority import DEFAULT_COMMAND_WINDOW, MAX_COMMAND_WINDOW, commands_dir
from tools.paths import REPO_ROOT

PRIVATE_KEY_PATH = REPO_ROOT / "operator_private.pem"


def sign_command() -> int:
    print("=== Sign Operator Command ===")
    if not PRIVATE_KEY_PATH.exists():
        print(f"Error: {PRIVATE_KEY_PATH.name} not found.")
        print("Run:  python tools/setup_operator_keys.py")
        return 1

    print()
    print("The action must match EXACTLY what the harness will check. A command")
    print('signed for "invent kind 39" does not authorise "invent kind 40".')
    print()
    command_text = input("Action to authorise: ").strip()
    if not command_text:
        print("Error: command cannot be empty.")
        return 1

    default_hours = int(DEFAULT_COMMAND_WINDOW.total_seconds() // 3600)
    max_hours = int(MAX_COMMAND_WINDOW.total_seconds() // 3600)
    raw = input(f"Valid for how many hours? [{default_hours}, max {max_hours}] ").strip()
    try:
        hours = int(raw) if raw else default_hours
    except ValueError:
        print("Error: not a number.")
        return 1
    if not 1 <= hours <= max_hours:
        print(f"Error: window must be between 1 and {max_hours} hours.")
        return 1

    passphrase = getpass.getpass("Enter operator passphrase: ").encode("utf-8")
    try:
        private_key = serialization.load_pem_private_key(
            PRIVATE_KEY_PATH.read_bytes(), password=passphrase
        )
    except Exception:
        print("[FAIL] Invalid passphrase.")
        return 1

    nonce = os.urandom(16).hex()
    signed_at = datetime.now(timezone.utc)
    expires_at = signed_at + timedelta(hours=hours)
    fmt = "%Y-%m-%dT%H:%M:%SZ"

    canonical = (
        f"[NONCE: {nonce}]\n"
        f"[TIMESTAMP: {signed_at.strftime(fmt)}]\n"
        f"[EXPIRES: {expires_at.strftime(fmt)}]\n"
        f"{command_text}"
    )
    signature = base64.b64encode(
        private_key.sign(canonical.encode("utf-8"))
    ).decode("utf-8")

    directory = commands_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{signed_at.strftime('%Y-%m-%d-%H%M%S')}-{nonce[:8]}-command.md"
    target.write_text(f"{canonical}\n\n[SIGNATURE: {signature}]\n", encoding="utf-8")

    print()
    print(f"[OK] Signed: {target.name}")
    print(f"     Action:  {command_text}")
    print(f"     Expires: {expires_at.strftime(fmt)}  ({hours}h)")
    print("     Consumed and deleted when the action runs. Usable once.")
    return 0


if __name__ == "__main__":
    raise SystemExit(sign_command())
