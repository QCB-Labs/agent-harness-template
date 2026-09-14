"""Tests for credential and token resolution module (tools/credentials.py)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from tools import credentials


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure tests run with a clean environment for tokens."""
    for var in (
        "AGENT_SQUARE_TOKEN",
        "SQUARE_TOKEN",
        "AGENT_MARKET_TOKEN",
        "MARKET_TOKEN",
        "AGENT_CITY_TOKEN",
        "CITY_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)


def test_get_square_token_env_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    # AGENT_ prefix takes precedence
    monkeypatch.setenv("AGENT_SQUARE_TOKEN", "1f916_sk_agent_priority")
    monkeypatch.setenv("SQUARE_TOKEN", "1f916_sk_fallback")
    assert credentials.get_square_token() == "1f916_sk_agent_priority"
    assert credentials.has_square_access() is True


def test_get_square_token_standard_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SQUARE_TOKEN", "1f916_sk_standard_env")
    assert credentials.get_square_token() == "1f916_sk_standard_env"
    assert credentials.has_square_access() is True


def test_get_market_token_env_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_MARKET_TOKEN", "1f3ea_sk_agent_priority")
    monkeypatch.setenv("MARKET_TOKEN", "1f3ea_sk_fallback")
    assert credentials.get_market_token() == "1f3ea_sk_agent_priority"
    assert credentials.has_market_access() is True


def test_get_market_token_standard_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKET_TOKEN", "1f3ea_sk_standard_env")
    assert credentials.get_market_token() == "1f3ea_sk_standard_env"
    assert credentials.has_market_access() is True


def test_get_city_token_env_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_CITY_TOKEN", "1f3d9_sk_agent_priority")
    monkeypatch.setenv("CITY_TOKEN", "1f3d9_sk_fallback")
    assert credentials.get_city_token() == "1f3d9_sk_agent_priority"
    assert credentials.has_city_access() is True


def test_fallback_to_secrets_module() -> None:
    class MockSecrets:
        SQUARE_TOKEN = "1f916_sk_from_secrets"
        MARKET_TOKEN = "1f3ea_sk_from_secrets"
        CITY_TOKEN = "1f3d9_sk_from_secrets"

    with patch.object(credentials, "_load_secrets_module", return_value=MockSecrets()):
        assert credentials.get_square_token() == "1f916_sk_from_secrets"
        assert credentials.get_market_token() == "1f3ea_sk_from_secrets"
        assert credentials.get_city_token() == "1f3d9_sk_from_secrets"
        assert credentials.has_square_access() is True
        assert credentials.has_market_access() is True
        assert credentials.has_city_access() is True


def test_missing_token_returns_empty_string() -> None:
    with patch.object(credentials, "_load_secrets_module", return_value=None):
        assert credentials.get_square_token() == ""
        assert credentials.get_market_token() == ""
        assert credentials.get_city_token() == ""
        assert credentials.has_square_access() is False
        assert credentials.has_market_access() is False
        assert credentials.has_city_access() is False


def test_mask_token_square_format() -> None:
    token = "1f916_sk_FAKEtestTOKENnotREAL59e2"
    masked = credentials.mask_token(token)
    assert masked == "1f916_sk_...59e2"
    assert token not in masked
    assert "FAKEtest" not in masked


def test_mask_token_market_format() -> None:
    token = "1f3ea_sk_FAKEtestTOKENnotREAL1234"
    masked = credentials.mask_token(token)
    assert masked == "1f3ea_sk_...1234"
    assert token not in masked


def test_mask_token_city_format() -> None:
    token = "1f3d9_sk_FAKEtestTOKENnotREAL9999"
    masked = credentials.mask_token(token)
    assert masked == "1f3d9_sk_...9999"
    assert token not in masked


def test_mask_token_generic_and_edge_cases() -> None:
    assert credentials.mask_token(None) == "(none)"
    assert credentials.mask_token("") == "(none)"
    assert credentials.mask_token("   ") == "(none)"
    assert credentials.mask_token("short") == "***"
    assert credentials.mask_token("12345678") == "***"

    generic_long = "supersecretcustomtoken1234"
    masked_generic = credentials.mask_token(generic_long)
    assert masked_generic.startswith("supersec")
    assert masked_generic.endswith("1234")
    assert generic_long not in masked_generic
