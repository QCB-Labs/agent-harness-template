"""The agent's public name, read from the committed agent-profile.json.

This is the only place the harness learns what the agent is called. The handle
also names the agent's out-of-repo trust and session folders (see paths.py), so
two agents on one machine never share them.

The profile holds public names only. Credentials, email addresses, tokens,
wallet IDs, and operator identity never go in it.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = REPO_ROOT / "agent-profile.json"

# The handle becomes a folder name, so it is restricted to safe characters.
HANDLE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


@lru_cache(maxsize=1)
def load_profile() -> dict[str, str]:
    """Return {"handle", "display_name"}. Fails loudly if the file is wrong."""
    try:
        data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise RuntimeError(f"agent-profile.json not found at {PROFILE_PATH}") from error
    handle = str(data.get("handle", "")).strip()
    display_name = str(data.get("display_name", "")).strip()
    if not HANDLE_PATTERN.fullmatch(handle):
        raise RuntimeError(
            "agent-profile.json handle must be 1-32 lowercase letters, digits, "
            "'-' or '_', starting with a letter or digit"
        )
    if not display_name:
        raise RuntimeError("agent-profile.json display_name must not be blank")
    return {"handle": handle, "display_name": display_name}


def handle() -> str:
    return load_profile()["handle"]


def display_name() -> str:
    return load_profile()["display_name"]
