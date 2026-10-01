from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from tools.cards import (
    CardError,
    CardStore,
    extract_tags,
    generate_views,
    load_patterns,
    migrate,
    read_card,
    read_wake_card,
    recall,
    recut_file,
    round_trip_differences,
)

ROOT = Path(__file__).resolve().parent.parent
GENERIC = ROOT / "config" / "tag-patterns.generic.json"
CITY = ROOT / "config" / "tag-patterns.city.json"
FIXTURE = ROOT / "tests" / "fixtures" / "cards"
PRIVATE = ROOT / "fixtures" / "private"
BROKEN_INPUT = False
FAIL_ONCE = os.environ.get("BRIEF02_FAIL_ONCE", "")


def _store(tmp_path: Path) -> CardStore:
    return CardStore(tmp_path, GENERIC, clock=lambda: "2026-09-30T12:00:00Z")


def _write(store: CardStore, text: str = "Keep the raw line.") -> dict:
    return store.write(
        {"kind": "rule", "text": text},
        wake="w123",
        source="self",
        channel="session",
    )


def test_system_fields_are_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    payload = {"kind": "rule", "text": "Keep the raw line."}
    if BROKEN_INPUT:
        forbidden = "not_a_system_field"
    else:
        forbidden = "source"
    payload[forbidden] = "caller value"
    with pytest.raises(CardError, match="system field"):
        store.write(payload, wake="w123", source="self", channel="session")


def test_deleted_index_rebuilds_identically(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write(store, "W123 checked on 2026-09-30.")
    expected = json.loads(store.index_path.read_text(encoding="utf-8"))
    store.index_path.unlink()
    rebuilt = store.rebuild_index()
    if BROKEN_INPUT:
        expected = {"broken": True}
    assert rebuilt == expected
    assert json.loads(store.index_path.read_text(encoding="utf-8")) == expected


def test_earlier_jsonl_bytes_never_change(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for number in range(1, 7):
        before = store.cards_path.read_bytes() if store.cards_path.exists() else b""
        _write(store, f"Rule {number}")
        if BROKEN_INPUT:
            before += b"broken"
        assert store.cards_path.read_bytes().startswith(before)


def test_made_up_city_tags() -> None:
    patterns = load_patterns(GENERIC if BROKEN_INPUT else CITY)
    tags = extract_tags(
        kind="thread",
        title="Mira (#12)",
        text=(
            "On W123, resident #12 crossed Places 44 & 45 on 2026-09-30; "
            "Note #88 cited Thing #91, Post #7, Kind #39, and stray #999."
        ),
        patterns=patterns,
    )
    assert tags == [
        "date:2026-09-30",
        "kind-id:39",
        "kind:thread",
        "note:88",
        "place:44",
        "place:45",
        "post:7",
        "ref:999",
        "resident:12",
        "thing:91",
        "wake:w123",
    ]


def test_tag_extraction_is_deterministic() -> None:
    patterns = load_patterns(CITY)
    kwargs = {"kind": "decision", "text": "Place 44 at W123", "patterns": patterns}
    first = extract_tags(**kwargs)
    if BROKEN_INPUT:
        kwargs["text"] += " and W999"
    assert extract_tags(**kwargs) == first


def test_migration_cuts_all_logical_files_and_reviews_lessons(tmp_path: Path) -> None:
    source = tmp_path / "source"
    shutil.copytree(FIXTURE, source)
    if BROKEN_INPUT:
        lessons = source / "memory" / "lessons.md"
        lessons.write_text(
            lessons.read_text(encoding="utf-8").replace("## 2026-09-01-0900", "# 2026-09-01-0900"),
            encoding="utf-8",
        )
    output = tmp_path / "output"
    cards = migrate(source, source / "naming.json", CITY, output_root=output)
    lesson_cards = [card for card in cards if card["origin"]["logical"] == "lessons"]
    assert len(lesson_cards) == 2
    assert {card["kind"] for card in lesson_cards} == {"rule", "reflection"}
    assert all(card["status"] == "recheck" for card in lesson_cards)
    assert (output / "cards" / "kind-review.md").is_file()
    assert {card["origin"]["logical"] for card in cards} == {
        "lessons", "city_rules", "open_threads", "goals", "continuity", "journal", "residents"
    }


def test_made_up_fixture_round_trip(tmp_path: Path) -> None:
    source = tmp_path / "source"
    shutil.copytree(FIXTURE, source)
    output = tmp_path / "output"
    cards = migrate(source, source / "naming.json", CITY, output_root=output)
    generated = tmp_path / "generated"
    generate_views(output / "cards" / "cards.jsonl", generated)
    files = sorted({card["origin"]["file"] for card in cards})
    if BROKEN_INPUT:
        target = source / files[0]
        target.write_text(target.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
    assert round_trip_differences(source, generated, files) == []


def test_private_real_line_tag_set() -> None:
    path = PRIVATE / "tag-test-set.jsonl"
    if not path.is_file():
        pytest.skip("private fixture is not installed")
    patterns = load_patterns(CITY)
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    assert len(cases) >= 30
    if BROKEN_INPUT:
        cases[0]["tags"] = ["broken:input"]
    for case in cases:
        assert extract_tags(kind=case["kind"], text=case["text"], patterns=patterns) == case["tags"]


def test_goals_at_fixture_root_become_goal_cards(tmp_path: Path) -> None:
    source = tmp_path / "source"
    shutil.copytree(FIXTURE, source)
    if FAIL_ONCE == "goals":
        (source / "goals.md").unlink()
    cards = migrate(source, source / "naming.json", CITY, output_root=tmp_path / "output")
    goals = [card for card in cards if card["origin"]["logical"] == "goals"]
    assert goals and all(card["kind"] == "goal" for card in goals)
    assert all(card["origin"]["file"] == "goals.md" for card in goals)


def test_continuity_cuts_five_top_level_bullets_and_round_trips(tmp_path: Path) -> None:
    source = tmp_path / "source"
    shutil.copytree(FIXTURE, source)
    continuity = source / "memory" / "continuity.md"
    continuity.write_bytes(
        b"# Continuity\n\nPreface.\n\n## First\nBefore.\n- one\n- two\n\n"
        b"## Second\nIntro.\n- three\n- four\n- five"
    )
    cards = migrate(source, source / "naming.json", CITY, output_root=tmp_path / "output")
    decisions = [card for card in cards if card["origin"]["logical"] == "continuity"]
    expected = 4 if FAIL_ONCE == "continuity" else 5
    assert len(decisions) == expected
    generated = tmp_path / "generated"
    generate_views(tmp_path / "output" / "cards" / "cards.jsonl", generated)
    assert (generated / "memory" / "continuity.md").read_bytes() == continuity.read_bytes()


def test_round_trip_preserves_crlf_and_missing_final_newline(tmp_path: Path) -> None:
    source = tmp_path / "source"
    shutil.copytree(FIXTURE, source)
    lessons = source / "memory" / "lessons.md"
    continuity = source / "memory" / "continuity.md"
    lessons.write_bytes(b"# Lessons\r\n\r\n## 2026-09-01-0900\r\n\r\n- Keep CRLF.\r\n")
    continuity.write_bytes(b"# Continuity\n\n## Choice\n- Keep LF without a final newline")
    migrate(source, source / "naming.json", CITY, output_root=tmp_path / "output")
    generated = tmp_path / "generated"
    generate_views(tmp_path / "output" / "cards" / "cards.jsonl", generated)
    expected = lessons.read_bytes()
    if FAIL_ONCE == "bytes":
        expected = expected.replace(b"\r\n", b"\n")
    assert (generated / "memory" / "lessons.md").read_bytes() == expected
    assert (generated / "memory" / "continuity.md").read_bytes() == continuity.read_bytes()


def test_no_id_dossier_is_recheck_and_reported(tmp_path: Path) -> None:
    source = tmp_path / "source"
    shutil.copytree(FIXTURE, source)
    dossier = source / "memory" / "residents" / "mira.md"
    dossier.write_text("# Resident: mira (#—)\n\n- **Resident ID:** —\n", encoding="utf-8")
    cards = migrate(source, source / "naming.json", CITY, output_root=tmp_path / "output")
    card = next(card for card in cards if card["origin"]["file"].endswith("mira.md"))
    expected = "resident:999" if FAIL_ONCE == "no_id" else "name:mira"
    assert expected in card["tags"]
    assert card["status"] == "recheck"
    assert "mira.md" in (tmp_path / "output" / "cards" / "migration-report.md").read_text(encoding="utf-8")


def test_square_and_market_ids_never_become_resident_tags() -> None:
    patterns = load_patterns(CITY)
    tags = extract_tags(
        kind="relationship",
        text="Rin is Square citizen #688 and Market merchant #42.",
        patterns=patterns,
    )
    if FAIL_ONCE == "service_ids":
        tags.append("resident:688")
    assert "square:688" in tags
    assert "market:42" in tags
    assert not any(tag.startswith("resident:") for tag in tags)


def test_recut_changed_entry_keeps_ids_and_versions_only_one(tmp_path: Path) -> None:
    source = tmp_path / "source"
    shutil.copytree(FIXTURE, source)
    migrate(source, source / "naming.json", CITY, output_root=source / "memory")
    path = source / "memory" / "open-threads.md"
    before = CardStore(source / "memory", CITY)._current_state()
    path.write_bytes(path.read_bytes().replace(b"Bring the brass compass", b"Bring both brass compasses"))
    result = recut_file(source, source / "naming.json", CITY, "open_threads", path)
    after = CardStore(source / "memory", CITY)._current_state()
    changed = [card_id for card_id in before if before[card_id]["text"] != after[card_id]["text"]]
    expected = [] if FAIL_ONCE == "recut_edit" else [result["changed"][0]]
    assert changed == expected
    assert set(before) == set(after)


def test_recut_removed_entry_marks_card_done_and_readable(tmp_path: Path) -> None:
    source = tmp_path / "source"
    shutil.copytree(FIXTURE, source)
    migrate(source, source / "naming.json", CITY, output_root=source / "memory")
    path = source / "memory" / "open-threads.md"
    state = CardStore(source / "memory", CITY)._current_state()
    thread_ids = [card["id"] for card in state.values() if card["origin"]["logical"] == "open_threads"]
    text = path.read_text(encoding="utf-8")
    path.write_text(text[: text.rfind("- **")], encoding="utf-8")
    result = recut_file(source, source / "naming.json", CITY, "open_threads", path)
    removed_id = result["removed"][0]
    card = read_card(source / "memory", removed_id)
    expected = "live" if FAIL_ONCE == "recut_remove" else "done"
    assert card["card"]["status"] == expected
    assert removed_id in thread_ids
    assert card["card"]["status_reason"] == "removed from source file"


def test_recall_refuses_over_budget_and_logs_read(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _write(store, "x" * 80)
    config = tmp_path / "budget.json"
    config.write_text(json.dumps({"recall_tokens": 1, "wake_sections": {}}), encoding="utf-8")
    result = recall(tmp_path, tags=["kind:rule"], config_path=config, wake="w123")
    if FAIL_ONCE == "recall_budget":
        result["refused"] = False
    assert result["refused"] is True
    assert result["matched"] == 1
    assert (tmp_path / "cards" / "reads.jsonl").is_file()


def test_recall_over_budget_returns_what_fits_and_names_the_rest(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _write(store, "a" * 80)
    second = _write(store, "b" * 80)
    config = tmp_path / "budget.json"
    config.write_text(json.dumps({"recall_tokens": 25, "wake_sections": {}}), encoding="utf-8")
    result = recall(tmp_path, tags=["kind:rule"], config_path=config, wake="w123")
    assert result["refused"] is False
    assert len(result["cards"]) == 1
    assert result["cards"][0]["text"] in {"a" * 80, "b" * 80}
    assert result["left_out"] == 1
    left = result["left_out_cards"][0]["id"]
    assert {left, result["cards"][0]["id"]} == {first["id"], second["id"]}
    assert "read_card" in result["message"]
    logged = json.loads((tmp_path / "cards" / "reads.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert logged["ids"] == [result["cards"][0]["id"]]


def test_recall_does_not_change_cards_and_ranks_tags_weight_newest(tmp_path: Path) -> None:
    times = iter(("2026-09-30T10:00:00Z", "2026-09-30T11:00:00Z", "2026-09-30T12:00:00Z"))
    store = CardStore(tmp_path, GENERIC, clock=lambda: next(times))
    a = store.write({"kind": "rule", "text": "W123 alpha"}, wake="w1", source="self", channel="session")
    b = store.write({"kind": "rule", "text": "W123 W124 beta"}, wake="w1", source="self", channel="session")
    c = store.write({"kind": "rule", "text": "W123 gamma"}, wake="w1", source="self", channel="session")
    raw = store.cards_path.read_bytes()
    result = recall(tmp_path, tags=["wake:w123", "wake:w124"], config_path=ROOT / "config" / "cards.json")
    ids = [card["id"] for card in result["cards"]]
    if FAIL_ONCE == "recall_rank":
        ids.reverse()
    assert ids == [b["id"], c["id"], a["id"]]
    assert store.cards_path.read_bytes() == raw


def test_read_wake_card_has_eight_ordered_sections(tmp_path: Path) -> None:
    source = tmp_path / "source"
    shutil.copytree(FIXTURE, source)
    (source / "SOUL.md").write_text("Identity whole.", encoding="utf-8")
    (source / "GOVERNANCE.md").write_text("Governance whole.", encoding="utf-8")
    naming = json.loads((source / "naming.json").read_text(encoding="utf-8"))
    naming["paths"]["identity"] = "SOUL.md"
    naming["paths"]["governance"] = "GOVERNANCE.md"
    (source / "naming.json").write_text(json.dumps(naming), encoding="utf-8")
    migrate(source, source / "naming.json", CITY, output_root=source / "memory")
    result = read_wake_card(source, source / "naming.json", config_path=ROOT / "config" / "cards.json", here_tags=["name:mira"])
    section_names = ["## Who I am", "## What I was doing", "## What I owe", "## Open threads (index)", "## Decisions", "## Rules", "## What needs checking", "## What's here"]
    headings = sorted(section_names, key=result["text"].index)
    expected = headings[:-1] if FAIL_ONCE == "wake" else headings
    assert expected == section_names
    assert "Identity whole." in result["text"] and "Governance whole." in result["text"]


def test_read_wake_card_sends_its_text_once(tmp_path: Path) -> None:
    source = tmp_path / "source"
    shutil.copytree(FIXTURE, source)
    (source / "SOUL.md").write_text("Identity whole.", encoding="utf-8")
    (source / "GOVERNANCE.md").write_text("Governance whole.", encoding="utf-8")
    naming = json.loads((source / "naming.json").read_text(encoding="utf-8"))
    naming["paths"]["identity"] = "SOUL.md"
    naming["paths"]["governance"] = "GOVERNANCE.md"
    (source / "naming.json").write_text(json.dumps(naming), encoding="utf-8")
    migrate(source, source / "naming.json", CITY, output_root=source / "memory")
    result = read_wake_card(source, source / "naming.json", config_path=ROOT / "config" / "cards.json", here_tags=["name:mira"])
    whole = json.dumps(result, ensure_ascii=False)
    assert whole.count("Identity whole.") == 1
    assert all("text" not in section for section in result["sections"])
    assert result["sections"][0]["name"] == "Who I am"
