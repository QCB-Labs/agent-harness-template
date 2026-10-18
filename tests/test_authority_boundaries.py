"""Exact authority limits, malformed commands, and replay isolation."""

import base64
import json
from datetime import datetime, timedelta, timezone
from itertools import permutations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from tools import authority, phase

FMT = "%Y-%m-%dT%H:%M:%SZ"


@pytest.fixture
def clock(tmp_path, monkeypatch):
    now = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(authority, "_now", lambda: now)
    monkeypatch.setattr(authority, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(phase, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("AGENT_SESSION_DIR", str(tmp_path / "session"))
    monkeypatch.setenv("AGENT_TRUST_DIR", str(tmp_path / "trust"))
    key = ed25519.Ed25519PrivateKey.generate()
    pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    monkeypatch.setenv("OPERATOR_PUBLIC_KEY", pem)
    return now, key


def _signed(key, canonical):
    signature = base64.b64encode(key.sign(canonical.strip().encode())).decode()
    return f"{canonical.strip()}\n\n[SIGNATURE: {signature}]\n"


def _command(clock, action="invent kind 39", nonce="ab" * 8, signed_at=None, expires_at=None):
    now, key = clock
    signed_at = now if signed_at is None else signed_at
    expires_at = now + timedelta(hours=12) if expires_at is None else expires_at
    return _signed(key, f"[NONCE: {nonce}]\n[TIMESTAMP: {signed_at.strftime(FMT)}]\n"
                   f"[EXPIRES: {expires_at.strftime(FMT)}]\n{action}")


def _session(clock, issued, last_used):
    _, key = clock
    data = f"SESSION:deadbeef:{issued.strftime(FMT)}"
    token = {"session_id": "deadbeef", "issued_utc": issued.strftime(FMT),
             "last_used_utc": last_used.strftime(FMT), "token_data": data,
             "signature": base64.b64encode(key.sign(data.encode())).decode()}
    path = authority.session_token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(token), encoding="utf-8")
    return path


def _drop(clock, name, **kwargs):
    path = authority.commands_dir() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_command(clock, **kwargs), encoding="utf-8")
    return path


def test_session_absolute_boundary(clock):
    now, _ = clock
    for seconds, accepted in [(-1, True), (0, True), (1, False)]:
        _session(clock, now - timedelta(hours=12, seconds=seconds), now)
        assert authority.session_active(touch=False) is accepted


def test_session_idle_boundary(clock):
    now, _ = clock
    for seconds, accepted in [(-1, True), (0, True), (1, False)]:
        _session(clock, now - timedelta(hours=5), now - timedelta(hours=4, seconds=seconds))
        assert authority.session_active(touch=False) is accepted


def test_session_default_touch_and_no_touch(clock):
    now, _ = clock
    old = now - timedelta(hours=1)
    path = _session(clock, old, old)
    original = path.read_bytes()
    assert authority.session_active(touch=False)
    assert path.read_bytes() == original
    assert authority.session_active()
    assert json.loads(path.read_text())["last_used_utc"] == now.strftime(FMT)


def test_command_future_skew_boundary(clock):
    now, _ = clock
    for seconds, accepted in [(-1, True), (0, True), (1, False)]:
        payload = _command(clock, signed_at=now + timedelta(minutes=5, seconds=seconds))
        assert authority.verify_command(payload)[0] is accepted


def test_command_expiry_boundary(clock):
    now, _ = clock
    for seconds, accepted in [(-1, False), (0, True), (1, True)]:
        payload = _command(clock, signed_at=now - timedelta(hours=1),
                           expires_at=now + timedelta(seconds=seconds))
        assert authority.verify_command(payload)[0] is accepted


def test_command_maximum_window_boundary(clock):
    now, _ = clock
    for seconds, accepted in [(-1, True), (0, True), (1, False)]:
        payload = _command(clock, expires_at=now + timedelta(hours=72, minutes=5, seconds=seconds))
        assert authority.verify_command(payload)[0] is accepted


@pytest.mark.parametrize("value", [None, "", "not a PEM key", 123])
def test_command_invalid_key_refuses(clock, monkeypatch, value):
    payload = _command(clock)
    if value is None:
        monkeypatch.delenv("OPERATOR_PUBLIC_KEY")
    elif isinstance(value, str):
        monkeypatch.setenv("OPERATOR_PUBLIC_KEY", value)
    else:
        # os.environ accepts strings only; supply an invalid key object at its loader boundary.
        monkeypatch.setattr(authority, "load_operator_public_key", lambda: value)
    assert authority.verify_command(payload)[0] is False


@pytest.mark.parametrize("field,value", [(field, value) for field in ("NONCE", "TIMESTAMP", "EXPIRES", "action") for value in (None, "", "{}") if (field, value) != ("action", "{}")])
def test_command_invalid_field_refuses(clock, field, value):
    now, key = clock
    fields = {"NONCE": "ab" * 8, "TIMESTAMP": now.strftime(FMT),
              "EXPIRES": (now + timedelta(hours=12)).strftime(FMT), "action": "invent kind 39"}
    fields[field] = value
    lines = [f"[{name}: {fields[name]}]" for name in ("NONCE", "TIMESTAMP", "EXPIRES")
             if fields[name] is not None]
    if fields["action"] is not None:
        lines.append(fields["action"])
    assert authority.verify_command(_signed(key, "\n".join(lines)))[0] is False


def test_unparseable_timestamp_refuses(clock):
    now, key = clock
    canonical = f"[NONCE: {'ab' * 8}]\n[TIMESTAMP: 2026-99-99T00:00:00Z]\n[EXPIRES: {(now + timedelta(hours=1)).strftime(FMT)}]\ninvent kind 39"
    assert authority.verify_command(_signed(key, canonical))[0] is False


def test_command_default_and_explicit_consumption(clock):
    now, _ = clock
    payload = _command(clock)
    assert authority.verify_command(payload)[0]
    assert not authority._nonce_file().exists()
    assert authority.verify_command(payload)[0]
    assert authority.verify_command(payload, consume=True)[0]
    assert authority.load_seen_nonces() == {"ab" * 8: now.strftime(FMT)}
    assert authority.verify_command(payload, consume=True)[0] is False


def test_multiline_action_preserved(clock):
    action = "invent kind 39\n[EXPIRES: illustrative text]\nthen rest"
    assert authority.verify_command(_command(clock, action=action))[1] == action


def test_different_actions_on_both_sides_refuse(clock):
    for action in ["invent kind 38", "invent kind 40"]:
        path = _drop(clock, "command.md", action=action)
        assert authority.find_signed_command("invent kind 39")[0] is False
        assert path.exists()
        assert authority.load_seen_nonces() == {}


def test_find_right_command_in_every_order(clock):
    for order in permutations(["invent kind 38", "invent kind 39", "invent kind 40"]):
        paths = [_drop(clock, f"{i}.md", action=action, nonce=f"{i + 1:016x}")
                 for i, action in enumerate(order)]
        valid, reason = authority.find_signed_command("invent kind 39")
        assert valid
        assert f"{order.index('invent kind 39')}.md" in reason
        assert all(path.exists() for path in paths)
        assert authority.load_seen_nonces() == {}


def test_find_consumes_matching_nonce_only(clock):
    now, _ = clock
    _drop(clock, "0.md", action="invent kind 38", nonce="aa" * 8)
    path = _drop(clock, "1.md")
    assert authority.find_signed_command("invent kind 39", consume=True)[0]
    assert not path.exists()
    assert authority.load_seen_nonces() == {"ab" * 8: now.strftime(FMT)}


def test_find_consumption_tolerates_disappearing_file(clock, monkeypatch):
    path = _drop(clock, "command.md")
    original = authority.verify_command
    def verify(payload, consume=False):
        result = original(payload, consume=consume)
        if consume:
            path.unlink()
        return result
    monkeypatch.setattr(authority, "verify_command", verify)
    assert authority.find_signed_command("invent kind 39", consume=True)[0]


def test_find_continues_after_bad_or_unreadable_files(clock, monkeypatch):
    from pathlib import Path
    bad = _drop(clock, "0.md")
    bad.write_text("unsigned", encoding="utf-8")
    unreadable = _drop(clock, "1.md")
    _drop(clock, "2.md", nonce="cc" * 8)
    original = Path.read_text
    def read(path, *args, **kwargs):
        if path == unreadable:
            raise OSError("illustrative read failure")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read)
    assert authority.find_signed_command("invent kind 39")[0]


def test_missing_command_directory_refuses(clock):
    assert authority.find_signed_command("invent kind 39")[0] is False


def test_operator_default_consumes(clock):
    path = _drop(clock, "command.md")
    assert authority.operator_authority("invent kind 39")[0]
    assert not path.exists()
    assert authority.load_seen_nonces() == {"ab" * 8: clock[0].strftime(FMT)}


def test_nonce_retention_boundary_and_invalid_entry(clock):
    now, _ = clock
    cutoff = now - timedelta(hours=24)
    entries = {"invalid": "broken", "old": (cutoff - timedelta(seconds=1)).strftime(FMT),
               "edge": cutoff.strftime(FMT), "new": (cutoff + timedelta(seconds=1)).strftime(FMT)}
    authority.save_seen_nonces(entries)
    assert authority.load_seen_nonces() == {key: entries[key] for key in ("edge", "new")}
    authority.save_seen_nonces({"replacement": now.strftime(FMT)})
    assert authority.load_seen_nonces() == {"replacement": now.strftime(FMT)}


def test_corrupt_nonce_ledger_fails_closed(clock):
    payload = _command(clock)
    assert authority.verify_command(payload, consume=True)[0]
    authority._nonce_file().write_text("{broken", encoding="utf-8")
    ok, action, reason = authority.verify_command(payload)
    assert ok is False and action is None and "replay ledger unreadable" in reason
    # A command never used before is refused too: nothing can be trusted
    # as unused while the ledger cannot be read.
    assert authority.verify_command(_command(clock))[0] is False
    # A ledger of the wrong shape is as unreadable as broken JSON.
    for wrong in ('[]', '{"nonces": 7}', '"text"'):
        authority._nonce_file().write_text(wrong, encoding="utf-8")
        assert authority.verify_command(payload)[0] is False
    # With no ledger file at all, nothing has been used.
    authority._nonce_file().unlink()
    assert authority.load_seen_nonces() == {}


def test_parse_utc_naive_and_aware_same_instant(clock):
    now, _ = clock
    assert authority._parse_utc(now.replace(tzinfo=None).isoformat()) == now
    assert authority._parse_utc(now.astimezone(timezone(timedelta(hours=2))).isoformat()) == now


def test_default_command_window(clock):
    assert authority.DEFAULT_COMMAND_WINDOW == timedelta(hours=12)



def test_operator_key_installation_failure_refuses(clock, monkeypatch):
    monkeypatch.delenv("OPERATOR_PUBLIC_KEY")
    assert authority.operator_key_is_installed() is False


def test_nonce_ledger_unreadable_and_bad_entry(clock, monkeypatch):
    from pathlib import Path
    now, _ = clock
    authority.save_seen_nonces({"bad": None, "good": now.strftime(FMT)})
    assert authority.load_seen_nonces() == {"good": now.strftime(FMT)}
    path = authority._nonce_file()
    original = Path.read_text
    def read(target, *args, **kwargs):
        if target == path:
            raise OSError("illustrative read failure")
        return original(target, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read)
    # A ledger that is there and cannot be read is not an empty ledger.
    with pytest.raises(authority.NonceLedgerUnreadable):
        authority.load_seen_nonces()
    assert authority.verify_command(_command(clock))[0] is False
