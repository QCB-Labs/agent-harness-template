"""Trust anchors must resolve outside the repo, on Windows and POSIX alike.

An anchor inside the repository is writable by the agent, which voids whatever
it anchors: the agent could install its own operator key, or add its own
approved correspondent.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tools import names
from tools import paths


# --- containment ----------------------------------------------------------

def test_assert_outside_repo_rejects_in_repo_paths() -> None:
    for inside in (
        paths.REPO_ROOT / "operator.pub",
        paths.REPO_ROOT / names.memory("private") / "operator.pub",
        paths.REPO_ROOT,
    ):
        with pytest.raises(RuntimeError, match="inside the repository"):
            paths.assert_outside_repo(inside)


def test_assert_outside_repo_accepts_external_paths(tmp_path: Path) -> None:
    paths.assert_outside_repo(tmp_path / "operator.pub")  # must not raise


def test_default_trust_anchor_is_outside_the_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_TRUST_DIR", raising=False)
    paths.assert_outside_repo(paths.trust_anchor_dir())


def test_default_session_dir_is_outside_the_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_SESSION_DIR", raising=False)
    paths.assert_outside_repo(paths.session_dir())


# --- overrides ------------------------------------------------------------

def test_trust_dir_override_is_honoured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_TRUST_DIR", str(tmp_path / "anchor"))
    assert paths.trust_anchor_dir() == tmp_path / "anchor"
    assert paths.operator_pubkey_path() == tmp_path / "anchor" / "operator.pub"
    assert paths.correspondents_path() == tmp_path / "anchor" / "correspondents.json"


def test_session_dir_override_is_honoured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_SESSION_DIR", str(tmp_path / "session"))
    assert paths.session_token_path() == (
        tmp_path / "session" / "operator_session.json"
    )


# --- platform defaults ----------------------------------------------------

def test_platform_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_TRUST_DIR", raising=False)
    monkeypatch.delenv("AGENT_SESSION_DIR", raising=False)
    trust = paths.trust_anchor_dir()
    session = paths.session_dir()

    if sys.platform.startswith("win"):
        assert "agent" in str(trust).lower()
        assert str(trust).lower().startswith(
            __import__("os").environ.get("PROGRAMDATA", "c:\\programdata").lower()
        )
        # The session dir is user-owned by necessity; the trust anchor is not.
        assert str(session).lower() != str(trust).lower()
    else:
        assert trust == Path("/etc/agent/trust")
        assert str(session).startswith(("/run/user/", "/tmp/", "/"))


def test_trust_anchor_is_not_user_writable_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """%LOCALAPPDATA% and $HOME are not trust anchors — the agent runs as that user."""
    monkeypatch.delenv("AGENT_TRUST_DIR", raising=False)
    trust = str(paths.trust_anchor_dir()).lower()
    assert "localappdata" not in trust
    assert str(Path.home()).lower() not in trust


# --- peer handles name files, so they are validated -----------------------

def test_peer_pubkey_path_accepts_valid_handles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_TRUST_DIR", str(tmp_path))
    assert paths.peer_pubkey_path("peer").name == "peer.pub"
    assert paths.peer_pubkey_path("WANDERER").name == "wanderer.pub"


def test_peer_pubkey_path_rejects_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_TRUST_DIR", str(tmp_path))
    for payload in ("../operator", "../../etc/passwd", "a/b", "", ".", "..",
                    "x" * 100, "with space", "semi;colon"):
        with pytest.raises(ValueError):
            paths.peer_pubkey_path(payload)
