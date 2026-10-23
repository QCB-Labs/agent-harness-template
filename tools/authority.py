"""Operator authority: who may trigger a privileged action, and how it is proven.

Two independent paths. Either is sufficient on its own.

    1. ACTIVE SESSION — the operator is at the keyboard (an MCP client, a shell,
       or SSH into the cloud VM) and has proven possession of the Ed25519
       private key via unlock_session.py. Presence is the authorisation.

    2. SIGNED COMMAND — a command naming this specific action, with an unused
       nonce, inside its signed expiry window. This is the unattended path:
       sign it tonight, a wake consumes it in the morning. Requiring a live
       session as well would make pre-signed commands useless for exactly the
       case they exist for.

Email is never a path. Not signed, not from the operator, not ever. A signature
on email verifies identity; it does not grant authority, and there is no flag
that changes that.

A signed command is authorisation, not instruction. It is verified by the
harness at the moment a privileged tool is called — it never enters the agent's
context as something to read and weigh.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization

from tools import names
from tools.paths import (
    assert_outside_repo,
    operator_pubkey_path,
    session_token_path,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# Session lifetime. A closed terminal is NOT detected — run lock_session.py or
# wait for the idle timeout. Stating that honestly beats implying otherwise.
SESSION_IDLE_TIMEOUT = timedelta(hours=4)
SESSION_ABSOLUTE_MAX = timedelta(hours=12)

# Signed command windows. The default must be able to span an overnight gap so
# a command signed at night can be consumed by a wake the next morning.
DEFAULT_COMMAND_WINDOW = timedelta(hours=12)
MAX_COMMAND_WINDOW = timedelta(hours=72)

# The operator's laptop and the cloud VM will disagree about the time. Tolerate
# a little future skew rather than rejecting a freshly signed command.
CLOCK_SKEW_TOLERANCE = timedelta(minutes=5)

# A used nonce is kept for as long as any command could still be valid, so an
# old command can't be replayed once its nonce is pruned.
NONCE_RETENTION = MAX_COMMAND_WINDOW + CLOCK_SKEW_TOLERANCE + timedelta(hours=1)


class AuthorityError(PermissionError):
    """Raised when a privileged action is attempted without authority."""


# --------------------------------------------------------------------------
# Public key
# --------------------------------------------------------------------------

def load_operator_public_key():
    """Load the operator public key. Env var first, then the trust anchor.

    Never from inside the repository: a key the agent can write is a key the
    agent can replace with its own.
    """
    import os

    env_key = os.environ.get("OPERATOR_PUBLIC_KEY")
    if env_key:
        return serialization.load_pem_public_key(env_key.encode("utf-8"))

    path = operator_pubkey_path()
    assert_outside_repo(path)
    if path.exists():
        # Strip a UTF-8 BOM if one is present. PEM parsing fails on any bytes
        # before "-----BEGIN", and several Windows editors add a BOM silently.
        return serialization.load_pem_public_key(
            path.read_bytes().lstrip(b"\xef\xbb\xbf")
        )

    raise FileNotFoundError(
        f"Operator public key not found. Set OPERATOR_PUBLIC_KEY, or install "
        f"the key at {path} (setup_operator_keys.py prints the exact commands)."
    )


def operator_key_is_installed() -> bool:
    try:
        load_operator_public_key()
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------
# Session
# --------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def session_active(touch: bool = True) -> bool:
    """True only if a valid, unexpired operator session token exists.

    Requires all of: the token file exists, its Ed25519 signature verifies
    against the operator public key, it is inside the absolute cap, and it is
    inside the idle timeout. Fails closed on any exception.

    On success refreshes last_used_utc so an active session does not expire
    in the middle of a long piece of work.
    """
    path = session_token_path()
    if not path.exists():
        return False
    try:
        token = json.loads(path.read_text(encoding="utf-8"))
        public_key = load_operator_public_key()
        public_key.verify(
            base64.b64decode(token["signature"]),
            token["token_data"].encode("utf-8"),
        )

        now = _now()
        # The issue time is read from the signed data, not the file's own field,
        # so the absolute cap can't be moved by editing the file.
        prefix, _, signed_issued = token["token_data"].partition(":")
        signed_issued = signed_issued.partition(":")[2]
        if prefix != "SESSION" or not signed_issued:
            return False
        issued = _parse_utc(signed_issued)
        last_used = _parse_utc(token.get("last_used_utc", token["issued_utc"]))

        if now - issued > SESSION_ABSOLUTE_MAX:
            return False
        if now - last_used > SESSION_IDLE_TIMEOUT:
            return False

        if touch:
            token["last_used_utc"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            path.write_text(json.dumps(token), encoding="utf-8")
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------
# Nonce ledger
# --------------------------------------------------------------------------

def _nonce_file() -> Path:
    return REPO_ROOT / names.memory("replay") / "seen_nonces.json"


class NonceLedgerUnreadable(Exception):
    """The replay ledger exists but cannot be read as a ledger."""


def load_seen_nonces() -> dict[str, str]:
    """Return {nonce: first_seen_iso}, pruned of entries past retention.

    No ledger yet means nothing has been used. A ledger that is there but
    broken is different: reading it as empty would forget every used nonce
    and let a consumed command through again, so it raises.
    """
    path = _nonce_file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data.get("nonces", {})
    except Exception as exc:
        raise NonceLedgerUnreadable(str(exc)) from exc
    if isinstance(entries, list):  # tolerate an older list-shaped ledger
        entries = {nonce: "1970-01-01T00:00:00Z" for nonce in entries}
    if not isinstance(entries, dict):
        raise NonceLedgerUnreadable("nonces is not a mapping")
    cutoff = _now() - NONCE_RETENTION
    kept = {}
    for nonce, seen_at in entries.items():
        try:
            if _parse_utc(seen_at) >= cutoff:
                kept[nonce] = seen_at
        except Exception:
            continue
    return kept


def save_seen_nonces(nonces: dict[str, str]) -> None:
    path = _nonce_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"nonces": nonces}), encoding="utf-8")


# --------------------------------------------------------------------------
# Signed commands
# --------------------------------------------------------------------------

def commands_dir() -> Path:
    return REPO_ROOT / "operator" / "commands"


def verify_command(payload: str, consume: bool = False) -> tuple[bool, str | None, str]:
    """Verify a locally-written signed command payload.

    Returns (is_valid, action_text, reason). ``reason`` explains a rejection.

    Only ever called on files from operator/commands/, written by
    sign_command.py. It is never called on email content — email cannot
    produce a command, so there is nothing here for it to reach.

    ``consume`` records the nonce, so a valid command cannot be replayed. Pass
    it only when the action is actually being authorised.
    """
    try:
        public_key = load_operator_public_key()
    except Exception as exc:
        return False, None, f"no operator public key: {exc}"

    signature_match = re.search(
        r"\[SIGNATURE:\s*([A-Za-z0-9+/=\s]+)\]\s*$", payload.strip()
    )
    if not signature_match:
        return False, None, "no signature block"

    signature_raw = re.sub(r"\s+", "", signature_match.group(1))
    canonical = payload.strip()[: signature_match.start()].strip()

    try:
        public_key.verify(base64.b64decode(signature_raw), canonical.encode("utf-8"))
    except Exception:
        return False, None, "signature does not verify"

    nonce_match = re.search(r"\[NONCE:\s*([a-f0-9]{8,64})\]", canonical)
    if not nonce_match:
        return False, None, "no nonce"
    nonce = nonce_match.group(1)

    try:
        seen = load_seen_nonces()
    except NonceLedgerUnreadable:
        return False, None, "replay ledger unreadable; refusing until it is repaired"
    if nonce in seen:
        return False, None, "nonce already used (replay)"

    timestamp_match = re.search(r"\[TIMESTAMP:\s*([\d\-T:Z+]+)\]", canonical)
    expires_match = re.search(r"\[EXPIRES:\s*([\d\-T:Z+]+)\]", canonical)
    if not timestamp_match or not expires_match:
        return False, None, "missing timestamp or expiry"

    try:
        signed_at = _parse_utc(timestamp_match.group(1))
        expires_at = _parse_utc(expires_match.group(1))
    except Exception:
        return False, None, "unparseable timestamp"

    now = _now()
    if signed_at - now > CLOCK_SKEW_TOLERANCE:
        return False, None, "signed in the future beyond clock skew tolerance"
    if now > expires_at:
        return False, None, "command has expired"
    if expires_at - signed_at > MAX_COMMAND_WINDOW + CLOCK_SKEW_TOLERANCE:
        return False, None, "expiry window exceeds the maximum"

    action_lines = []
    past_headers = False
    for line in canonical.split("\n"):
        if past_headers:
            action_lines.append(line)
        elif not line.startswith(("[NONCE:", "[TIMESTAMP:", "[EXPIRES:")):
            past_headers = True
            action_lines.append(line)
    action_text = "\n".join(action_lines).strip()
    if not action_text:
        return False, None, "no action text"

    if consume:
        seen[nonce] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        save_seen_nonces(seen)

    return True, action_text, "ok"


def _normalise_action(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def find_signed_command(action: str, consume: bool = False) -> tuple[bool, str]:
    """Look for a valid signed command authorising this specific action.

    Scope is exact: a command signed for "invent kind 39" does not authorise
    "invent kind 40". Matching is on normalised whitespace and case only.
    """
    wanted = _normalise_action(action)
    directory = commands_dir()
    if not directory.exists():
        return False, "no operator/commands directory"

    reasons = []
    for path in sorted(directory.glob("*.md")):
        try:
            payload = path.read_text(encoding="utf-8")
        except Exception:
            continue
        valid, action_text, reason = verify_command(payload, consume=False)
        if not valid:
            reasons.append(f"{path.name}: {reason}")
            continue
        if _normalise_action(action_text) != wanted:
            reasons.append(f"{path.name}: authorises a different action")
            continue
        if consume:
            # Consuming re-checks the nonce; another caller may have used it since.
            consumed, _, reason = verify_command(payload, consume=True)
            if not consumed:
                reasons.append(f"{path.name}: {reason}")
                continue
            path.unlink(missing_ok=True)
        return True, f"authorised by {path.name}"

    return False, "; ".join(reasons) or "no signed command found"


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------

def operator_authority(action: str, consume: bool = True) -> tuple[bool, str]:
    """True if this specific action is authorised right now.

    Session first: if the operator is present, no signed file is needed and
    nothing is consumed. Otherwise fall back to a pre-signed command, which is
    consumed so it cannot be replayed.
    """
    if session_active():
        return True, "active operator session"

    found, reason = find_signed_command(action, consume=consume)
    if found:
        return True, reason
    return False, (
        "no active operator session and no valid signed command for this "
        f"action ({reason})"
    )
