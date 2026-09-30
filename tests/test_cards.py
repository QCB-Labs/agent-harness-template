from __future__ import annotations

import json
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
    round_trip_differences,
)

ROOT = Path(__file__).resolve().parent.parent
GENERIC = ROOT / "config" / "tag-patterns.generic.json"
CITY = ROOT / "config" / "tag-patterns.city.json"
FIXTURE = ROOT / "tests" / "fixtures" / "cards"
PRIVATE = ROOT / "fixtures" / "private"
BROKEN_INPUT = False


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
