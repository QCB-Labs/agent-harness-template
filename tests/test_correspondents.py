"""The approved-correspondents allowlist is a trust anchor.

The model passes a key, never an address. An unknown key fails loudly rather
than falling back to anyone, because a quiet fallback is a misdelivery.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import names
from tools import correspondents
from tools.correspondents import UnknownCorrespondent


# Fixtures use example.com throughout. Real addresses belong in the trust
# anchor, never in this repository — it is public, and the pre-commit hook
# blocks the operator's own address for exactly that reason.
FAKE = {"operator": "operator@example.com", "peer": "peer@example.com"}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("AGENT_TRUST_DIR", str(tmp_path / "trust"))
    monkeypatch.setenv("OPERATOR_CORRESPONDENTS", json.dumps(FAKE))


# --- resolution -----------------------------------------------------------

def test_known_keys_resolve() -> None:
    assert correspondents.resolve("peer") == FAKE["peer"]
    assert correspondents.resolve("operator") == FAKE["operator"]


def test_resolution_is_case_and_whitespace_insensitive() -> None:
    assert correspondents.resolve("  PEER ") == FAKE["peer"]


def test_unknown_key_raises_with_no_fallback() -> None:
    with pytest.raises(UnknownCorrespondent) as excinfo:
        correspondents.resolve("attacker")
    assert "not an approved correspondent" in str(excinfo.value)


def test_unknown_peer_is_absent_not_placeheld() -> None:
    """A placeholder pointing at a real person misdelivers real mail.

    The peer's address is not yet known. The key must be absent — failing closed
    with a clear error — rather than aliased to somebody else's inbox.
    """
    assert not correspondents.is_approved("wanderer")
    # A known person's address must map to their own key and nothing else.
    matching = [
        key for key, value in correspondents.load_correspondents().items()
        if value == FAKE["peer"]
    ]
    assert matching == ["peer"], (
        "a correspondent's address is a real person's inbox and must be "
        "reachable only under their own key"
    )


def test_no_key_maps_to_an_empty_or_bogus_address() -> None:
    for key, address in correspondents.load_correspondents().items():
        assert "@" in address and not address.startswith("REPLACE"), (
            f"{key} has a placeholder address — it would send mail nowhere useful"
        )


# --- overrides ------------------------------------------------------------

def test_env_override_takes_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "OPERATOR_CORRESPONDENTS", json.dumps({"wanderer": "wanderer@example.com"})
    )
    assert correspondents.resolve("wanderer") == "wanderer@example.com"
    assert not correspondents.is_approved("peer")  # override replaces


def test_ships_with_no_addresses_in_the_repository() -> None:
    """The repo is public. Real addresses live in the trust anchor only."""
    assert correspondents.DEFAULT_CORRESPONDENTS == {}


def test_with_nothing_installed_no_key_resolves(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fails closed: it can still reply to anyone, it just cannot initiate."""
    monkeypatch.delenv("OPERATOR_CORRESPONDENTS", raising=False)
    monkeypatch.setenv("AGENT_TRUST_DIR", str(tmp_path / "empty"))
    for key in ("operator", "peer", "anyone"):
        assert not correspondents.is_approved(key)


def test_trust_anchor_file_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPERATOR_CORRESPONDENTS", raising=False)
    anchor = tmp_path / "trust"
    anchor.mkdir(parents=True)
    (anchor / "correspondents.json").write_text(
        json.dumps({"wanderer": "wanderer@example.com"}), encoding="utf-8"
    )
    assert correspondents.resolve("wanderer") == "wanderer@example.com"


def test_allowlist_inside_the_repo_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """An agent-writable allowlist is the same hole as a writable public key."""
    from tools import paths

    monkeypatch.delenv("OPERATOR_CORRESPONDENTS", raising=False)
    monkeypatch.setenv("AGENT_TRUST_DIR", str(paths.REPO_ROOT / names.dir_name("memory")))
    with pytest.raises(RuntimeError, match="inside the repository"):
        correspondents.load_correspondents()


# --- validation -----------------------------------------------------------

def test_malformed_address_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPERATOR_CORRESPONDENTS", json.dumps({"x": "not-an-email"}))
    with pytest.raises(ValueError, match="invalid address"):
        correspondents.load_correspondents()


def test_malformed_key_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "OPERATOR_CORRESPONDENTS", json.dumps({"../etc/passwd": "a@b.com"})
    )
    with pytest.raises(ValueError, match="invalid correspondent key"):
        correspondents.load_correspondents()


def test_invalid_json_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPERATOR_CORRESPONDENTS", "{not json")
    with pytest.raises(ValueError, match="not valid JSON"):
        correspondents.load_correspondents()


def test_non_string_key_is_refused() -> None:
    with pytest.raises(UnknownCorrespondent):
        correspondents.resolve(None)  # type: ignore[arg-type]


# --- reverse lookup is routing only ---------------------------------------

def test_address_to_key_classifies_known_senders() -> None:
    assert correspondents.address_to_key(FAKE["peer"]) == "peer"
    assert correspondents.address_to_key(FAKE["peer"].upper()) == "peer"


def test_address_to_key_returns_none_for_strangers() -> None:
    assert correspondents.address_to_key("stranger@example.com") is None
    assert correspondents.address_to_key(None) is None  # type: ignore[arg-type]


def test_bom_prefixed_config_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PowerShell 5.1's Set-Content -Encoding utf8 writes a byte-order mark.

    The allowlist is hand-written on Windows, so a stray BOM is the expected
    case rather than an exotic one. Rejecting it would break the config for
    three invisible bytes.
    """
    monkeypatch.delenv("OPERATOR_CORRESPONDENTS", raising=False)
    anchor = tmp_path / "trust"
    anchor.mkdir(parents=True)
    (anchor / "correspondents.json").write_text(
        json.dumps({"wanderer": "wanderer@example.com"}), encoding="utf-8-sig"
    )
    assert correspondents.resolve("wanderer") == "wanderer@example.com"
