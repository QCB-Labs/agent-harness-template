"""Credential resolution for the agent ecosystem City connector and agent mailbox.

Prioritizes environment variables, then falls back to gitignored secrets.py,
and returns an empty string if no token is configured.
Never exposes plaintext secrets in logs or representations.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = Path(__file__).resolve().parent


def _load_secrets_module() -> object | None:
    """Load local secrets.py by explicit path without shadowing stdlib secrets."""
    candidate_paths = [
        TOOLS_DIR / "secrets.py",
        REPO_ROOT / "secrets.py",
    ]
    for p in candidate_paths:
        if p.exists() and p.is_file():
            try:
                spec = importlib.util.spec_from_file_location("agent_local_secrets", p)
                if spec is not None and spec.loader is not None:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    return module
            except Exception:
                continue
    return None


def get_city_token() -> str:
    """Return the City (1F3D9) bearer token, or empty string if not configured.

    Checks:
    1. AGENT_CITY_TOKEN environment variable
    2. CITY_TOKEN environment variable
    3. CITY_TOKEN from secrets.py
    """
    env_token = os.environ.get("AGENT_CITY_TOKEN") or os.environ.get("CITY_TOKEN")
    if env_token:
        return env_token.strip()

    secrets = _load_secrets_module()
    if secrets is not None:
        token = getattr(secrets, "CITY_TOKEN", "")
        if isinstance(token, str):
            return token.strip()

    return ""


def mask_token(token: str | None) -> str:
    """Return a masked representation of a secret token (e.g. 1f916_sk_...59e2).

    Never returns the full secret token.
    """
    if not token:
        return "(none)"
    clean = token.strip()
    if not clean:
        return "(none)"
    if len(clean) <= 8:
        return "***"

    for prefix in ("1f916_sk_", "1f3ea_sk_", "1f3d9_sk_"):
        if clean.startswith(prefix) and len(clean) > len(prefix) + 4:
            return f"{prefix}...{clean[-4:]}"

    if len(clean) > 12:
        return f"{clean[:8]}...{clean[-4:]}"

    return f"{clean[:3]}...{clean[-2:]}"


def has_city_access() -> bool:
    """Return True if City token is present."""
    return bool(get_city_token())


def get_agent_email() -> tuple[str, str]:
    """Return (address, app_password) for the agent's own mailbox, or ("", "").

    Checks, in order:
    1. AGENT_EMAIL_ADDRESS / AGENT_EMAIL_APP_PASSWORD environment variables
    2. The same names from secrets.py

    Distinct from GMAIL_ADDRESS / GMAIL_APP_PASSWORD, which are the operator's
    notifier — the address the agent sends status mail *to*. This pair is its own
    inbox, the one it receives on and replies from.

    Harness-internal. Never logged, never returned by any MCP tool. Google
    rejects ordinary account passwords for IMAP/SMTP, so the password here is a
    16-character App Password; spaces in it are insignificant and stripped.
    """
    address = os.environ.get("AGENT_EMAIL_ADDRESS", "").strip()
    password = os.environ.get("AGENT_EMAIL_APP_PASSWORD", "").strip()

    if not (address and password):
        secrets = _load_secrets_module()
        if secrets is not None:
            address = address or str(
                getattr(secrets, "AGENT_EMAIL_ADDRESS", "") or ""
            ).strip()
            password = password or str(
                getattr(secrets, "AGENT_EMAIL_APP_PASSWORD", "") or ""
            ).strip()

    if not (address and password):
        return "", ""
    return address, password.replace(" ", "")


def has_agent_email() -> bool:
    """Return True if the agent's mailbox credentials are configured."""
    return bool(get_agent_email()[0])
