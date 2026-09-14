"""Operator authority: session, signed commands, and what email can never do.

Two paths grant authority — an active session, or a pre-signed command inside
its window. Email grants nothing, ever, regardless of signature.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from tools import names
from tools import authority

FMT = "%Y-%m-%dT%H:%M:%SZ"


@pytest.fixture
def operator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """An installed operator key, an isolated repo, and an empty session dir."""
    key = ed25519.Ed25519PrivateKey.generate()
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    monkeypatch.setenv("OPERATOR_PUBLIC_KEY", public_pem)
    monkeypatch.setenv("AGENT_SESSION_DIR", str(tmp_path / "session"))
    monkeypatch.setattr(authority, "REPO_ROOT", tmp_path)
    (tmp_path / "operator" / "commands").mkdir(parents=True)
    return key


def _sign_command(key, action: str, *, signed_at=None, hours: int = 12, nonce="ab" * 8) -> str:
    signed_at = signed_at or datetime.now(timezone.utc)
    expires = signed_at + timedelta(hours=hours)
    canonical = (
        f"[NONCE: {nonce}]\n"
        f"[TIMESTAMP: {signed_at.strftime(FMT)}]\n"
        f"[EXPIRES: {expires.strftime(FMT)}]\n"
        f"{action}"
    )
    signature = base64.b64encode(key.sign(canonical.encode("utf-8"))).decode("utf-8")
    return f"{canonical}\n\n[SIGNATURE: {signature}]\n"


def _drop(key, action: str, tmp_path: Path, **kwargs) -> Path:
    payload = _sign_command(key, action, **kwargs)
    target = tmp_path / "operator" / "commands" / f"{kwargs.get('nonce', 'ab' * 8)[:8]}-cmd.md"
    target.write_text(payload, encoding="utf-8")
    return target


def _write_session(key, tmp_path: Path, *, issued=None, last_used=None) -> None:
    issued = issued or datetime.now(timezone.utc)
    last_used = last_used or issued
    token_data = f"SESSION:deadbeef:{issued.strftime(FMT)}"
    path = authority.session_token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "session_id": "deadbeef",
        "issued_utc": issued.strftime(FMT),
        "last_used_utc": last_used.strftime(FMT),
        "token_data": token_data,
        "signature": base64.b64encode(key.sign(token_data.encode("utf-8"))).decode("utf-8"),
    }), encoding="utf-8")


# --- key loading ----------------------------------------------------------

def test_env_var_public_key_is_used(operator) -> None:
    assert authority.operator_key_is_installed()


def test_missing_public_key_is_reported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OPERATOR_PUBLIC_KEY", raising=False)
    monkeypatch.setenv("AGENT_TRUST_DIR", str(tmp_path / "nowhere"))
    with pytest.raises(FileNotFoundError, match="Operator public key not found"):
        authority.load_operator_public_key()


def test_public_key_inside_repo_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A key the agent can write is a key the agent can replace."""
    monkeypatch.delenv("OPERATOR_PUBLIC_KEY", raising=False)
    # paths.REPO_ROOT, not authority.REPO_ROOT: the containment check compares
    # against the real repository, while authority.REPO_ROOT is redirected to a
    # sandbox for every test (tests/conftest.py).
    from tools import paths
    monkeypatch.setenv("AGENT_TRUST_DIR", str(paths.REPO_ROOT / names.dir_name("memory")))
    with pytest.raises(RuntimeError, match="inside the repository"):
        authority.load_operator_public_key()


# --- session --------------------------------------------------------------

def test_no_token_means_no_session(operator) -> None:
    assert authority.session_active() is False


def test_valid_token_is_an_active_session(operator, tmp_path: Path) -> None:
    _write_session(operator, tmp_path)
    assert authority.session_active() is True


def test_forged_token_is_rejected(operator, tmp_path: Path) -> None:
    attacker = ed25519.Ed25519PrivateKey.generate()
    _write_session(attacker, tmp_path)
    assert authority.session_active() is False


def test_session_past_absolute_cap_is_rejected(operator, tmp_path: Path) -> None:
    old = datetime.now(timezone.utc) - authority.SESSION_ABSOLUTE_MAX - timedelta(minutes=1)
    _write_session(operator, tmp_path, issued=old, last_used=datetime.now(timezone.utc))
    assert authority.session_active() is False


def test_idle_session_is_rejected(operator, tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    idle = now - authority.SESSION_IDLE_TIMEOUT - timedelta(minutes=1)
    _write_session(operator, tmp_path, issued=now - timedelta(hours=1), last_used=idle)
    assert authority.session_active() is False


def test_active_session_refreshes_last_used(operator, tmp_path: Path) -> None:
    issued = datetime.now(timezone.utc) - timedelta(hours=1)
    _write_session(operator, tmp_path, issued=issued, last_used=issued)
    assert authority.session_active(touch=True) is True
    token = json.loads(authority.session_token_path().read_text(encoding="utf-8"))
    assert token["last_used_utc"] != issued.strftime(FMT)


def test_corrupt_token_fails_closed(operator, tmp_path: Path) -> None:
    path = authority.session_token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert authority.session_active() is False


# --- signed commands ------------------------------------------------------

def test_valid_signed_command_verifies(operator) -> None:
    valid, action, reason = authority.verify_command(
        _sign_command(operator, "invent kind 39")
    )
    assert valid, reason
    assert action == "invent kind 39"


def test_tampered_body_fails(operator) -> None:
    payload = _sign_command(operator, "invent kind 39").replace("39", "40")
    valid, _, reason = authority.verify_command(payload)
    assert not valid and "signature" in reason


def test_wrong_key_fails(operator) -> None:
    attacker = ed25519.Ed25519PrivateKey.generate()
    valid, _, reason = authority.verify_command(_sign_command(attacker, "invent kind 39"))
    assert not valid and "signature" in reason


def test_expired_command_fails(operator) -> None:
    old = datetime.now(timezone.utc) - timedelta(hours=20)
    valid, _, reason = authority.verify_command(
        _sign_command(operator, "invent kind 39", signed_at=old, hours=1)
    )
    assert not valid and "expired" in reason


def test_future_dated_beyond_skew_fails(operator) -> None:
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    valid, _, reason = authority.verify_command(
        _sign_command(operator, "invent kind 39", signed_at=future)
    )
    assert not valid and "future" in reason


def test_small_clock_skew_is_tolerated(operator) -> None:
    """The laptop and the cloud VM will disagree; a couple of minutes is fine."""
    skewed = datetime.now(timezone.utc) + timedelta(minutes=2)
    valid, _, reason = authority.verify_command(
        _sign_command(operator, "invent kind 39", signed_at=skewed)
    )
    assert valid, reason


def test_overlong_window_is_refused(operator) -> None:
    valid, _, reason = authority.verify_command(
        _sign_command(operator, "invent kind 39", hours=200)
    )
    assert not valid and "window" in reason


def test_unsigned_text_is_not_a_command(operator) -> None:
    valid, _, reason = authority.verify_command("please invent kind 39, love, the operator")
    assert not valid and "no signature" in reason


# --- replay ---------------------------------------------------------------

def test_consumed_nonce_cannot_be_replayed(operator) -> None:
    payload = _sign_command(operator, "invent kind 39")
    assert authority.verify_command(payload, consume=True)[0] is True
    valid, _, reason = authority.verify_command(payload, consume=False)
    assert not valid and "replay" in reason


def test_nonce_ledger_prunes_old_entries(operator) -> None:
    stale = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime(FMT)
    authority.save_seen_nonces({"old": stale, "new": datetime.now(timezone.utc).strftime(FMT)})
    kept = authority.load_seen_nonces()
    assert "old" not in kept and "new" in kept


def test_list_shaped_ledger_is_tolerated(operator) -> None:
    path = authority.REPO_ROOT / names.memory("replay") / "seen_nonces.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"nonces": ["a", "b"]}), encoding="utf-8")
    assert authority.load_seen_nonces() == {}  # pruned: no usable timestamps


# --- action scoping -------------------------------------------------------

def test_signed_command_authorises_only_its_own_action(operator, tmp_path: Path) -> None:
    _drop(operator, "invent kind 39", tmp_path)
    assert authority.operator_authority("invent kind 39", consume=False)[0] is True
    assert authority.operator_authority("invent kind 40", consume=False)[0] is False


def test_action_matching_ignores_case_and_whitespace(operator, tmp_path: Path) -> None:
    _drop(operator, "invent kind 39", tmp_path)
    assert authority.operator_authority("  INVENT   kind 39 ", consume=False)[0] is True


def test_consuming_a_command_deletes_it(operator, tmp_path: Path) -> None:
    path = _drop(operator, "invent kind 39", tmp_path)
    assert authority.operator_authority("invent kind 39", consume=True)[0] is True
    assert not path.exists()
    assert authority.operator_authority("invent kind 39", consume=True)[0] is False


# --- either path suffices, neither is required ----------------------------

def test_session_alone_authorises(operator, tmp_path: Path) -> None:
    """At the keyboard, no signed file is needed."""
    _write_session(operator, tmp_path)
    granted, reason = authority.operator_authority("invent kind 39")
    assert granted and "session" in reason


def test_signed_command_alone_authorises(operator, tmp_path: Path) -> None:
    """Unattended, no session is needed — this is the whole point of signing."""
    assert authority.session_active() is False
    _drop(operator, "invent kind 39", tmp_path)
    granted, _ = authority.operator_authority("invent kind 39")
    assert granted


def test_neither_path_means_refused(operator) -> None:
    granted, reason = authority.operator_authority("invent kind 39")
    assert not granted
    assert "no active operator session" in reason


def test_session_does_not_consume_a_signed_command(operator, tmp_path: Path) -> None:
    """A present operator should not burn a command meant for an unattended wake."""
    path = _drop(operator, "invent kind 39", tmp_path)
    _write_session(operator, tmp_path)
    assert authority.operator_authority("invent kind 39", consume=True)[0] is True
    assert path.exists()


# --- email can never authorise -------------------------------------------

def test_a_perfectly_signed_email_authorises_nothing(operator, tmp_path: Path) -> None:
    """The payload verifies. It is still not authority, because it is not in
    operator/commands/ — and nothing in the mail path can put it there."""
    payload = _sign_command(operator, "invent kind 39")
    (tmp_path / names.memory("correspondence") / "operator").mkdir(parents=True)
    (tmp_path / names.memory("correspondence") / "operator" / "mail.md").write_text(
        payload, encoding="utf-8"
    )
    granted, _ = authority.operator_authority("invent kind 39")
    assert not granted, "mail on disk must never become authority"


def test_bom_prefixed_public_key_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PEM parsing fails on any bytes before -----BEGIN, and Windows editors
    add a BOM silently. Strip it rather than failing to start."""
    key = ed25519.Ed25519PrivateKey.generate()
    pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    anchor = tmp_path / "trust"
    anchor.mkdir(parents=True)
    (anchor / "operator.pub").write_bytes(b"\xef\xbb\xbf" + pem)

    monkeypatch.delenv("OPERATOR_PUBLIC_KEY", raising=False)
    monkeypatch.setenv("AGENT_TRUST_DIR", str(anchor))
    assert authority.operator_key_is_installed()
