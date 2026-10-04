"""Made-up wake-card regression fixtures; no agent memory is used."""
import json
from pathlib import Path

import pytest

from tools.cards import CardStore, read_wake_card

ROOT = Path(__file__).resolve().parents[1]
NAMES = ["Who I am", "What I was doing", "What I owe", "Open threads (index)",
         "Rules", "What needs checking", "What's here"]


def fixture(tmp_path):
    (tmp_path / "identity.md").write_text("Made-up identity.", encoding="utf-8")
    (tmp_path / "governance.md").write_text("Made-up governance.", encoding="utf-8")
    identity = "identity.md"
    naming = tmp_path / "naming.json"
    naming.write_text(json.dumps({"dirs": {"memory": "memory"}, "paths": {
        "identity": identity, "governance": "governance.md"}}), encoding="utf-8")
    config = json.loads((ROOT / "config/cards.json").read_text(encoding="utf-8"))
    config["wake_sections"].update(thread_index=2000, rules=3000)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    store = CardStore(tmp_path / "memory", ROOT / "config/tag-patterns.city.json")

    def add(kind, text, **extra):
        return store.write({"kind": kind, "text": text, **extra},
                           wake="test", source="self", channel="session")

    def read():
        return read_wake_card(tmp_path, naming, config_path=config_path)

    return store, add, read, config, config_path


def section(result, name):
    start = result["text"].index("## " + name + "\n\n") + len("## " + name + "\n\n")
    end = result["text"].find("\n\n## ", start)
    return result["text"][start:] if end < 0 else result["text"][start:end]


def test_seven_sections_in_order_and_text_once(tmp_path):
    store, add, read, _, _ = fixture(tmp_path)
    add("rule", "Made-up rule.")
    before = store.cards_path.read_bytes()
    result = read()
    assert [s["name"] for s in result["sections"]] == NAMES
    assert result["text"].count("Made-up identity.") == 1
    assert all("text" not in s for s in result["sections"])
    assert store.cards_path.read_bytes() == before


def test_thread_index_titles_tags_newest_and_no_bodies(tmp_path):
    store, add, read, _, _ = fixture(tmp_path)
    old = add("thread", "SECRET OLD BODY resident:12 Place 34", title="Older question")
    new = add("thread", "SECRET NEW BODY resident:56 Place 78", title="Remote person's question")
    store.clock = lambda: "2026-01-01"
    store.append_version(old)
    store.clock = lambda: "2026-02-01"
    store.append_version(new)
    add("thread", "DONE BODY", title="Closed", status="done")
    body = section(read(), "Open threads (index)")
    assert body.index(new["id"]) < body.index(old["id"])
    assert f"- {new['id']}: Remote person's question [place:78, resident:56]" in body
    assert "SECRET" not in body and "Closed" not in body and "name:" not in body
    assert "Recall a thread with read_card(card_id=\"<id>\") or recall(tags=[...])." in body


def test_rules_live_newest_and_recheck_only_in_checking(tmp_path):
    store, add, read, _, _ = fixture(tmp_path)
    old = add("rule", "Older full rule.")
    new = add("rule", "Newer full rule.")
    store.clock = lambda: "2026-01-01"
    store.append_version(old)
    store.clock = lambda: "2026-02-01"
    store.append_version(new)
    add("rule", "Unconfirmed rule.", status="recheck")
    add("rule", "Retired rule.", status="done")
    result = read()
    body = section(result, "Rules")
    assert body.index(new["text"]) < body.index(old["text"])
    assert "Unconfirmed" not in body and "Retired" not in body
    assert "Unconfirmed rule." in section(result, "What needs checking")


@pytest.mark.parametrize("kind,name,key", [
    ("thread", "Open threads (index)", "thread_index"),
    ("rule", "Rules", "rules"),
])
def test_new_sections_respect_budget_without_partial_cards(tmp_path, kind, name, key):
    _, add, read, config, config_path = fixture(tmp_path)
    add(kind, "FULL SMALL ENTRY", title="Small title")
    add(kind, "HUGE BODY " * 1000, title="HUGE TITLE " * 1000)
    config["wake_sections"][key] = 80
    config_path.write_text(json.dumps(config), encoding="utf-8")
    result = read()
    info = next(s for s in result["sections"] if s["name"] == name)
    body = section(result, name)
    assert info["tokens"] <= 80
    assert info["shown"] == 1 and info["left_out"] == 1
    assert ("Small title" if kind == "thread" else "FULL SMALL ENTRY") in body
    assert "HUGE" not in body
    assert "1 more" in body and ("recall" in body or "read_card" in body)


def filed(store, card, order, when):
    store.clock = lambda: when
    return store.append_version(card, origin={"file": "source.md", "order": order})


@pytest.mark.parametrize("kind,name", [("thread", "Open threads (index)"), ("rule", "Rules")])
def test_file_order_wins_over_updated_at(tmp_path, kind, name):
    store, add, read, _, _ = fixture(tmp_path)
    first = add(kind, "FIRST IN FILE", title="First in file")
    second = add(kind, "SECOND IN FILE", title="Second in file")
    # A migration stamps cards as it goes, so the last in the file looks newest.
    filed(store, first, 1, "2026-01-01")
    filed(store, second, 2, "2026-02-01")
    body = section(read(), name)
    marks = ("First in file", "Second in file") if kind == "thread" else ("FIRST IN FILE", "SECOND IN FILE")
    assert body.index(marks[0]) < body.index(marks[1])


@pytest.mark.parametrize("kind,name", [("thread", "Open threads (index)"), ("rule", "Rules")])
def test_card_written_since_comes_before_filed_cards(tmp_path, kind, name):
    store, add, read, _, _ = fixture(tmp_path)
    old = add(kind, "FILED BODY", title="Filed title")
    filed(store, old, 1, "2026-03-01")
    store.clock = lambda: "2026-02-01"
    add(kind, "FRESH BODY", title="Fresh title")
    body = section(read(), name)
    marks = ("Fresh title", "Filed title") if kind == "thread" else ("FRESH BODY", "FILED BODY")
    assert body.index(marks[0]) < body.index(marks[1])


def test_unlisted_threads_are_named(tmp_path):
    _, add, read, config, config_path = fixture(tmp_path)
    add("thread", "BODY", title="Small title")
    huge = [add("thread", "BODY", title="HUGE TITLE " * 1000) for _ in range(2)]
    config["wake_sections"]["thread_index"] = 80
    config_path.write_text(json.dumps(config), encoding="utf-8")
    result = read()
    info = next(s for s in result["sections"] if s["name"] == "Open threads (index)")
    body = section(result, "Open threads (index)")
    assert info["tokens"] <= 80 and info["left_out"] == 2
    assert "2 more thread(s) not listed" in body
    assert all(card["id"] in body for card in huge)
    assert "HUGE" not in body




def test_seven_sections_exclude_live_decision_card(tmp_path):
    store, add, read, _, _ = fixture(tmp_path)
    card = add("decision", "Historical compass choice.")
    before = store.cards_path.read_bytes()
    result = read()
    assert [s["name"] for s in result["sections"]] == NAMES
    assert len(result["sections"]) == 7
    for name in NAMES:
        body = section(result, name)
        assert card["text"] not in body and card["id"] not in body
    assert store.cards_path.read_bytes() == before


def test_whole_wake_card_fits_one_client_view():
    # Identity and governance remain whole; section budgets count UTF-8 bytes.
    # Reserve 2,000 bytes of the client view for JSON escaping and headings.
    from tools import names

    whole = sum(len((ROOT / names.rel(key)).read_bytes())
                for key in ("identity", "governance"))
    config = json.loads((ROOT / "config/cards.json").read_text(encoding="utf-8"))
    assert whole + sum(config["wake_sections"].values()) <= config["wake_card_bytes"]
    assert config["wake_card_bytes"] <= 46_000 - 2_000
