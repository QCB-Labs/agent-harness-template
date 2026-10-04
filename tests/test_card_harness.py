"""Card integration at the harness level; all source text is made up.

Paths and headings come from the naming file, so these tests follow whatever
scheme the repository is using.
"""
from pathlib import Path
import shutil

import pytest

from tools import cards, harness_mcp as harness, names, release

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "cards"
MEMORY = names.dir_name("memory")

# fixture file -> the name this scheme gives it
SOURCES = {
    "goals.md": names.rel("goals"),
    "memory/lessons.md": names.rel("lessons"),
    "memory/city-rules.md": names.rel("city_rules"),
    "memory/open-threads.md": names.rel("open_threads"),
    "memory/journal": names.rel("journal"),
    "memory/residents": names.rel("residents"),
}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    source = tmp_path / "agent"
    source.mkdir()
    for fixture_path, scheme_path in SOURCES.items():
        target = source / scheme_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if (FIXTURE / fixture_path).is_dir():
            shutil.copytree(FIXTURE / fixture_path, target)
        else:
            shutil.copyfile(FIXTURE / fixture_path, target)
    shutil.copyfile(ROOT / "naming.json", source / "naming.json")
    shutil.copytree(ROOT / "config", source / "config")
    (source / names.rel("identity")).write_text("Made-up identity whole.", encoding="utf-8")
    (source / names.rel("governance")).write_text("Made-up rules whole.", encoding="utf-8")
    monkeypatch.setattr(harness, "REPO", source)
    monkeypatch.setattr(harness, "_send_email", lambda **kwargs: False)
    monkeypatch.setattr(harness, "session_active", lambda **kwargs: True)
    return source


@pytest.mark.parametrize("writer", [
    "append_lesson", "propose_city_rule", "update_open_threads",
    "update_goals", "write_journal", "file_unrecorded", "upsert_resident",
])
def test_each_writer_recuts_its_own_files_once(repo, monkeypatch, writer):
    calls = []
    monkeypatch.setattr(harness, "_recut_after_write", lambda logical, path: calls.append((logical, path.relative_to(repo).as_posix())))
    if writer == "append_lesson":
        result = harness.append_lesson("Keep the compass.")
        expected = [("lessons", names.rel("lessons"))]
    elif writer == "propose_city_rule":
        result = harness.propose_city_rule("Make a new bell.")
        expected = [("city_rules", names.rel("city_rules"))]
    elif writer == "update_open_threads":
        result = harness.update_open_threads(names.heading("open_threads") + "\n\n- **Compass** Keep looking.\n")
        expected = [("open_threads", names.rel("open_threads"))]
    elif writer == "update_goals":
        result = harness.update_goals("# Goals\n\n## Compass\nFind it.\n")
        expected = [("goals", names.rel("goals"))]
    elif writer == "write_journal":
        result = harness.write_journal("# Log\n\n## Plan\nLook tomorrow.\n").splitlines()[0]
        expected = [("journal", names.rel("journal") + "/" + result)]
    elif writer == "file_unrecorded":
        dump = repo / names.rel("journal") / "_UNRECORDED-2026-09-30-120000.md"
        dump.write_text("# Log\nRecovered made-up text.", encoding="utf-8")
        result = harness.file_unrecorded(dump.name)
        expected = [("journal", names.rel("journal") + "/" + result)]
    else:
        result = harness.upsert_resident("rin", resident_id=22, note="Said hello.", source="session:test")
        expected = [("residents", names.rel("residents") + "/rin.md"), ("residents", names.rel("residents") + "/index.md")]
    assert calls == expected
    assert not result.startswith("REFUSED")
    assert all((repo / relative).is_file() for _, relative in calls)


def test_recut_failure_keeps_source_write_and_normal_result(repo, monkeypatch):
    harness._ensure_cards()

    def fail(*args, **kwargs):
        raise RuntimeError("made-up sync failure")

    monkeypatch.setattr(cards, "recut_file", fail)
    result = harness.append_lesson("Durable compass lesson.")
    assert not result.startswith("REFUSED")
    assert "Durable compass lesson." in (repo / names.rel("lessons")).read_text(encoding="utf-8")
    assert "made-up sync failure" in (repo / MEMORY / "private" / "card-errors.log").read_text(encoding="utf-8")


def test_a_failing_error_log_cannot_undo_the_write(repo, monkeypatch):
    harness._ensure_cards()

    def fail(*args, **kwargs):
        raise RuntimeError("made-up sync failure")

    monkeypatch.setattr(cards, "recut_file", fail)
    # a file where the private folder should be: the log cannot be written
    (repo / MEMORY / "private").write_text("in the way", encoding="utf-8")
    result = harness.append_lesson("Still written.")
    assert not result.startswith("REFUSED")
    assert "Still written." in (repo / names.rel("lessons")).read_text(encoding="utf-8")


@pytest.mark.parametrize("tool", ["recall", "read_card", "read_wake_card"])
def test_card_tools_bootstrap_and_respond(repo, tool):
    store = repo / MEMORY / "cards" / "cards.jsonl"
    assert not store.exists()
    if tool == "recall":
        result = harness.recall(tags=["kind:goal"])
        assert result["cards"]
        assert (repo / MEMORY / "private" / "card-reads.jsonl").is_file()
        assert not (repo / MEMORY / "cards" / "reads.jsonl").exists()
    elif tool == "read_card":
        result = harness.read_card("goal-0001", history=True)
        assert result["card"]["kind"] == "goal"
        assert result["history"]
    else:
        result = harness.read_wake_card(here_tags=["name:mira"])
        assert "Made-up identity whole." in result["text"]
        assert len(result["sections"]) == 7
        assert all("text" not in section for section in result["sections"])
    assert store.is_file()


def test_recut_bootstraps_missing_store(repo):
    assert not (repo / MEMORY / "cards" / "cards.jsonl").exists()
    harness.append_lesson("Bootstrap this lesson.")
    result = harness.recall(tags=["kind:rule", "kind:reflection"], text="Bootstrap")
    assert result["matched"] == 1


def test_card_reads_leave_store_bytes_identical(repo):
    harness._ensure_cards()
    path = repo / MEMORY / "cards" / "cards.jsonl"
    before = path.read_bytes()
    harness.recall(tags=["kind:goal"])
    harness.read_wake_card(here_tags=["name:mira"])
    assert path.read_bytes() == before


def test_card_privacy_boundary():
    assert not release.is_private(names.memory("cards", "cards.jsonl"))
    assert release.is_private(names.memory("private", "card-reads.jsonl"))
    assert release.is_private(names.memory("private", "card-errors.log"))
