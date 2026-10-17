"""Private casefiles: a permanent row per flag, outside his reach.

All text is made up. Every test uses its own cases folder under tmp_path
(conftest turns casefiles off by default). Per source there are two files:
`<kind>-<key>.flags.md` (the harness appends) and `<kind>-<key>.md` (the
operator's judgement, which the harness only reads).
"""
from naming_support import fixture_name
import os
from datetime import datetime, timezone
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tools import backfill_casefiles, casefiles, paths, verdicts
from tools import harness_mcp as harness, names

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
    monkeypatch.setattr(harness, "_cards_patterns", lambda: source / "config/tag-patterns.city.json")
    monkeypatch.setattr(harness, "update_map", lambda entries: None)
    return source


@pytest.fixture
def cases(tmp_path, monkeypatch):
    folder = tmp_path / "cases"
    folder.mkdir()
    monkeypatch.setenv("AGENT_CASES_DIR", str(folder))
    return folder


def _rows(path: Path) -> list[str]:
    return [l for l in path.read_text(encoding="utf-8").splitlines() if l.startswith("| 20")]


def _judge(cases: Path, name: str, words: str = "2026-10-15, operator: 50 notes read; 24 link to opinion.") -> Path:
    path = cases / name
    path.write_text("# Judgement\n\n" + words + "\n", encoding="utf-8")
    return path


WIRE_FLAGS = "resident-made-up-wire.flags.md"
WIRE = "resident-made-up-wire.md"


# --- Where they live ----------------------------------------------------------

def test_casefiles_are_off_by_default_in_tests():
    assert paths.cases_dir() is None


def test_folder_comes_from_the_trust_anchor_pointer(tmp_path, monkeypatch):
    folder = tmp_path / "notes" / "cases"
    folder.mkdir(parents=True)
    trust = tmp_path / "trust"
    trust.mkdir()
    (trust / "cases-dir.txt").write_text(f"{folder}\n", encoding="utf-8")
    monkeypatch.delenv("AGENT_CASES_DIR")
    monkeypatch.setenv("AGENT_TRUST_DIR", str(trust))
    assert paths.cases_dir() == folder.resolve()


def test_a_folder_inside_the_code_repo_is_refused(monkeypatch):
    monkeypatch.setenv("AGENT_CASES_DIR", str(paths.REPO_ROOT / fixture_name("records")))
    assert paths.cases_dir() is None


def test_a_folder_inside_the_active_data_repo_is_refused(repo, monkeypatch):
    # Review: the harness can run on a data repository other than its code.
    inside = repo / fixture_name("records") / "residents"
    monkeypatch.setenv("AGENT_CASES_DIR", str(inside))
    assert paths.cases_dir(also_outside=repo) is None
    harness.upsert_resident(handle="made-up-wire", impression="Runs a neutral newsroom.")
    assert not (inside / WIRE_FLAGS).exists()


def test_a_missing_folder_is_off_and_never_created(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_CASES_DIR", str(tmp_path / "absent"))
    assert paths.cases_dir() is None
    assert not (tmp_path / "absent").exists()


# --- Rows ---------------------------------------------------------------------

def test_first_flag_creates_the_flags_file_and_never_a_judgement(repo, cases):
    harness.upsert_resident(handle="made-up-wire", impression="Runs a neutral newsroom.")
    text = (cases / WIRE_FLAGS).read_text(encoding="utf-8")
    assert text.startswith("# resident made-up-wire: made-up-wire\n")
    assert "| Date (UTC) | File | Phrase | Line as saved |\n|---|---|---|---|\n" in text
    (row,) = _rows(cases / WIRE_FLAGS)
    assert row.endswith(fixture_name("| records/case-files/made-up-wire.md | neutral | Runs a neutral newsroom. |"))
    assert verdicts.FLAG not in row
    assert not (cases / WIRE).exists()


def test_saving_the_same_impression_twice_adds_no_row(repo, cases):
    for _ in range(2):
        harness.upsert_resident(handle="made-up-wire", impression="Runs a neutral newsroom.")
    assert len(_rows(cases / WIRE_FLAGS)) == 1


def test_rows_are_only_ever_appended(repo, cases):
    harness.upsert_resident(handle="made-up-wire", impression="Runs a neutral newsroom.")
    before = (cases / WIRE_FLAGS).read_bytes()
    harness.upsert_resident(handle="made-up-wire", impression="An accurate feed, it says.")
    assert (cases / WIRE_FLAGS).read_bytes().startswith(before)


def test_a_reworded_line_keeps_its_old_row_and_the_flag_leaves_his_file(repo, cases):
    harness.upsert_resident(handle="made-up-wire", impression="Runs a neutral newsroom.")
    harness.upsert_resident(handle="made-up-wire", impression="Runs a newsroom of headlines.")
    dossier = (repo / fixture_name("records/case-files/made-up-wire.md")).read_text(encoding="utf-8")
    assert verdicts.MARK_PREFIX not in dossier
    assert len(_rows(cases / WIRE_FLAGS)) == 1
    harness.upsert_resident(handle="made-up-wire", impression="An accurate feed, it says.")
    rows = _rows(cases / WIRE_FLAGS)
    assert len(rows) == 2 and "| accurate |" in rows[1]


def test_a_note_without_an_impression_adds_no_row(repo, cases):
    harness.upsert_resident(handle="made-up-wire", note="Says its feed is neutral.", source="note:1")
    assert not (cases / WIRE_FLAGS).exists()


def test_site_inspection_rows_go_to_the_place_casefile(repo, cases):
    harness.record_place(place_id=9001, slug="made-up-room", physical_description="One room.",
                              impression="An accurate and verified feed.")
    (row,) = _rows(cases / "place-9001.flags.md")
    assert "| accurate, verified |" in row and "9001-made-up-room.md" in row


def test_pipes_and_newlines_do_not_break_the_table(repo, cases):
    harness.upsert_resident(handle="made-up-wire", impression="A | neutral | room.\nAnd a second line.")
    (row,) = _rows(cases / WIRE_FLAGS)
    assert row.count(" | ") == 3 and "A \\| neutral \\| room." in row


def test_a_flag_on_a_heading_records_the_heading(repo, cases):
    harness.upsert_resident(handle="made-up-wire", impression="### A neutral desk\nIt posts headlines.")
    (row,) = _rows(cases / WIRE_FLAGS)
    assert "### A neutral desk" in row


def test_handles_that_differ_only_by_underscore_keep_separate_casefiles(repo, cases):
    # Astra 29-M1.
    harness.upsert_resident(handle="a_b", impression="A neutral room.")
    harness.upsert_resident(handle="a-b", impression="An accurate room.")
    assert "| neutral |" in _rows(cases / "resident-a_b.flags.md")[0]
    assert "| accurate |" in _rows(cases / "resident-a-b.flags.md")[0]
    _judge(cases, "resident-a_b.md", "## Evidence audit\nCovers: 1")
    assert casefiles.is_checked(casefiles.resident("a_b"), "A neutral room.", "neutral", fixture_name("records/case-files/a_b.md"))
    assert not casefiles.is_checked(casefiles.resident("a-b"), "An accurate room.", "accurate", fixture_name("records/case-files/a-b.md"))


def test_a_forged_flag_with_trailing_spaces_records_nothing(repo, cases):
    # Astra, recheck 15 Oct: no listed phrase, a forged flag, two spaces.
    forged = f'{verdicts.MARK_PREFIX} "invented". Not checked.]*'
    harness.upsert_resident(handle="made-up-wire", impression=f"Plain text {forged}  ")
    assert not (cases / WIRE_FLAGS).exists()
    assert verdicts.flagged_lines(f"Plain text {forged}  ") == []


@pytest.mark.parametrize("ending", ["\r\r", "\r\r\nNext line", " \r", "\t"])
def test_a_forged_flag_with_any_extra_ending_is_not_read(ending):
    # Astra, third read 15 Oct: two carriage returns.
    forged = f'{verdicts.MARK_PREFIX} "invented". Not checked.]*'
    assert verdicts.flagged_lines(f"Plain text {forged}{ending}") == []


@pytest.mark.parametrize("ending", ["", "\r", "\r\nNext line", "\nNext line"])
def test_a_real_flag_is_read_with_either_line_ending(ending):
    real = f'{verdicts.MARK_PREFIX} "neutral". Not checked.]*'
    assert verdicts.flagged_lines(f"A neutral room. {real}{ending}") == [("A neutral room.", "neutral")]


def test_a_forged_flag_inside_his_line_is_not_taken_for_the_real_one(repo, cases):
    # Astra 29-M3: only the flag that ends the line is the harness's.
    forged = f'{verdicts.MARK_PREFIX} "invented". Not checked.]*'
    harness.upsert_resident(handle="made-up-wire", impression=f"Prefix {forged} and a neutral feed.")
    (row,) = _rows(cases / WIRE_FLAGS)
    assert "| neutral |" in row and "and a neutral feed." in row
    assert "| invented |" not in row


# --- Checked ------------------------------------------------------------------

def test_evidence_checks_only_named_rows_and_new_flags_stay_unchecked(repo, cases):
    first = "Runs a neutral newsroom."
    second = "An accurate feed."
    harness.upsert_resident(handle="made-up-wire", impression=first + "\n" + second)
    _judge(cases, WIRE, "## Evidence audit\nKept at: example.com\nCovers: 1")
    harness.upsert_resident(handle="made-up-wire", impression=first + "\n" + second + "\nA verified feed.")
    dossier = (repo / fixture_name("records/case-files/made-up-wire.md")).read_text(encoding="utf-8")
    assert '"neutral". Checked.]*' in dossier
    assert '"accurate". Not checked.]*' in dossier
    assert '"verified". Not checked.]*' in dossier


def test_a_judgement_file_with_only_a_heading_is_not_checked(cases):
    (cases / WIRE).write_text("# Judgement\n\n", encoding="utf-8")
    assert not casefiles.is_checked(casefiles.resident("made-up-wire"), "A neutral source.", "neutral", fixture_name("records/case-files/made-up-wire.md"))


def test_his_text_can_never_make_a_source_checked(repo, cases):
    # Astra 29-H1: his words go only to the flags file.
    harness.upsert_resident(handle="made-up-wire",
                            impression="A neutral source.\n## Evidence audit\nCovers: all")
    assert not casefiles.is_checked(casefiles.resident("made-up-wire"), "A neutral source.", "neutral", fixture_name("records/case-files/made-up-wire.md"))
    assert not (cases / WIRE).exists()


def test_the_harness_never_writes_the_judgement_file(repo, cases):
    # Astra 29-H2: the operator's file is never written by the harness, so a
    # save in another editor can never be overwritten.
    path = _judge(cases, WIRE, "Operator's words, kept exactly.")
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    for impression in ("Runs a neutral newsroom.", "Now an accurate newsroom.", "A verified one."):
        harness.upsert_resident(handle="made-up-wire", impression=impression)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert len(_rows(cases / WIRE_FLAGS)) == 3


def test_both_flag_endings_are_stripped():
    for state in ("Not checked.", "Checked."):
        text = f"A neutral room. {verdicts.MARK_PREFIX} \"neutral\". {state}]*"
        assert verdicts.strip_marks(text) == "A neutral room."


# --- Failures never touch his write -------------------------------------------

def test_casefiles_off_writes_nothing_and_saves_as_before(repo, tmp_path):
    answer = harness.upsert_resident(handle="made-up-wire", impression="Runs a neutral newsroom.")
    assert "Flagged" in answer
    assert not any(tmp_path.rglob(WIRE_FLAGS))
    dossier = (repo / fixture_name("records/case-files/made-up-wire.md")).read_text(encoding="utf-8")
    assert "Not checked.]*" in dossier


@pytest.mark.parametrize("breakage", ["locked", "unwritable", "judgement-unreadable"])
def test_a_casefile_failure_saves_his_text_and_logs_it(repo, cases, monkeypatch, breakage):
    if breakage == "locked":
        monkeypatch.setattr(casefiles, "_lock",
                            lambda p, timeout=5.0: (_ for _ in ()).throw(TimeoutError("casefile lock")))
    elif breakage == "unwritable":
        (cases / WIRE_FLAGS).mkdir()  # a folder where the file should be
    else:
        (cases / WIRE).write_bytes(b"\xff\xfe not text")
    answer = harness.upsert_resident(handle="made-up-wire", impression="Runs a neutral newsroom.")
    assert "Flagged" in answer and "REFUSED" not in answer
    dossier = (repo / fixture_name("records/case-files/made-up-wire.md")).read_text(encoding="utf-8")
    assert "Runs a neutral newsroom." in dossier and "Not checked.]*" in dossier
    log = (paths.operator_log_dir() / "casefile-errors.log").read_text(encoding="utf-8")
    assert log.strip() and str(cases) not in log and "neutral" not in log


def test_no_log_is_written_inside_a_repository(repo, cases, monkeypatch):
    monkeypatch.setenv("AGENT_SESSION_DIR", str(repo / fixture_name("records") / "private" / "session"))
    casefiles.log_failure("probe", RuntimeError("x"), repo)
    assert not (repo / fixture_name("records") / "private" / "logs").exists()


# --- Never reachable by him ---------------------------------------------------

def test_no_tool_result_names_the_casefile_folder(repo, cases):
    answers = [
        harness.upsert_resident(handle="made-up-wire", impression="Runs a neutral newsroom."),
        harness.record_place(place_id=9001, slug="made-up-room", physical_description="One room.",
                                  impression="An accurate feed."),
        harness.read_resident(handle="made-up-wire"),
    ]
    for answer in answers:
        assert str(cases) not in answer and "cases" not in answer.lower()


def test_recall_and_cards_never_return_casefile_text(repo, cases):
    harness.upsert_resident(handle="made-up-wire", impression="Runs a neutral newsroom.")
    _judge(cases, WIRE, "SECRETJUDGEMENTWORD")
    for card in (repo / fixture_name("records")).rglob("*"):
        if card.is_file():
            assert "SECRETJUDGEMENTWORD" not in card.read_text(encoding="utf-8", errors="ignore")
    answer = harness.recall(tags=["resident:made-up-wire"])
    assert "SECRETJUDGEMENTWORD" not in json.dumps(answer, default=str)


def test_only_the_save_paths_use_casefiles():
    source = (ROOT / "tools/harness_mcp.py").read_text(encoding="utf-8")
    funcs = set()
    for m in re.finditer(r"casefiles\.", source):
        funcs.add(re.findall(r"^def (\w+)\(", source[:m.start()], flags=re.M)[-1])
    assert funcs == {"_record_flags", "upsert_resident", "record_place"}
    for other in (ROOT / "tools").glob("*.py"):
        if other.name in {"harness_mcp.py", "casefiles.py", "backfill_casefiles.py"}:
            continue
        text = other.read_text(encoding="utf-8")
        assert not re.search(r"import casefiles|casefiles\.", text), other.name


# --- Backfill -----------------------------------------------------------------

def _git(root, *args):
    subprocess.run(["git", "-c", "user.name=F", "-c", "user.email=f@example.com",
                    "-c", "core.hooksPath=unused", *args], cwd=root, check=True,
                   capture_output=True)


def _history(tmp_path):
    root = tmp_path / "hist"
    (root / fixture_name("records/case-files")).mkdir(parents=True)
    shutil.copytree(ROOT / "config", root / "config")
    _git(root, "init", "-q")
    dossier = root / fixture_name("records/case-files/made-up-wire.md")
    dossier.write_text("# Resident\n\n## Impression\nKeeps lamps.\n", encoding="utf-8")
    _git(root, "add", "-A"); _git(root, "commit", "-qm", "one")
    dossier.write_text("# Resident\n\n## Impression\nRuns a neutral newsroom.\n", encoding="utf-8")
    _git(root, "add", "-A"); _git(root, "commit", "-qm", "two")
    dossier.write_text("# Resident\n\n## Impression\nRuns a neutral newsroom.\nAn accurate feed.\n",
                       encoding="utf-8")
    _git(root, "add", "-A"); _git(root, "commit", "-qm", "three")
    return root


def test_backfill_records_each_line_once_at_its_first_commit(tmp_path, cases, monkeypatch, capsys):
    root = _history(tmp_path)
    monkeypatch.chdir(root)
    assert backfill_casefiles.main([]) == 0
    rows = _rows(cases / WIRE_FLAGS)
    assert len(rows) == 2
    assert "Runs a neutral newsroom." in rows[0] and "An accurate feed." in rows[1]
    assert backfill_casefiles.main([]) == 0  # twice is safe
    assert len(_rows(cases / WIRE_FLAGS)) == 2
    assert not (cases / WIRE).exists()


def test_backfill_dry_run_prints_counts_only(tmp_path, cases, monkeypatch, capsys):
    root = _history(tmp_path)
    monkeypatch.chdir(root)
    assert backfill_casefiles.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "resident made-up-wire: 2 flagged line(s)" in out
    assert "newsroom" not in out and str(cases) not in out
    assert not any(cases.iterdir())


def test_backfill_refuses_when_casefiles_are_off(tmp_path, monkeypatch, capsys):
    root = _history(tmp_path)
    monkeypatch.chdir(root)
    assert backfill_casefiles.main([]) == 1


def test_backfill_reports_rows_it_could_not_write(tmp_path, cases, monkeypatch, capsys):
    root = _history(tmp_path)
    monkeypatch.chdir(root)
    (cases / WIRE_FLAGS).mkdir()  # unwritable
    assert backfill_casefiles.main([]) == 1
    assert "could not be written" in capsys.readouterr().out


@pytest.mark.parametrize("selection, expected", [
    ("3", {3}), ("1-3", {1, 2, 3}), ("1, 4, 7-9", {1, 4, 7, 8, 9}),
    ("1,4,7–9", {1, 4, 7, 8, 9}), ("all", set(range(1, 10))),
])
def test_covers_forms(cases, selection, expected):
    source = casefiles.resident("invented-audit")
    casefiles.append_flags(source, "Audit", fixture_name("records/example.md"),
                           [(f"Line {n}", "neutral") for n in range(1, 10)],
                           when=datetime(2020, 1, 1, tzinfo=timezone.utc))
    _judge(cases, "resident-invented-audit.md", f"## Evidence audit\nCovers: {selection}")
    assert casefiles.covered_rows(source) == {
        fixture_name(f"| records/example.md | neutral | Line {n} |") for n in expected}


@pytest.mark.parametrize("selection", ["", "0", "1,", "-1", "3-1", "1-2 extra", "all extra", "1, bad"])
def test_invalid_covers_logs_and_covers_nothing(cases, monkeypatch, selection):
    source = casefiles.resident("invented-audit")
    _judge(cases, "resident-invented-audit.md", f"## Evidence audit\nCovers: {selection}")
    logged = []
    monkeypatch.setattr(casefiles, "log_failure", lambda *args: logged.append(args))
    assert casefiles.covered_rows(source) == set()
    assert len(logged) == 1


def test_evidence_entries_union_and_other_sections_are_ignored(cases):
    source = casefiles.resident("invented-audit")
    casefiles.append_flags(source, "Audit", fixture_name("records/example.md"),
                           [(f"Line {n}", "neutral") for n in range(1, 5)])
    _judge(cases, "resident-invented-audit.md",
           "Covers: all\n## Notes\nCovers: all\n## Evidence one\nCovers: 1\n"
           "### Detail\nCovers: 3\n## Other\nCovers: 2\n## Evidence two\nCovers: 4")
    assert casefiles.covered_rows(source) == {
        fixture_name(f"| records/example.md | neutral | Line {n} |") for n in (1, 3, 4)}


def test_all_uses_operator_mtime_not_flags_mtime(cases):
    source = casefiles.resident("invented-audit")
    for year in (2020, 2022):
        casefiles.append_flags(source, "Audit", fixture_name("records/example.md"), [(str(year), "neutral")],
                               when=datetime(year, 1, 1, tzinfo=timezone.utc))
    path = _judge(cases, "resident-invented-audit.md", "## Evidence audit\nCovers: all")
    cutoff = datetime(2021, 1, 1, tzinfo=timezone.utc).timestamp()
    os.utime(path, (cutoff, cutoff))
    assert casefiles.covered_rows(source) == {fixture_name("| records/example.md | neutral | 2020 |")}


def test_free_text_checks_no_rows(repo, cases):
    harness.upsert_resident(handle="made-up-wire", impression="A neutral room.")
    _judge(cases, WIRE)
    harness.upsert_resident(handle="made-up-wire", impression="A neutral room.")
    assert "Not checked.]*" in (repo / fixture_name("records/case-files/made-up-wire.md")).read_text(encoding="utf-8")


def test_place_evidence_uses_existing_filename(repo, cases):
    kwargs = dict(place_id=9001, physical_description="One room.", impression="An accurate feed.")
    harness.record_place(slug="invented-room", **kwargs)
    _judge(cases, "place-9001.md", "## Evidence audit\nCovers: 1")
    harness.record_place(slug="renamed-room", **kwargs)
    text = (repo / fixture_name("records/site-inspections/places/9001-invented-room.md")).read_text(encoding="utf-8")
    assert '"accurate". Checked.]*' in text


def test_matching_uses_saved_cells_and_exact_file(cases):
    source = casefiles.resident("invented-audit")
    casefiles.append_flags(source, "Audit", fixture_name("records/example.md"), [("A | neutral room.  ", "neutral")])
    _judge(cases, "resident-invented-audit.md", "## Evidence audit\nCovers: 1")
    assert casefiles.is_checked(source, "A | neutral room.", "neutral", fixture_name("records/example.md"))
    assert not casefiles.is_checked(source, "A | neutral room.", "neutral", fixture_name("records/other.md"))


def test_the_row_mark_asks_about_is_the_row_the_casefile_records():
    # mark() asks about (line, phrases); _record_flags later stores what
    # flagged_lines() reads back from the marked text. They must be equal,
    # or no evidence could ever check a flag.
    from tools import verdicts
    from tools.harness_mcp import REPO
    claims, _ = verdicts.load_words(REPO)
    phrase = claims[0]
    text = f"First line.\nThe room is {phrase} today.\nLast line."
    asked = []
    marked, found = verdicts.mark(REPO, text, checked=lambda line, phrases: asked.append((line, phrases)) or False)
    assert found and asked
    assert asked == verdicts.flagged_lines(marked)
