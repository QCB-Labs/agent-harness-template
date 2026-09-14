"""The agent's public name comes from agent-profile.json and nowhere else."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import paths, profile


@pytest.fixture
def profile_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def write(data: object) -> Path:
        path = tmp_path / "agent-profile.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        monkeypatch.setattr(profile, "PROFILE_PATH", path)
        profile.load_profile.cache_clear()
        return path

    yield write
    profile.load_profile.cache_clear()


def test_committed_profile_is_neutral() -> None:
    data = json.loads(profile.PROFILE_PATH.read_text(encoding="utf-8"))
    assert data == {"handle": "agent", "display_name": "Agent"}


def test_profile_values_are_read(profile_file) -> None:
    profile_file({"handle": "bard", "display_name": "The Bard"})
    assert profile.handle() == "bard"
    assert profile.display_name() == "The Bard"


@pytest.mark.parametrize("bad", ["", "Bard", "../x", "a b", "x" * 33])
def test_unsafe_handle_is_refused(profile_file, bad: str) -> None:
    profile_file({"handle": bad, "display_name": "X"})
    with pytest.raises(RuntimeError):
        profile.load_profile()


def test_blank_display_name_is_refused(profile_file) -> None:
    profile_file({"handle": "bard", "display_name": " "})
    with pytest.raises(RuntimeError):
        profile.load_profile()


def test_trust_and_session_folders_follow_the_handle(
    profile_file, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_file({"handle": "bard", "display_name": "The Bard"})
    monkeypatch.delenv("AGENT_TRUST_DIR", raising=False)
    monkeypatch.delenv("AGENT_SESSION_DIR", raising=False)
    assert "bard" in paths.trust_anchor_dir().parts
    assert "bard" in paths.session_dir().parts
