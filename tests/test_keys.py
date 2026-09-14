"""Tests for Ed25519 key management (tools/keys.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import names
from tools import keys


def test_get_or_create_generates_new_key(tmp_path: Path) -> None:
    record = keys.get_or_create_square_key(repo=tmp_path, handle="agent")
    assert record["algorithm"] == "Ed25519"
    assert record["handle"] == "agent"
    assert record["custody"] == "self"
    assert record["bound_status"] == "unbound"
    assert isinstance(record["public_key"], str)
    assert isinstance(record["private_key_raw"], str)
    assert len(record["public_key"]) > 20

    key_file = tmp_path / names.memory("private") / "keys" / "square_ed25519.json"
    assert key_file.exists()


def test_get_or_create_returns_existing_key(tmp_path: Path) -> None:
    first = keys.get_or_create_square_key(repo=tmp_path, handle="agent")
    second = keys.get_or_create_square_key(repo=tmp_path, handle="agent")
    assert first["public_key"] == second["public_key"]
    assert first["private_key_raw"] == second["private_key_raw"]


def test_corrupted_key_file_regenerates(tmp_path: Path) -> None:
    keys_dir = tmp_path / names.memory("private") / "keys"
    keys_dir.mkdir(parents=True)
    key_file = keys_dir / "square_ed25519.json"
    key_file.write_text("{invalid json!!", encoding="utf-8")

    record = keys.get_or_create_square_key(repo=tmp_path)
    assert record["algorithm"] == "Ed25519"
    assert record["public_key"]
    assert record["private_key_raw"]


def test_corrupted_key_file_missing_fields_regenerates(tmp_path: Path) -> None:
    keys_dir = tmp_path / names.memory("private") / "keys"
    keys_dir.mkdir(parents=True)
    key_file = keys_dir / "square_ed25519.json"
    key_file.write_text(json.dumps({"algorithm": "Ed25519"}), encoding="utf-8")

    record = keys.get_or_create_square_key(repo=tmp_path)
    assert record["private_key_raw"]
    assert record["public_key"]


def test_create_key_bind_payload_produces_valid_signature(tmp_path: Path) -> None:
    from cryptography.hazmat.primitives.asymmetric import ed25519 as ed_mod

    payload = keys.create_key_bind_payload(repo=tmp_path, handle="agent")
    assert "public_key" in payload
    assert "signature" in payload

    record = keys.get_or_create_square_key(repo=tmp_path, handle="agent")
    raw_pub = keys.b64url_decode(payload["public_key"])
    sig = keys.b64url_decode(payload["signature"])

    pub_key = ed_mod.Ed25519PublicKey.from_public_bytes(raw_pub)
    message = f"1f916.key-bind.v1:agent:{payload['public_key']}".encode("utf-8")
    pub_key.verify(sig, message)


def test_b64url_roundtrip() -> None:
    data = b"\x00\x01\xff\xfe"
    assert keys.b64url_decode(keys.b64url_encode(data)) == data


def test_custom_handle(tmp_path: Path) -> None:
    record = keys.get_or_create_square_key(repo=tmp_path, handle="othername")
    assert record["handle"] == "othername"
    payload = keys.create_key_bind_payload(repo=tmp_path, handle="othername")
    assert payload["public_key"] == record["public_key"]
