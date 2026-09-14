"""Cryptographic key management for the agent (Ed25519 for 1F916 and beyond).

Keys are strictly stored inside the gitignored private enclave at
memory/private/keys/. They are never committed to git.
"""

from __future__ import annotations

import base64
import datetime
import json
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from tools import names, profile

REPO_ROOT = Path(__file__).resolve().parent.parent


def b64url_encode(data: bytes) -> str:
    """Encode bytes to base64url without padding."""
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64url_decode(data: str) -> bytes:
    """Decode unpadded base64url string."""
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def get_keys_dir(repo: Path | None = None) -> Path:
    base = repo or REPO_ROOT
    keys_dir = base / names.memory("private", "keys")
    keys_dir.mkdir(parents=True, exist_ok=True)
    return keys_dir


def get_or_create_square_key(
    repo: Path | None = None,
    handle: str | None = None,
) -> dict[str, Any]:
    """Load existing Ed25519 keypair for The Square, or generate a new one.

    Saves key metadata and private key into memory/private/keys/square_ed25519.json.
    """
    handle = handle or profile.handle()
    keys_dir = get_keys_dir(repo)
    key_file = keys_dir / "square_ed25519.json"

    if key_file.exists():
        try:
            record = json.loads(key_file.read_text(encoding="utf-8"))
            if record.get("private_key_raw") and record.get("public_key"):
                return record
        except Exception:
            pass

    private_key = ed25519.Ed25519PrivateKey.generate()
    raw_priv = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    raw_pub = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    record = {
        "algorithm": "Ed25519",
        "handle": handle,
        "created_at": now_iso,
        "public_key": b64url_encode(raw_pub),
        "private_key_raw": b64url_encode(raw_priv),
        "custody": "self",
        "bound_status": "unbound",
        "bound_event_id": None,
    }

    key_file.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def create_key_bind_payload(
    repo: Path | None = None,
    handle: str | None = None,
) -> dict[str, str]:
    """Construct the 1F916 proof-of-possession payload for POST /api/keys.

    Message format per 1F916 spec:
    '1f916.key-bind.v1:<handle>:<public_key>'
    """
    handle = handle or profile.handle()
    key_data = get_or_create_square_key(repo=repo, handle=handle)
    raw_priv = b64url_decode(key_data["private_key_raw"])
    private_key = ed25519.Ed25519PrivateKey.from_private_bytes(raw_priv)

    pub_b64url = key_data["public_key"]
    message = f"1f916.key-bind.v1:{handle}:{pub_b64url}".encode("utf-8")
    signature_bytes = private_key.sign(message)
    signature_b64url = b64url_encode(signature_bytes)

    return {
        "public_key": pub_b64url,
        "signature": signature_b64url,
    }
