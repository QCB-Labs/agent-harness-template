"""Staged scope and privacy decisions in disposable repositories only."""
from naming_support import fixture_name
from pathlib import Path
import subprocess

import pytest

from tools import commit_scope


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.chdir(root)
    git("init", "-q")
    pattern_file = tmp_path / "patterns.txt"
    pattern_file.write_text("zanzibarquill\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_PRIVATE_PATTERNS", str(pattern_file))
    return root


def git(*arguments):
    return subprocess.run(["git", *arguments], check=True, capture_output=True)


def stage(name, content="clean\n"):
    path = Path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    git("add", "--", name)


def commit():
    # Fixture commits have no hooks or external identity requirements.
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.com",
        "-c", "core.hooksPath=unused-hooks", "commit", "-qm", "fixture")


@pytest.mark.parametrize("name", [
    fixture_name("records/a.md"), fixture_name("records/nested/a.md"), fixture_name("DIRECTIVES.md"), fixture_name("COMPLETED-DIRECTIVES.md"),
    "budget.json", fixture_name("records/mail-archive/a.md"),
])
def test_memory_paths(repo, capsys, name):
    stage(name)
    assert commit_scope.main() == 0
    assert capsys.readouterr().out == "memory-only commit: privacy checks passed on 1 files\n"


def test_clean_count(repo, capsys):
    stage(fixture_name("records/a.md"))
    stage("budget.json")
    assert commit_scope.main() == 0
    assert "passed on 2 files" in capsys.readouterr().out


@pytest.mark.parametrize("name", [
    "tools/x.py", "tests/x.py", "config/x.json", "AGENTS.md", fixture_name("STANDING-ORDERS.md"),
    fixture_name("PERSONNEL-FILE.md"), "communications/archive/a.md", ".gitignore", "README.md", fixture_name("records.md"), "recordsx/a.md",
    fixture_name("DIRECTIVES.md.bak"),
])
def test_non_memory(repo, capsys, name):
    stage(fixture_name("records/a.md"))
    stage(name)
    assert commit_scope.main() == 2
    assert capsys.readouterr().out == f"full suite: {name}\n"


def test_capitalised_memory_folder_alone(repo, capsys):
    # Alone: on a case-insensitive disk, staging records/a.md first would make
    # Records/a.md the same file, and git would file it under records/.
    stage(fixture_name("Records/a.md"))
    assert commit_scope.main() == 2
    assert capsys.readouterr().out == fixture_name("full suite: Records/a.md\n")


def test_empty(repo, capsys):
    assert commit_scope.main() == 2
    assert capsys.readouterr().out == "full suite: nothing staged\n"


def test_deleted(repo, capsys):
    stage(fixture_name("records/a.md"))
    commit()
    git("rm", fixture_name("records/a.md"))
    assert commit_scope.main() == 0
    assert "passed on 1 files" in capsys.readouterr().out


@pytest.mark.parametrize("source,target", [
    ("tools/a.py", fixture_name("records/a.md")), (fixture_name("records/a.md"), "tools/a.py"),
])
def test_rename_checks_both_names(repo, capsys, source, target):
    stage(source)
    commit()
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    git("mv", source, target)
    assert commit_scope.main() == 2
    assert capsys.readouterr().out == "full suite: tools/a.py\n"


@pytest.mark.parametrize("content", [
    "zanzibarquill", "C:" + "/Users/" + "invented/person.txt",
    "1f3d9_sk_" + "a" * 32,
    # Every token family the outgoing check knows (assembled, so this file
    # holds none of them whole).
    "gh" + "p_" + "A" * 30, "s" + "k-" + "B" * 30, "AI" + "za" + "C" * 30,
    "xo" + "xb-" + "1" * 20,
])
def test_private_text_is_not_printed(repo, capsys, content):
    stage(fixture_name("records/a.md"), "clean\n" + content)
    assert commit_scope.main() == 1
    output = capsys.readouterr().out
    assert output == fixture_name("memory-only commit: privacy check failed\nrecords/a.md:2\n")
    assert content not in output


@pytest.mark.parametrize("staged,working,expected", [
    ("zanzibarquill", "clean", 1), ("clean", "zanzibarquill", 0),
])
def test_index_not_working_copy(repo, capsys, staged, working, expected):
    stage(fixture_name("records/a.md"), staged)
    Path(fixture_name("records/a.md")).write_text(working, encoding="utf-8")
    assert commit_scope.main() == expected
    assert "zanzibarquill" not in capsys.readouterr().out


def test_missing_patterns(repo, monkeypatch, capsys):
    stage(fixture_name("records/a.md"))
    monkeypatch.setenv("AGENT_PRIVATE_PATTERNS", str(repo / "absent"))
    assert commit_scope.main() == 1
    assert capsys.readouterr().out == "memory-only commit: privacy check failed\n"


@pytest.mark.parametrize("content", [
    b"\xff\x00zanzibarquill",
    "zanzibarquill".encode("utf-16"),
    # Without a byte-order mark these decode as UTF-8 with NULs between the
    # letters, and would hide the word from every check (Astra, 15 Oct).
    "zanzibarquill".encode("utf-16-le"),
    "zanzibarquill".encode("utf-16-be"),
    ("1f3d9_sk_" + "a" * 32).encode("utf-16-le"),
])
def test_unreadable_blob_is_refused(repo, capsys, content):
    # Text that can't be read can't be checked, so it is refused.
    stage(fixture_name("records/a.bin"), content)
    assert commit_scope.main() == 1
    out = capsys.readouterr().out
    assert fixture_name("records/a.bin: not UTF-8 text, cannot be checked") in out
    assert "zanzibarquill" not in out


def test_no_git(repo, monkeypatch, capsys):
    monkeypatch.setenv("PATH", "")
    assert commit_scope.main() == 2
    assert capsys.readouterr().out == "full suite: check unavailable\n"


def test_not_repository(repo, monkeypatch):
    monkeypatch.chdir(repo.parent)
    assert commit_scope.main() == 2


@pytest.mark.parametrize("failure", [OSError("hidden"), RuntimeError("hidden")])
def test_unexpected_failure(repo, monkeypatch, capsys, failure):
    def fail(*args):
        raise failure
    monkeypatch.setattr(commit_scope, "_git", fail)
    assert commit_scope.main() == 2
    assert capsys.readouterr().out == "full suite: check unavailable\n"


def test_undecodable_path(repo, monkeypatch, capsys):
    monkeypatch.setattr(commit_scope, "_git", lambda *args: fixture_name(b"records/\xff\0"))
    assert commit_scope.main() == 2
    assert capsys.readouterr().out == "full suite: check unavailable\n"


def test_tracked_hook_commits_clean_memory_without_the_suite(repo, monkeypatch):
    import os
    import sys
    project = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("AGENT_PYTHON", sys.executable)
    monkeypatch.setenv("PYTHONPATH", str(project) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    stage(fixture_name("records/hook-fixture.md"))
    result = git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.com",
                 "-c", "core.hooksPath=" + (project / "hooks").as_posix(),
                 "commit", "-m", "Store fixture memory")
    assert b"memory-only commit: privacy checks passed" in result.stdout + result.stderr
    assert b"Running tests" not in result.stdout + result.stderr
