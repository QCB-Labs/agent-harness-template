"""The reliability-verdict mark; all text is made up."""
from naming_support import fixture_name
import json
from pathlib import Path
import shutil

import pytest

from tools import harness_mcp as harness, verdicts, names

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
    return source


WORDS = (["only facts", "does not add opinions", "neutral", "accurate", "verified"], ["neutral ground"])


def test_a_self_description_and_its_restatement_are_both_found():
    assert verdicts.find("The owner reports only facts and does not add opinions.", *WORDS) == [
        "does not add opinions", "only facts"]
    assert verdicts.find("An unblinking, NEUTRAL newsroom.", *WORDS) == ["neutral"]


def test_the_limit_is_part_of_the_test():
    # A tripwire, not a judge: a rewording that avoids the list is missed.
    assert verdicts.find("It tells things as they are.", *WORDS) == []


def test_plain_text_and_an_ignored_phrase_pass():
    assert verdicts.find("A quiet room with a slate and a lamp.", *WORDS) == []
    assert verdicts.find("We met on neutral ground by the well.", *WORDS) == []
    assert verdicts.find("Neutrality was never mentioned; inaccurate maps were.", *WORDS) == []


def test_no_word_file_means_nothing_is_marked(tmp_path):
    assert verdicts.mark(tmp_path, "A neutral newsroom.") == ("A neutral newsroom.", [])
    assert verdicts.mark(tmp_path, None) == (None, [])


def test_a_flag_ends_the_line_that_holds_the_verdict_and_is_never_doubled(repo):
    once, found = verdicts.mark(repo, "A neutral newsroom.")
    assert found == ["neutral"]
    assert once.startswith("A neutral newsroom. " + verdicts.MARK_PREFIX) and "\n" not in once
    assert once.count(verdicts.MARK_PREFIX) == 1
    twice, _ = verdicts.mark(repo, once)
    assert twice == once
    # A corrected text loses the old flag.
    assert verdicts.mark(repo, once.replace("neutral", "small")) == ("A small newsroom.", [])


def test_text_with_no_listed_phrase_is_stored_byte_for_byte(repo):
    text = "  quiet room  \n\n    indented line\n"
    assert verdicts.mark(repo, text) == (text, [])


def test_each_flag_is_on_its_own_verdicts_line(repo):
    text = "A neutral newsroom.\n\n## Details\nHas a lamp.\n\nIts ledger is accurate."
    marked, found = verdicts.mark(repo, text)
    lines = marked.split("\n")
    assert lines[0].startswith("A neutral newsroom. " + verdicts.MARK_PREFIX) and '"neutral"' in lines[0]
    assert lines[2] == "## Details" and lines[3] == "Has a lamp."
    assert lines[5].startswith("Its ledger is accurate. " + verdicts.MARK_PREFIX) and '"accurate"' in lines[5]
    assert found == ["accurate", "neutral"]


def test_a_verdict_in_a_heading_is_flagged_on_the_heading_line(repo):
    marked, found = verdicts.mark(repo, "## A neutral newsroom.\nHas a lamp.")
    assert found == ["neutral"]
    assert marked.split("\n")[0].startswith("## A neutral newsroom. " + verdicts.MARK_PREFIX)
    assert marked.split("\n")[1] == "Has a lamp."
    assert verdicts.mark(repo, "#418 runs a neutral newsroom.")[1] == ["neutral"]


def test_a_dated_list_keeps_each_flag_on_its_own_item(repo):
    text = "- 2026-10-12 A neutral newsroom.\n- 2026-10-13 Has a lamp."
    lines = verdicts.mark(repo, text)[0].split("\n")
    assert verdicts.MARK_PREFIX in lines[0] and verdicts.MARK_PREFIX not in lines[1]


def test_flag_shaped_text_without_the_emoji_is_his_and_stays(repo):
    # No flag without the emoji ever reached his memory; such text is his own.
    plain = "A neutral newsroom.\n*[Flagged by the harness: something.]*\nHas a lamp."
    again, _ = verdicts.mark(repo, plain)
    assert again.split("\n")[1] == "*[Flagged by the harness: something.]*"
    assert again.count(verdicts.MARK_PREFIX) == 1 and again.split("\n")[-1] == "Has a lamp."
    assert verdicts.strip_marks(again) == plain


def test_windows_line_endings_survive(repo):
    once, _ = verdicts.mark(repo, "A neutral newsroom.\r\n\r\nHas a lamp.\r\n")
    assert once.split("\n")[0].endswith("]*\r") and once.split("\n")[2] == "Has a lamp.\r"
    assert verdicts.mark(repo, once)[0] == once                      # byte for byte, endings included


@pytest.mark.parametrize("text", [
    "  A neutral newsroom.\r\n\r\nHas a lamp.\r\n",
    "## A neutral newsroom.\nHas a lamp.\n",
    "\n- an accurate ledger\n- a lamp\n\n",
    "A neutral newsroom.  ",
    "A neutral newsroom.\t\nHas a lamp.",
    "## neutral",
    "## neutral\r\nHas a lamp.",
    "Has a lamp.\n## neutral",
])
def test_flagging_twice_changes_nothing_and_unflagging_restores_the_text(repo, text):
    once = verdicts.mark(repo, text)[0]
    assert verdicts.mark(repo, once)[0] == once
    assert verdicts.strip_marks(once) == text


def test_stripping_leaves_other_annotations_and_quoted_flag_text_alone(repo):
    line = "A small room. *[source:note:1]* *[" + verdicts.FLAG + " Flagged by the harness: something.]*"
    assert verdicts.strip_marks(line) == "A small room. *[source:note:1]*"
    quoted = "He wrote *[Flagged by the harness: x.]* in the middle of a sentence, then went on."
    assert verdicts.strip_marks(quoted) == quoted


def test_quoted_flag_text_never_costs_him_his_own_words(repo):
    text = "He quoted *[Flagged by the harness: x.]* then called it neutral."
    once, found = verdicts.mark(repo, text)
    assert found == ["neutral"] and once.startswith(text + " " + verdicts.MARK_PREFIX)
    assert verdicts.mark(repo, once)[0] == once
    assert verdicts.strip_marks(once) == text
    starts = "*[Flagged by the harness: x.]* was what the neutral note said."
    once, _ = verdicts.mark(repo, starts)
    assert verdicts.strip_marks(once) == starts and verdicts.mark(repo, once)[0] == once


def test_the_known_limit_of_flag_removal_is_pinned(repo):
    # Without the emoji, flag-shaped text at the end of a line is his.
    own = "He quoted *[Flagged by the harness: x.]*"
    assert verdicts.strip_marks(own) == own
    # Only text in a flag's exact form WITH the emoji is taken for one.
    exact = "He quoted *[" + verdicts.FLAG + " Flagged by the harness: x.]*"
    assert verdicts.strip_marks(exact) == "He quoted"
    assert verdicts.strip_marks(exact + " and went on.") == exact + " and went on."


def test_a_phrase_with_brackets_still_makes_a_flag_that_can_be_removed(repo):
    (repo / "config/verdict-words.json").write_text('{"claims": ["verified [ledger]*"]}', encoding="utf-8")
    once, found = verdicts.mark(repo, "It is verified [ledger]* by them.")
    assert found == ["verified [ledger]*"] and once.count(verdicts.MARK_PREFIX) == 1
    assert verdicts.mark(repo, once)[0] == once


def test_a_wrapped_phrase_is_found_and_flagged_once(repo):
    marked, found = verdicts.mark(repo, "It reports only\nfacts, they say.")
    assert found == ["only facts"]
    assert verdicts.MARK_PREFIX in marked.split("\n")[0]             # where the phrase starts
    assert verdicts.mark(repo, marked)[0] == marked


def test_the_mark_does_not_claim_no_basis_was_given(repo):
    marked, _ = verdicts.mark(repo, "The feed is accurate: I checked all 50 entries against the ledger.")
    assert marked.endswith('"accurate". Not checked.]*') and "No outside check" not in marked


@pytest.mark.parametrize("content, still_flags", [
    ("[]", False), ("42", False), ('{"claims": null}', False), ('{"claims": 42}', False),
    ('{"claims": "neutral"}', False), ('{"claims": [1, null, " "]}', False), ("not json", False),
    ('{"claims": ["neutral"], "ignore": null}', True), ('{"claims": ["neutral"], "ignore": 7}', True),
])
def test_a_word_file_of_the_wrong_shape_never_breaks_a_write(repo, content, still_flags):
    (repo / "config/verdict-words.json").write_text(content, encoding="utf-8")
    text, found = verdicts.mark(repo, "A neutral newsroom.")
    assert found == (["neutral"] if still_flags else [])
    assert (verdicts.MARK_PREFIX in text) is still_flags
    assert "updated" in harness.upsert_resident(handle="made-up-wire", impression="A neutral newsroom.")


def test_many_repeats_stay_quick():
    import time
    started = time.monotonic()
    assert verdicts.find("neutral " * 20000, *WORDS) == ["neutral"]
    assert time.monotonic() - started < 2


def test_every_card_that_holds_a_verdict_holds_its_flag(repo):
    harness.upsert_resident(handle="made-up-wire", resident_id=977,
                            impression="A neutral newsroom.\n\n## Details\nIt has a lamp.\n\n"
                                       "## Sourced Observations\n"
                                       "- **2026-10-12** Its ledger is accurate.\n- **2026-10-13** It has a door.")
    newest = {}
    for line in (repo / fixture_name("records/cards/cards.jsonl")).read_text(encoding="utf-8").splitlines():
        if line.strip():
            card = json.loads(line)
            newest[card["id"]] = card
    for words in ("A neutral newsroom.", "Its ledger is accurate."):
        holding = [card for card in newest.values() if words in card.get("text", "")]
        assert holding, f"no card was cut holding {words!r}"
        for card in holding:
            line = next(row for row in card["text"].split("\n") if words in row)
            assert verdicts.MARK_PREFIX in line


def test_an_omitted_impression_keeps_its_flag_and_a_rewrite_drops_it(repo):
    harness.upsert_resident(handle="made-up-wire", impression="A neutral newsroom.")
    harness.upsert_resident(handle="made-up-wire", interests=["lamps"])
    path = repo / fixture_name("records/case-files/made-up-wire.md")
    assert path.read_text(encoding="utf-8").count(verdicts.MARK_PREFIX) == 1
    harness.upsert_resident(handle="made-up-wire", impression="A room of lamps.")
    assert verdicts.MARK_PREFIX not in path.read_text(encoding="utf-8")
    registry = json.loads((repo / fixture_name("records/registry.json")).read_text(encoding="utf-8"))
    assert verdicts.MARK_PREFIX not in registry["residents"]["made-up-wire"]["impression"]


def test_a_draft_reports_the_phrases_and_is_still_staged(repo):
    result = harness.stage_draft(conversation_id="11111111-1111-4111-8111-111111111111", slug="made-up-note", target_type="place", target_place_id=1,
                                 body="Thank you for this accurate map.")
    assert result["status"] == "STAGED"
    assert result["metrics"]["reliability_verdicts"] == ["accurate"]


def test_dossier_stores_the_mark_in_the_file_and_the_registry_and_says_so(repo):
    answer = harness.upsert_resident(handle="made-up-wire", impression="Runs a neutral newsroom.")
    assert verdicts.FLAG + ' Flagged "neutral" (not checked). An owner describing itself is a claim, not a check.' in answer
    text = (repo / fixture_name("records/case-files/made-up-wire.md")).read_text(encoding="utf-8")
    assert "Runs a neutral newsroom." in text and verdicts.MARK_PREFIX in text
    registry = json.loads((repo / fixture_name("records/registry.json")).read_text(encoding="utf-8"))
    assert verdicts.MARK_PREFIX in registry["residents"]["made-up-wire"]["impression"]
    # The write was not refused, and an unmarked impression says nothing extra.
    plain = harness.upsert_resident(handle="made-up-wire", impression="Keeps a small room of lamps.")
    assert "Flagged" not in plain
    assert verdicts.MARK_PREFIX not in (repo / fixture_name("records/case-files/made-up-wire.md")).read_text(encoding="utf-8")


def test_an_attributed_note_is_not_marked(repo):
    answer = harness.upsert_resident(handle="made-up-wire", note="Says its feed is unbiased.", source="note:1")
    assert "Flagged" not in answer


def test_site_inspection_stores_the_mark_and_says_so(repo, monkeypatch):
    monkeypatch.setattr(harness, "update_map", lambda entries: None)
    answer = harness.record_place(place_id=9001, slug="made-up-room", physical_description="One room.",
                                       impression="An accurate and verified feed.")
    assert 'Flagged "accurate", "verified" (not checked).' in answer
    text = next((repo / fixture_name("records/site-inspections/places")).glob("9001-*.md")).read_text(encoding="utf-8")
    assert verdicts.MARK_PREFIX in text and "An accurate and verified feed." in text


def test_the_shipped_list_would_have_caught_the_case():
    claims, ignore = verdicts.load_words(ROOT)
    assert claims, "config/verdict-words.json is missing or empty"
    assert verdicts.find("an unblinking, neutral newsroom reporting outside headlines", claims, ignore) == ["neutral"]
    assert "does not add opinions" in verdicts.find("The owner does not add opinions to the news.", claims, ignore)


def test_the_flag_line_is_worded_as_a_flag(repo):
    marked, _ = verdicts.mark(repo, "A neutral newsroom.")
    assert verdicts.FLAG + " Flagged by the harness:" in marked and "Marked" not in marked


@pytest.mark.parametrize("failure", [OSError, ValueError, RecursionError, MemoryError, RuntimeError])
@pytest.mark.parametrize("operation", ["read", "parse"])
def test_word_loading_fails_open(tmp_path, monkeypatch, failure, operation):
    def fail(*args, **kwargs):
        raise failure("made-up failure")
    if operation == "read":
        monkeypatch.setattr(Path, "read_text", fail)
    else:
        (tmp_path / "config").mkdir()
        (tmp_path / "config/verdict-words.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(verdicts.json, "loads", fail)
    assert verdicts.load_words(tmp_path) == ([], [])


@pytest.mark.parametrize("content", [None, "not json", '{"claims": []}'])
def test_unusable_words_preserve_existing_flags(repo, content):
    original = "## A neutral newsroom.\r\nIts ledger is accurate."
    marked = verdicts.mark(repo, original)[0]
    path = repo / "config/verdict-words.json"
    words = path.read_text(encoding="utf-8")
    if content is None:
        path.unlink()
    else:
        path.write_text(content, encoding="utf-8")
    assert verdicts.mark(repo, marked) == (marked, [])
    path.write_text(words, encoding="utf-8")
    restored, found = verdicts.mark(repo, marked)
    assert restored == marked
    assert found == ["accurate", "neutral"]
    assert restored.count(verdicts.MARK_PREFIX) == 2
    assert verdicts.strip_marks(restored) == original


@pytest.mark.parametrize("failure", [RuntimeError, RecursionError, MemoryError])
def test_airlock_failure_still_saves_case_file_and_site_inspection(repo, monkeypatch, failure):
    def fail(*args, **kwargs):
        raise failure("made-up failure")
    monkeypatch.setattr(verdicts, "mark", fail)
    monkeypatch.setattr(harness, "update_map", lambda entries: None)
    text = "A neutral newsroom."
    answer = harness.upsert_resident(handle="made-up-wire", impression=text)
    assert answer.endswith(harness.RELIABILITY_CHECK_FAILED)
    dossier = (repo / fixture_name("records/case-files/made-up-wire.md")).read_text(encoding="utf-8")
    assert text in dossier and verdicts.MARK_PREFIX not in dossier
    registry = json.loads((repo / fixture_name("records/registry.json")).read_text(encoding="utf-8"))
    assert registry["residents"]["made-up-wire"]["impression"] == text
    answer = harness.record_place(place_id=9001, slug="made-up-room",
                                       physical_description="One room.", impression=text)
    assert answer.endswith(harness.RELIABILITY_CHECK_FAILED)
    flight = next((repo / fixture_name("records/site-inspections/places")).glob("9001-*.md")).read_text(encoding="utf-8")
    assert text in flight and verdicts.MARK_PREFIX not in flight


@pytest.mark.parametrize("failure", [RuntimeError, RecursionError, MemoryError])
def test_airlock_failure_still_saves_draft(repo, monkeypatch, failure):
    def fail(*args, **kwargs):
        raise failure("made-up failure")
    monkeypatch.setattr(verdicts, "find", fail)
    body = "Thank you for this accurate map."
    result = harness.stage_draft(conversation_id="11111111-1111-4111-8111-111111111111",
                                 slug="made-up-note", target_type="place", target_place_id=1, body=body)
    assert result["status"] == "STAGED"
    assert result["reliability_check"] == "did not run"
    assert result["metrics"]["reliability_verdicts"] == []
    saved = (repo / result["file"]).read_text(encoding="utf-8")
    assert "reliability_verdicts: []\n" in saved
    assert saved.split("# Draft Content\n\n", 1)[1] == body + "\n"
    assert verdicts.MARK_PREFIX not in saved


@pytest.mark.parametrize("body, phrases", [("An accurate map.", ["accurate"]), ("A small map.", [])])
def test_draft_header_has_advisory_phrases(repo, body, phrases):
    result = harness.stage_draft(conversation_id="11111111-1111-4111-8111-111111111111",
                                 slug="made-up-note", target_type="place", target_place_id=1, body=body)
    saved = (repo / result["file"]).read_text(encoding="utf-8")
    assert "reliability_verdicts: " + json.dumps(phrases) + "\n" in saved
    assert result["metrics"]["reliability_verdicts"] == phrases
    assert "reliability_check" not in result


@pytest.mark.parametrize("text", ["> only\n> facts", "   > only\n   > facts", "> > only\r\n> > facts", "Before.\n  > only\n  > facts"])
def test_wrapped_block_quote_is_flagged_on_its_start_line(repo, text):
    marked, found = verdicts.mark(repo, text)
    assert found == ["only facts"]
    line = next(line for line in marked.splitlines() if "only" in line)
    assert verdicts.MARK_PREFIX in line
    assert marked.count(verdicts.MARK_PREFIX) == 1
    assert verdicts.strip_marks(marked) == text
    assert verdicts.mark(repo, marked)[0] == marked


@pytest.mark.parametrize("indent", ["\t", "    "])
def test_code_indented_quote_markers_do_not_join_a_phrase(repo, indent):
    text = f"{indent}> only\n{indent}> facts"
    assert verdicts.find_by_line(text, *WORDS) == {}
    assert verdicts.mark(repo, text) == (text, [])


@pytest.mark.parametrize("tool", ["upsert_resident", "record_place"])
def test_notice_failure_is_not_swallowed(repo, monkeypatch, tool):
    def fail(*args, **kwargs):
        raise RuntimeError("made-up notice failure")
    monkeypatch.setattr(harness, "_verdict_notice", fail)
    monkeypatch.setattr(harness, "update_map", lambda entries: None)
    with pytest.raises(RuntimeError, match="made-up notice failure"):
        if tool == "upsert_resident":
            harness.upsert_resident(handle="made-up-wire", impression="A neutral newsroom.")
        else:
            harness.record_place(place_id=9001, slug="made-up-room",
                                      physical_description="One room.", impression="A neutral newsroom.")


def test_draft_json_failure_is_not_swallowed(repo, monkeypatch):
    dumps = json.dumps

    def fail_verdicts(value, *args, **kwargs):
        if value == ["accurate"]:
            raise RuntimeError("made-up JSON failure")
        return dumps(value, *args, **kwargs)

    monkeypatch.setattr(harness.json, "dumps", fail_verdicts)
    with pytest.raises(RuntimeError, match="made-up JSON failure"):
        harness.stage_draft(conversation_id="11111111-1111-4111-8111-111111111111",
                            slug="made-up-note", target_type="place", target_place_id=1,
                            body="An accurate map.")


def test_draft_phrase_cannot_break_the_header(repo):
    phrases = ["verified---ledger"]
    (repo / "config/verdict-words.json").write_text(
        json.dumps({"claims": phrases}), encoding="utf-8")
    body = "Thank you for the verified---ledger.\nIt has a map."
    conversation_id = "11111111-1111-4111-8111-111111111111"
    result = harness.stage_draft(conversation_id=conversation_id, slug="made-up-note",
                                 target_type="place", target_place_id=1, body=body)
    assert result["status"] == "STAGED"
    assert result["metrics"]["reliability_verdicts"] == phrases
    path = repo / result["file"]
    saved = path.read_text(encoding="utf-8")
    header_line = next(line for line in saved.splitlines() if line.startswith("reliability_verdicts: "))
    assert "---" not in header_line
    assert json.loads(header_line.split(": ", 1)[1]) == phrases
    assert harness.read_drafts(slug="made-up-note", body_only=True,
                               conversation_id=conversation_id) == body
    answer = harness.archive_draft(slug="made-up-note", note_id=42,
                                   conversation_id=conversation_id)
    assert fixture_name("moved to pending-approval/archive/") in answer
    assert not path.exists()
    archived = next((repo / fixture_name("pending-approval/archive")).glob("*-made-up-note.md")).read_text(encoding="utf-8")
    assert archived.splitlines().count("---") == 2
    opening, header, rest = archived.split("---", 2)
    assert opening == ""
    assert 'archive_status: "published"\n' in header
    assert "note_id: 42\n" in header
    header_line = next(line for line in header.splitlines() if line.startswith("reliability_verdicts: "))
    assert json.loads(header_line.split(": ", 1)[1]) == phrases
    assert rest == "\n\n# Draft Content\n\n" + body + "\n"


def test_flagged_impression_adds_no_shift_report_footer_or_flag_files(repo, monkeypatch):
    from tools import driver_record
    monkeypatch.setattr(driver_record, "footer", lambda root: "")
    harness.upsert_resident(handle="made-up-wire", impression="A neutral newsroom.")
    dossier = (repo / fixture_name("records/case-files/made-up-wire.md")).read_text(encoding="utf-8")
    assert verdicts.MARK_PREFIX in dossier
    content = "Visited a made-up room and recorded an impression.\n"
    answer = harness.write_journal(content)
    path = repo / fixture_name("records/shift-reports") / answer.splitlines()[0]
    assert path.read_text(encoding="utf-8") == content
    assert not list((repo / fixture_name("records/private")).rglob("flags-*.json*"))


def _live_cards(repo):
    newest = {}
    for line in (repo / fixture_name("records/cards/cards.jsonl")).read_text(encoding="utf-8").splitlines():
        if line.strip():
            card = json.loads(line)
            newest[card["id"]] = card
    return [card for card in newest.values() if card.get("status") in ("live", "recheck")]


def test_a_heading_verdict_is_flagged_in_title_only_recall_with_the_real_cutter(repo):
    from tools import cards
    harness.upsert_resident(handle="made-up-wire", resident_id=977, impression="## A neutral newsroom.\nHas a lamp.")
    live = _live_cards(repo)
    holding = [card for card in live if "A neutral newsroom." in card.get("title", "") + card.get("text", "")]
    assert holding
    for card in holding:
        assert verdicts.FLAG in card.get("text", "")
        reference = cards._recall_reference(card)
        if "A neutral newsroom." in reference.get("title", ""):
            assert verdicts.FLAG in reference["title"]
    # Rewritten without the verdict: no live card keeps the old flagged heading.
    harness.upsert_resident(handle="made-up-wire", resident_id=977, impression="## A small newsroom.\nHas a lamp.")
    live = _live_cards(repo)
    assert not [card for card in live if "A neutral newsroom." in card.get("title", "") + card.get("text", "")]
    assert [card for card in live if "A small newsroom." in card.get("title", "") + card.get("text", "")]
