"""File structure is not an entry: headings, intros and placeholders.

Made-up text only; no agent memory is used.
"""
import json
from pathlib import Path

from tools.cards import _card_spec, _pieces_for

ROOT = Path(__file__).resolve().parents[1]
SECTIONS = json.loads(
    (ROOT / "config/tag-patterns.city.json").read_text(encoding="utf-8")
)["section_status"]

RULEBOOK = """# Rules

How this made-up rulebook works:

- **Proposed** means not yet trusted.
- **Confirmed** means trusted.

## Confirmed

- **Stand in the room before writing.**

## Proposed (unverified)

- (none yet)
- **Measure before naming.**

## Retracted

- **A made-up false belief.**
"""

LESSONS = """# Lessons

## 2026-09-01-0900

## 2026-09-01-0900

- Check the room after changing it.

## 2026-09-02-1015
"""


def statuses(logical, text):
    path = Path("made-up.md")
    result = {}
    for position, segment in _pieces_for(logical, text, path):
        supplied, _, _ = _card_spec(logical, path, text, position, segment, SECTIONS)
        result[segment.strip().splitlines()[0] + f" @{position}"] = supplied.get("status", "live")
    return result


def test_rulebook_structure_is_not_a_rule():
    found = statuses("city_rules", RULEBOOK)
    by_first_line = {key.rsplit(" @", 1)[0]: value for key, value in found.items()}
    # the first piece runs from the top of the file through its first bullet
    assert by_first_line["# Rules"] == "structure"
    assert by_first_line["- **Confirmed** means trusted."] == "structure"
    assert by_first_line["## Confirmed"] == "structure"
    assert by_first_line["## Proposed (unverified)"] == "structure"
    assert by_first_line["## Retracted"] == "structure"
    assert by_first_line["- (none yet)"] == "structure"


def test_rulebook_entries_keep_their_section_status():
    found = statuses("city_rules", RULEBOOK)
    by_first_line = {key.rsplit(" @", 1)[0]: value for key, value in found.items()}
    assert by_first_line["- **Stand in the room before writing.**"] == "live"
    assert by_first_line["- **Measure before naming.**"] == "recheck"
    assert by_first_line["- **A made-up false belief.**"] == "done"


def test_empty_lesson_heading_is_structure():
    found = list(statuses("lessons", LESSONS).values())
    # first heading (carries the file title above it) is empty, the second
    # holds the lesson, the third is an empty heading at the end of the file
    assert found == ["structure", "recheck", "structure"]


def test_pieces_still_rebuild_the_file():
    for logical, text in (("city_rules", RULEBOOK), ("lessons", LESSONS)):
        pieces = _pieces_for(logical, text, Path("made-up.md"))
        assert "".join(segment for _, segment in pieces) == text


def test_bullets_under_an_explaining_section_are_structure():
    text = (
        "# Rules\n\n## How a rule earns its place\n\n- **Proposed** means not yet trusted.\n\n"
        "## Confirmed\n\n- **Stand in the room before writing.**\n"
    )
    found = statuses("city_rules", text)
    by_first_line = {key.rsplit(" @", 1)[0]: value for key, value in found.items()}
    assert by_first_line["- **Proposed** means not yet trusted."] == "structure"
    assert by_first_line["- **Stand in the room before writing.**"] == "live"


def test_rulebook_without_sections_keeps_its_rules():
    text = "# Rules\n\n- **Stand in the room before writing.**\n- **One edge per move.**\n"
    assert list(statuses("city_rules", text).values()) == ["live", "live"]


def test_a_removed_entry_is_removed_once(tmp_path):
    import shutil
    from tools.cards import migrate, recut_file

    source = tmp_path / "source"
    shutil.copytree(ROOT / "tests" / "fixtures" / "cards", source)
    naming = source / "naming.json"
    patterns = ROOT / "config" / "tag-patterns.city.json"
    migrate(source, naming, patterns, output_root=source / "memory")
    lessons = source / "memory" / "lessons.md"
    text = lessons.read_text(encoding="utf-8")
    cut = text.index("## 2026-09-02-101500")
    lessons.write_text(text[:cut], encoding="utf-8")
    first = recut_file(source, naming, patterns, "lessons", lessons)
    second = recut_file(source, naming, patterns, "lessons", lessons)
    assert len(first["removed"]) >= 1
    assert second["removed"] == []
