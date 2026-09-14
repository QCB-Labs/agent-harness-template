"""Tests for Circle Agent Wallet helper (tools/wallet.py)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tools import wallet


FULL_CONFIG = {
    "enabled": True,
    "mode": "autonomous-approved",
    "caps_verified": True,
    "public_address": "0xABCD1234ABCD1234ABCD1234ABCD1234ABCD1234",
    "site_scope": ["1F3D9", "1F3EA"],
    "pay_armed": True,
    "caps_usdc": {"per_tx": 5.0, "daily": 10.0, "weekly": 50.0, "monthly": 100.0},
}


# --- money_actions_allowed: fail-closed by default ---

def test_money_actions_default_false_no_config() -> None:
    assert wallet.money_actions_allowed(cfg={}) is False


def test_money_actions_default_false_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wallet, "WALLET_CONFIG", tmp_path / "nonexistent.json")
    assert wallet.money_actions_allowed() is False


def test_money_actions_false_when_disabled() -> None:
    cfg = {**FULL_CONFIG, "enabled": False}
    assert wallet.money_actions_allowed(cfg=cfg) is False


def test_money_actions_false_when_wrong_mode() -> None:
    cfg = {**FULL_CONFIG, "mode": "manual"}
    assert wallet.money_actions_allowed(cfg=cfg) is False


def test_money_actions_false_when_caps_not_verified() -> None:
    cfg = {**FULL_CONFIG, "caps_verified": False}
    assert wallet.money_actions_allowed(cfg=cfg) is False


def test_money_actions_false_when_no_address() -> None:
    cfg = {**FULL_CONFIG, "public_address": ""}
    assert wallet.money_actions_allowed(cfg=cfg) is False


def test_money_actions_false_when_site_not_in_scope() -> None:
    cfg = {**FULL_CONFIG, "site_scope": ["1F3D9"]}
    assert wallet.money_actions_allowed(site="1F3EA", cfg=cfg) is False


def test_money_actions_true_when_fully_configured() -> None:
    assert wallet.money_actions_allowed(site="1F3D9", cfg=FULL_CONFIG) is True
    assert wallet.money_actions_allowed(site="1F3EA", cfg=FULL_CONFIG) is True


def test_money_actions_false_when_scope_missing() -> None:
    cfg = {**FULL_CONFIG, "site_scope": None}
    assert wallet.money_actions_allowed(cfg=cfg) is False


# --- pay_armed: fail-closed ---

def test_pay_armed_default_false() -> None:
    assert wallet.pay_armed(cfg={}) is False


def test_pay_armed_true_when_set() -> None:
    assert wallet.pay_armed(cfg={"pay_armed": True}) is True


def test_pay_armed_false_when_string_true() -> None:
    assert wallet.pay_armed(cfg={"pay_armed": "true"}) is False


# --- wallet_session_ok ---

def test_wallet_session_ok_returns_false_on_subprocess_failure() -> None:
    with patch("subprocess.run", side_effect=FileNotFoundError("circle not found")):
        assert wallet.wallet_session_ok() is False


def test_wallet_session_ok_returns_false_on_nonzero_exit() -> None:
    import subprocess
    mock_result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="expired")
    with patch("subprocess.run", return_value=mock_result):
        assert wallet.wallet_session_ok() is False


def test_wallet_session_ok_returns_true_on_success() -> None:
    import subprocess
    mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="active", stderr="")
    with patch("subprocess.run", return_value=mock_result):
        assert wallet.wallet_session_ok() is True


# --- wallet_context_line ---

def test_wallet_context_line_browse_only_when_disabled() -> None:
    line = wallet.wallet_context_line(cfg={})
    assert "browse-only" in line
    assert "NO spending authority" in line


def test_wallet_context_line_shows_caps_when_approved() -> None:
    line = wallet.wallet_context_line(cfg=FULL_CONFIG)
    assert "approved" in line.lower()
    assert "0xABCD" in line
    assert "per-tx 5.0" in line


# --- pay: gate enforcement ---

def test_pay_raises_refused_when_not_allowed() -> None:
    with pytest.raises(wallet.PayRefused, match="money_actions_allowed"):
        wallet.pay("0xRecipient", 1.0, "test", cfg={})


def test_pay_raises_held_when_not_armed() -> None:
    cfg = {**FULL_CONFIG, "pay_armed": False}
    with pytest.raises(wallet.PayHeld, match="DISARMED"):
        wallet.pay("0xRecipient", 1.0, "test", cfg=cfg)


def test_pay_raises_refused_when_session_expired() -> None:
    with patch.object(wallet, "wallet_session_ok", return_value=False):
        with pytest.raises(wallet.PayRefused, match="No live Circle session"):
            wallet.pay("0xRecipient", 1.0, "test", cfg=FULL_CONFIG)


def test_pay_raises_refused_when_over_cap() -> None:
    official = {"treasury": "0xRecipient", "network": "base"}
    with patch.object(wallet, "wallet_session_ok", return_value=True):
        with patch("urllib.request.urlopen") as mock_open:
            resp = json.dumps(official).encode()
            mock_open.return_value.__enter__ = lambda s: s
            mock_open.return_value.__exit__ = lambda s, *a: None
            mock_open.return_value.read.return_value = resp
            with pytest.raises(wallet.PayRefused, match="exceeds per-tx cap"):
                wallet.pay("0xRecipient", 99.0, "test", cfg=FULL_CONFIG)


def test_pay_raises_refused_when_recipient_mismatch() -> None:
    official = {"treasury": "0xTreasury", "network": "base"}
    with patch.object(wallet, "wallet_session_ok", return_value=True):
        with patch("urllib.request.urlopen") as mock_open:
            resp = json.dumps(official).encode()
            mock_open.return_value.__enter__ = lambda s: s
            mock_open.return_value.__exit__ = lambda s, *a: None
            mock_open.return_value.read.return_value = resp
            with pytest.raises(wallet.PayRefused, match="does NOT match"):
                wallet.pay("0xWrongAddress", 1.0, "test", cfg=FULL_CONFIG)
