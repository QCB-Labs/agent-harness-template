"""Wake commit path guard, safe budget writes and receipt pruning, using invented state only."""
import datetime
import json
from pathlib import Path
import threading

import pytest

from tools import commit, harness_mcp as h, names, phase

A = "11111111-1111-4111-8111-111111111111"
B = "22222222-2222-4222-8222-222222222222"


def test_wake_rejects_all_foreign_files_before_git(tmp_path, monkeypatch):
    monkeypatch.setattr(commit, "_log_timing", lambda *a: None)
    called = []
    monkeypatch.setattr(commit, "_run", lambda *a, **kw: called.append(a) or (_ for _ in ()).throw(AssertionError("git called")))
    foreign = [names.rel("governance"), "tools/x.py", ".gitignore"]
    result = commit.commit_wake(tmp_path, foreign, "Example", lambda: {}, threading.Lock())
    assert result["status"] == "REFUSED"
    assert all(name in result["reason"] for name in foreign)
    assert called == []


@pytest.mark.parametrize("name", [names.rel("identity"), names.rel("goals"), "budget.json",
                                  names.dir_name("memory") + "/x.md"])
def test_wake_files_pass_the_path_guard(tmp_path, monkeypatch, name):
    monkeypatch.setattr(commit, "_log_timing", lambda *a: None)
    result = commit.commit_wake(tmp_path, [name], "Example", lambda: {}, threading.Lock())
    assert "Not wake files" not in str(result.get("reason", ""))


def test_budget_replace_failure_preserves_original(tmp_path, monkeypatch):
    monkeypatch.setattr(h, "REPO", tmp_path)
    path = tmp_path / "budget.json"
    path.write_text('{"wake_count": 42}')
    seen = []
    def replace(source, target):
        assert Path(source).parent == target.parent
        seen.append(json.loads(Path(source).read_text()))
        raise OSError("simulated replace failure")
    monkeypatch.setattr(h.os, "replace", replace)
    with pytest.raises(OSError, match="simulated"):
        h._save_budget({"wake_count": 43})
    assert seen[0]["wake_count"] == 43
    assert path.read_text() == '{"wake_count": 42}'
    assert list(tmp_path.iterdir()) == [path]


def test_budget_save_retries_a_briefly_locked_file(tmp_path, monkeypatch):
    # Windows refuses the swap while a reader has budget.json open; it must retry.
    monkeypatch.setattr(h, "REPO", tmp_path)
    real, calls = h.os.replace, []
    def flaky(src, dst):
        calls.append(dst)
        if len(calls) < 3:
            raise PermissionError(32, "in use")
        return real(src, dst)
    monkeypatch.setattr(h.os, "replace", flaky)
    monkeypatch.setattr(h.time, "sleep", lambda s: None)
    h._save_budget({"wake_count": 7})
    assert json.loads((tmp_path / "budget.json").read_text())["wake_count"] == 7
    assert len(calls) == 3


@pytest.mark.parametrize("bad", ["{broken", "[]", '{"wake_count": "bad"}'])
def test_bad_budget_stops_journal_before_writes(tmp_path, monkeypatch, bad):
    monkeypatch.setattr(h, "REPO", tmp_path)
    monkeypatch.setattr(h, "_send_email", lambda **kw: pytest.fail("mail attempted"))
    path = tmp_path / "budget.json"
    path.write_text(bad)
    with pytest.raises(ValueError, match="budget.json"):
        h.write_journal("Example", conversation_id=A)
    assert path.read_text() == bad
    assert list(tmp_path.iterdir()) == [path]


def test_unreadable_budget_stops_before_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(h, "REPO", tmp_path)
    monkeypatch.setattr(h, "_send_email", lambda **kw: False)
    path = tmp_path / "budget.json"
    path.write_text('{"wake_count": 42}')
    original_open = Path.open
    def open_file(target, mode="r", *args, **kwargs):
        if target == path and "r" in mode:
            raise PermissionError("simulated unreadable budget")
        return original_open(target, mode, *args, **kwargs)
    monkeypatch.setattr(Path, "open", open_file)
    with pytest.raises(ValueError, match="budget.json"):
        h.write_journal("Example", conversation_id=A)
    assert list(tmp_path.iterdir()) == [path]
    monkeypatch.setattr(Path, "open", original_open)
    assert json.loads(path.read_bytes())["wake_count"] == 42


def test_pruning_preserves_old_conversation_block(tmp_path, monkeypatch):
    monkeypatch.setattr(phase, "REPO_ROOT", tmp_path)
    now = datetime.datetime(2026, 11, 19, tzinfo=datetime.timezone.utc)
    folder = phase._private_use_root() / A
    folder.mkdir(parents=True)
    for days in [3, 2]:
        (folder / f"old-{days}.json").write_text(json.dumps({"conversation_id": A,
            "private_used_utc": (now - datetime.timedelta(days=days)).isoformat()}))
    monkeypatch.setattr(phase, "_now", lambda: now)
    phase._record_private_use(B)
    assert phase.unknown_email_status(A)["reason"] == "same_conversation_private"
    assert len(list(folder.glob("*.json"))) == 1
    assert not list(folder.glob("old-*.json"))
    phase._record_private_use(B)
    assert len(list((phase._private_use_root() / B).glob("*.json"))) == 1


def test_a_malformed_receipt_does_not_block_a_private_read(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_SESSION_DIR", str(tmp_path / "session"))
    monkeypatch.setattr(phase, "REPO_ROOT", tmp_path)
    root = phase._private_use_root()
    (root / A).mkdir(parents=True)
    (root / A / "bad.json").write_text("{not json", encoding="utf-8")
    phase._record_private_use(B)
    assert phase.unknown_email_status(B)["available"] is False

