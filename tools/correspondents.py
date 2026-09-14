"""Approved correspondents: who the agent may contact without being written to first.

This list is a trust anchor, exactly like the operator public key. If it lived
anywhere the agent can write, an injection could add an attacker's address and
then call send_new_thread to reach it — the same class of hole as a writable
public key. So it lives outside the repository and the agent can only read it.

The model never handles an address. It passes a KEY ("peer"), and the
harness resolves it here. There is no parameter anywhere in the outbound path
that accepts a raw email address, which is why "forward this to
attacker@example.com" has nowhere to land.

An unknown key raises. There is deliberately no fallback and no default
recipient: a typo must fail loudly rather than quietly mail the wrong person.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from tools.paths import assert_outside_repo, correspondents_path

# Deliberately EMPTY. Real addresses never live in this repository.
#
# Two reasons. The repo is public, so committing anyone's address publishes it
# — the pre-commit hook enforces that for the operator's own address. And the
# allowlist is a trust anchor: baking a copy into the repo would mean the
# agent could read an address list the operator cannot revoke by editing the
# anchor, which defeats the point of putting it outside.
#
# Install real addresses in the trust anchor (correspondents.json) or via the
# OPERATOR_CORRESPONDENTS env var. See correspondents.example.json.
#
# With nothing installed, send_new_thread refuses every key. That is the
# correct posture: the agent can still REPLY to anyone who writes to it, it
# simply cannot initiate contact until the operator says with whom.
DEFAULT_CORRESPONDENTS: dict[str, str] = {}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class UnknownCorrespondent(KeyError):
    """Raised when a key does not resolve. No fallback, by design."""


def load_correspondents() -> dict[str, str]:
    """Load the allowlist. Env override, then trust anchor, then defaults."""
    raw = os.environ.get("OPERATOR_CORRESPONDENTS")
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"OPERATOR_CORRESPONDENTS is not valid JSON: {exc}") from exc
        return _validate(data)

    path = correspondents_path()
    assert_outside_repo(path)
    if path.exists():
        # utf-8-sig, not utf-8: this file is hand-written on Windows, where
        # PowerShell 5.1's Set-Content -Encoding utf8 prepends a byte-order
        # mark that plain json.loads rejects. Tolerating a BOM costs nothing;
        # being strict about it just breaks the allowlist for a stray 3 bytes.
        return _validate(json.loads(path.read_text(encoding="utf-8-sig")))

    return dict(DEFAULT_CORRESPONDENTS)


def _validate(data: object) -> dict[str, str]:
    if not isinstance(data, dict):
        raise ValueError("correspondents must be a JSON object of key -> address")
    result: dict[str, str] = {}
    for key, address in data.items():
        if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
            raise ValueError(f"invalid correspondent key: {key!r}")
        if not isinstance(address, str) or not _EMAIL_RE.fullmatch(address.strip()):
            raise ValueError(f"invalid address for {key!r}")
        result[key] = address.strip()
    return result


def resolve(correspondent_key: str) -> str:
    """Resolve a key to an address. Raises UnknownCorrespondent if absent."""
    if not isinstance(correspondent_key, str):
        raise UnknownCorrespondent("correspondent key must be a string")
    key = correspondent_key.strip().lower()
    correspondents = load_correspondents()
    if key not in correspondents:
        raise UnknownCorrespondent(
            f"'{correspondent_key}' is not an approved correspondent. "
            f"Known: {', '.join(sorted(correspondents)) or '(none)'}. "
            "Adding one requires a signed operator command and a config update "
            "— the agent cannot expand its own reach."
        )
    return correspondents[key]


def is_approved(correspondent_key: str) -> bool:
    try:
        resolve(correspondent_key)
        return True
    except (UnknownCorrespondent, ValueError):
        return False


def address_to_key(address: str) -> str | None:
    """Reverse lookup for classifying inbound mail.

    Routing only — this decides which folder a message lands in. It never
    conveys trust, because a From header is trivially spoofable.
    """
    if not isinstance(address, str):
        return None
    needle = address.strip().lower()
    for key, known in load_correspondents().items():
        if known.lower() == needle:
            return key
    return None
