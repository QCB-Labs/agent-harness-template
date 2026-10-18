"""The private pattern file is read once per scan, with unchanged results.

Uses a fake pattern file only (AGENT_PRIVATE_PATTERNS); the real one is
never read here.
"""

from __future__ import annotations
from naming_support import fixture_name

import subprocess
from pathlib import Path

import pytest

from tools import commit_scope, outgoing_check


@pytest.fixture
def patterns(tmp_path, monkeypatch):
    path = tmp_path / "patterns.txt"
    path.write_text("# comment line\n\nZanzibarQuill\n  ochrefinch  \n", encoding="utf-8")
    monkeypatch.setenv("AGENT_PRIVATE_PATTERNS", str(path))
    return path


def _counting_reads(monkeypatch):
    calls = {"n": 0}
    real = Path.read_text

    def counted(self, *args, **kwargs):
        if self.name == "patterns.txt":
            calls["n"] += 1
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted)
    return calls


@pytest.mark.parametrize("text", [
    "nothing private here",
    "a zanzibarquill in the middle",
    "ZANZIBARQUILL shouted",
    "ochrefinch then zanzibarquill",
    "# comment line",
    "",
])
def test_loaded_check_matches_the_old_check(patterns, text):
    loaded = outgoing_check.load_private_patterns()
    assert outgoing_check.check_loaded_patterns(text, loaded) == \
        outgoing_check._check_private_patterns(text)


def test_offsets_are_reported_not_text(patterns):
    found = outgoing_check.check_loaded_patterns("ab zanzibarquill",
                                                 outgoing_check.load_private_patterns())
    assert [f.detail for f in found] == ["matched at character offset 3"]
    assert "zanzibarquill" not in found[0].detail


def test_comments_and_blanks_are_not_patterns(patterns):
    assert outgoing_check.load_private_patterns() == ["zanzibarquill", "ochrefinch"]


def test_unreadable_file_is_unavailable(patterns, monkeypatch):
    real = Path.read_text

    def refuse(self, *args, **kwargs):
        if self.name == "patterns.txt":
            raise PermissionError("denied")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", refuse)
    assert outgoing_check.load_private_patterns() is None
    assert [f.kind for f in outgoing_check._check_private_patterns("x")] == ["patterns-unavailable"]


def test_undecodable_file_is_unavailable(tmp_path, monkeypatch):
    bad = tmp_path / "patterns.txt"
    bad.write_bytes(b"\xff\xfe not utf-8 \xc3")
    monkeypatch.setenv("AGENT_PRIVATE_PATTERNS", str(bad))
    assert outgoing_check.load_private_patterns() is None
    assert [f.kind for f in outgoing_check._check_private_patterns("x")] == ["patterns-unavailable"]


def test_missing_file_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_PRIVATE_PATTERNS", str(tmp_path / "absent.txt"))
    assert outgoing_check.load_private_patterns() is None
    found = outgoing_check.check_loaded_patterns("anything", None)
    assert [f.kind for f in found] == ["patterns-unavailable"]
    assert [f.kind for f in outgoing_check._check_private_patterns("x")] == ["patterns-unavailable"]


def test_an_edit_is_seen_by_the_next_scan(patterns):
    assert outgoing_check.check_loaded_patterns(
        "newword", outgoing_check.load_private_patterns()) == []
    patterns.write_text("newword\n", encoding="utf-8")
    assert len(outgoing_check.check_loaded_patterns(
        "newword", outgoing_check.load_private_patterns())) == 1


def test_commit_scope_reads_the_file_once(patterns, tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    subprocess.run(["git", "init", "-q"], check=True)
    Path(fixture_name("records")).mkdir()
    Path(fixture_name("records/a.md")).write_text("line\n" * 200, encoding="utf-8")
    subprocess.run(["git", "add", "--", fixture_name("records/a.md")], check=True)
    calls = _counting_reads(monkeypatch)
    assert commit_scope.main() == 0
    assert calls["n"] == 1


def test_commit_scope_missing_file_still_refuses(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AGENT_PRIVATE_PATTERNS", str(tmp_path / "absent.txt"))
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    subprocess.run(["git", "init", "-q"], check=True)
    Path(fixture_name("records")).mkdir()
    Path(fixture_name("records/a.md")).write_text("clean\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", fixture_name("records/a.md")], check=True)
    assert commit_scope.main() == 1
