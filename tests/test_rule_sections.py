"""Made-up rulebook; no agent memory is used."""
import json
import shutil
from pathlib import Path

from tools.cards import migrate, read_wake_card

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "cards"
CITY = ROOT / "config" / "tag-patterns.city.json"

RULEBOOK = (
    "# Rules\n\nAn introductory note.\n\n"
    "## Confirmed\n\n"
    "- **Stand in the room before writing.** Last confirmed 2026-01-05.\n\n"
    "## Proposed (unverified)\n\n"
    "- **[proposed 2026-02-03-0910]** Measure before naming.\n"
    "- **[proposed 2026-03-04]** Count twice.\n"
    "- (none yet — append new observations here)\n\n"
    "## Retracted (proven false)\n\n"
    "- **[RETRACTED 2026-01-09]** Doors are always open.\n"
)


def build(tmp_path, patterns=CITY):
    source = tmp_path / "source"
    shutil.copytree(FIXTURE, source)
    (source / "memory" / "city-rules.md").write_text(RULEBOOK, encoding="utf-8")
    (source / "identity.md").write_text("Made-up identity.", encoding="utf-8")
    (source / "governance.md").write_text("Made-up governance.", encoding="utf-8")
    naming = json.loads((source / "naming.json").read_text(encoding="utf-8"))
    naming["paths"].update(identity="identity.md", governance="governance.md")
    (source / "naming.json").write_text(json.dumps(naming), encoding="utf-8")
    cards = migrate(source, source / "naming.json", patterns, output_root=source / "memory")
    rules = [c for c in cards if c["origin"]["logical"] == "city_rules"]
    return source, rules


def by_text(rules, fragment):
    return next(c for c in rules if fragment in c["text"])


def test_rule_status_follows_its_section(tmp_path):
    _, rules = build(tmp_path)
    assert by_text(rules, "Stand in the room")["status"] == "live"
    assert by_text(rules, "Measure before naming")["status"] == "recheck"
    assert by_text(rules, "Count twice")["status"] == "recheck"
    assert by_text(rules, "Doors are always open")["status"] == "done"


def test_headings_keep_their_place(tmp_path):
    _, rules = build(tmp_path)
    for heading in ("## Confirmed", "## Proposed (unverified)", "## Retracted (proven false)"):
        card = next(c for c in rules if c["text"].rstrip().endswith(heading))
        assert card["status"] == "live"


def test_placeholder_line_is_not_something_to_check(tmp_path):
    _, rules = build(tmp_path)
    assert by_text(rules, "none yet")["status"] == "live"


def test_rule_cards_carry_their_own_date(tmp_path):
    _, rules = build(tmp_path)
    assert by_text(rules, "Measure before naming")["created_at"] == "2026-02-03T09:10:00Z"
    assert by_text(rules, "Count twice")["created_at"] == "2026-03-04T00:00:00Z"


def test_without_section_rules_every_rule_is_live(tmp_path):
    pack = json.loads(CITY.read_text(encoding="utf-8"))
    del pack["section_status"]
    plain = tmp_path / "pack.json"
    plain.write_text(json.dumps(pack), encoding="utf-8")
    _, rules = build(tmp_path, patterns=plain)
    assert {c["status"] for c in rules} == {"live"}


def test_wake_card_shows_confirmed_rules_and_says_what_is_withheld(tmp_path):
    source, _ = build(tmp_path)
    result = read_wake_card(source, source / "naming.json", config_path=ROOT / "config" / "cards.json")
    text = result["text"]
    body = text[text.index("## Rules\n\n"):text.index("\n\n## What needs checking")]
    assert "Stand in the room" in body
    assert "## Proposed (unverified)" in body
    assert "Measure before naming" not in body and "Doors are always open" not in body
    assert "waiting to be checked" in body and "1 closed" in body
    checking = text[text.index("## What needs checking"):text.index("\n\n## What's here")]
    assert "Count twice" in checking and "Doors are always open" not in checking
    # Newest memory first: the March proposal comes before the February one.
    assert checking.index("Count twice") < checking.index("Measure before naming")
