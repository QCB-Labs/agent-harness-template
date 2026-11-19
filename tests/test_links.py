"""Derived associations use invented cards and never touch live memory."""
import copy
import json
from pathlib import Path

import pytest
from pydantic_core import to_json

from tools import cards


def card(id, tags, kind="thread", **fields):
    return dict(id=id, tags=tags, kind=kind, text="Remember Mira.", title=id,
                status="live", weight=1, updated_at="2026-10-21", **fields)


@pytest.fixture
def scene(tmp_path):
    memory = tmp_path / "memory"
    (memory / "cards").mkdir(parents=True)
    state = [card("seed", ["resident:12", "place:1", "thing:2", "note:3"]),
             card("missing", ["resident:12", "place:1"]),
             card("bridge", ["thing:2", "place:9"]),
             card("hub", ["place:1"]),
             card("second-hop", ["place:9"]),
             card("query-only", ["resident:12"]),
             card("dead", ["thing:2"], status_reason="closed")]
    state[-1]["status"] = "done"
    state += [card(f"filler-{i}", ["place:1"]) for i in range(10)]
    state += [card(f"note-{i}", ["note:3"], "reflection") for i in range(5)]
    path = memory / "cards" / "cards.jsonl"
    path.write_text("".join(json.dumps(c) + "\n" for c in state), encoding="utf-8")
    config = tmp_path / "config.json"
    settings = dict(recall_bytes=16000, recall_answer_bytes=24000,
                    recall_linked_listed=12,
                    link_tag_prefixes=["resident:", "place:", "thing:", "note:"],
                    link_note_tag_prefixes=["note:"],
                    link_entity_group_prefixes={"places": ["place:"], "things": ["thing:"]})
    config.write_text(json.dumps(settings))
    return memory, state, config, settings


def run(scene, **kwargs):
    memory, _, config, _ = scene
    return cards.recall(memory, tags=["resident:12"], limit=1, config_path=config, **kwargs)


def test_rank_groups_rarity_and_exclusions(scene):
    _, state, _, settings = scene
    result = cards.linked_labels([state[0]], state, {}, settings,
                                 query_tags={"resident:12"}, excluded_ids={"missing"})
    assert list(result) == ["threads", "notes"]
    assert result["threads"][0] == dict(id="bridge", title="bridge", kind="thread",
                                          bytes=14, tag="thing:2")
    ids = [c["id"] for group in result.values() for c in group]
    assert not set(ids) & {"seed", "missing", "second-hop", "query-only", "dead"}
    assert len(result["notes"]) == 3
    assert len(ids) <= 12


def test_weight_recency_ties_and_configured_prefixes(scene):
    _, _, _, settings = scene
    state = [card("seed", ["custom:1"]), card("older", ["custom:1"]),
             card("newer", ["custom:1"])]
    state[2]["created_at"] = "2026-10-22"
    settings = dict(settings, link_tag_prefixes=["custom:"])
    labels = cards.linked_labels([state[0]], state, {}, settings)
    assert [c["id"] for c in labels["threads"]] == ["newer", "older"]
    labels = cards.linked_labels([state[0]], state, {"older": 2}, settings)
    assert labels["threads"][0]["id"] == "older"
    assert cards.linked_labels([state[0]], state, {}, dict(settings, recall_linked_listed=0)) == {}


@pytest.mark.parametrize("prefix", ["name:", "kind:", "date:", "wake:", "ref:"])
def test_descriptive_tags_never_link(scene, prefix):
    settings = scene[3]
    state = [card("seed", [prefix + "value"]), card("other", [prefix + "value"])]
    assert cards.linked_labels([state[0]], state, {}, settings) == {}


def test_group_order_caps_and_note_tag_cap(scene):
    settings = scene[3]
    state = [card("seed", ["thing:1", "note:1"])]
    for kind in ("thread", "relationship", "reflection", "log"):
        state.extend(card(f"{kind}-{i}", ["thing:1"], kind) for i in range(5))
    result = cards.linked_labels([state[0]], state, {}, dict(settings, recall_linked_listed=30))
    assert list(result) == ["threads", "things", "wakes", "relationship"]
    assert all(len(group) == 3 for group in result.values())
    for c in state[1:]:
        c["tags"] = ["note:1"]
    result = cards.linked_labels([state[0]], state, {}, settings)
    assert sum(len(group) for group in result.values()) == 3


def test_recall_off_matches_stage_b_and_full_cards_unchanged(scene):
    memory, _, config, settings = scene
    # Captured from c2fc0df before implementation, using this invented scene.
    answers = json.loads((Path(__file__).parent / "fixtures/links-stage-b.json").read_text())
    queries = ({"tags": ["resident:12"], "limit": 1}, {"tags": []},
               {"tags": ["absent"]}, {"tags": ["place:1"], "history": True})
    for query, expected in zip(queries, answers, strict=True):
        config.write_text(json.dumps(dict(settings, recall_linked_listed=0)))
        assert to_json(cards.recall(memory, config_path=config, record_read=False, **query)) == to_json(expected)
        config.write_text(json.dumps(settings))
        enabled = cards.recall(memory, config_path=config, record_read=False, **query)
        assert enabled["cards"] == expected["cards"]
    assert run(scene, record_read=False)["linked"]


def test_offered_is_not_read_and_memory_is_unchanged(scene):
    memory, _, _, _ = scene
    before = {p.relative_to(memory): p.read_bytes() for p in memory.rglob("*") if p.is_file()}
    result = run(scene)
    offered = [c["id"] for group in result["linked"].values() for c in group]
    event = json.loads((memory / "private/card-reads.jsonl").read_text())
    assert event["offered"] == offered
    assert event["ids"] == [c["id"] for c in result["cards"]]
    assert not set(offered) & set(event["ids"])
    after = {p.relative_to(memory): p.read_bytes() for p in memory.rglob("*")
             if p.is_file() and p.relative_to(memory) != Path("private/card-reads.jsonl")}
    assert after == before
    for p, data in before.items():
        assert (memory / p).read_bytes() == data
    cards.read_card(memory, offered[0], record_read=True)
    events = [json.loads(line) for line in (memory / "private/card-reads.jsonl").read_text().splitlines()]
    assert events[-1]["ids"] == [offered[0]]


def test_linked_gives_way_first_and_logs_only_visible_offers(scene):
    memory, _, config, settings = scene
    answer = run(scene, record_read=False)
    assert answer["linked"]
    without = copy.deepcopy(answer)
    without.pop("linked")
    # Exercise all three pre-existing lists, even though this scene has no
    # file siblings or a separate resident dossier.
    with_lists = dict(copy.deepcopy(answer), same_file={"file": {"cards": []}}, people=[{"id": "person"}])
    expected_lists = copy.deepcopy(with_lists)
    expected_lists.pop("linked")
    cards._limit_recall_answer(with_lists, cards._recall_answer_size(expected_lists))
    assert with_lists == expected_lists
    budget = cards._recall_answer_size(without)
    config.write_text(json.dumps(dict(settings, recall_answer_bytes=budget)))
    trimmed = run(scene)
    assert trimmed == without
    assert json.loads((memory / "private/card-reads.jsonl").read_text())["offered"] == []
    assert trimmed["cards"] == answer["cards"]


def test_note_cap_backfills_people(scene):
    state = [card("seed", ["note:1", "resident:1"])]
    state += [card(f"thread-{i}", ["note:1"]) for i in range(3)]
    state += [card(f"person-note-{i}", ["note:1"], "relationship") for i in range(3)]
    state += [card(f"person-{i}", ["resident:1"], "relationship") for i in range(8)]
    result = cards.linked_labels([state[0]], state, {}, scene[3])
    assert [c["id"] for c in result["relationship"]] == ["person-0", "person-1", "person-2"]


def test_note_cap_counts_all_shared_tags(scene):
    state = [card("seed", ["note:1"] + [f"thing:{i}" for i in range(6)])]
    state += [card(f"candidate-{i}", ["note:1", f"thing:{i}"],
                   "thread" if i < 3 else "relationship") for i in range(6)]
    result = cards.linked_labels([state[0]], state, {}, scene[3])
    assert sum(map(len, result.values())) == 3


def test_places_and_things_use_valid_stored_kinds(scene):
    state = [card("seed", ["place:1", "thing:1"]),
             card("place", ["place:1"], "reflection"),
             card("thing", ["thing:1"], "decision")]
    assert all(c["kind"] in cards.VALID_KINDS for c in state)
    result = cards.linked_labels([state[0]], state, {}, scene[3])
    assert result["places"][0]["kind"] == "reflection"
    assert result["things"][0]["kind"] == "decision"


def test_note_cap_uses_configured_prefix(scene):
    state = [card("seed", ["memo:1", "note:1"])]
    state += [card(f"candidate-{i}", ["memo:1"],
                   "thread" if i < 3 else "relationship") for i in range(6)]
    settings = dict(scene[3], link_tag_prefixes=["memo:", "note:"],
                    link_note_tag_prefixes=["memo:"])
    result = cards.linked_labels([state[0]], state, {}, settings)
    assert sum(map(len, result.values())) == 3
    for c in state[1:]:
        c["tags"] = ["note:1"]
    result = cards.linked_labels([state[0]], state, {}, settings)
    assert sum(map(len, result.values())) == 6


def test_links_off_read_log_matches_stage_b_bytes(scene, monkeypatch):
    memory, _, config, settings = scene
    config.write_text(json.dumps(dict(settings, recall_linked_listed=0)))
    monkeypatch.setattr(cards, "_utc_now", lambda: "2026-10-23T12:00:00Z")
    result = run(scene)
    expected = {"time": "2026-10-23T12:00:00Z", "wake": "read",
                "query": result["searched"], "ids": [c["id"] for c in result["cards"]],
                "refused": result["refused"], "conversation": None, "session": "wake"}
    # Stage B's event, plus only the read meter's two fields (brief 52).
    logged = json.loads((memory / "private/card-reads.jsonl").read_text(encoding="utf-8"))
    assert logged.pop("tool") == "recall"
    assert logged.pop("bytes") > 0
    assert logged == expected


def test_link_recency_uses_card_date_then_id_not_recut_or_log_number(scene):
    state = [card("seed", ["place:1"])]
    for id, date, number in (("old", "2026-10-20", 999),
                              ("z-new", "2026-10-22", 2),
                              ("a-new", "2026-10-22", 1)):
        c = card(id, ["place:1"], "log", created_at=date,
                 origin={"file": f"memory/logs/{number}.md"})
        c["updated_at"] = "2026-10-23" if id == "old" else date
        state.append(c)
    result = cards.linked_labels([state[0]], state, {}, scene[3])
    assert [c["id"] for c in result["wakes"]] == ["a-new", "z-new", "old"]


def test_fixed_groups_then_each_other_kind_alphabetically(scene):
    state = [card("seed", ["place:1", "thing:1", "resident:1"])]
    for kind, tag in (("zebra", "resident:1"), ("relationship", "resident:1"),
                      ("log", "resident:1"), ("reflection", "resident:1"),
                      ("decision", "thing:1"), ("rule", "place:1"),
                      ("thread", "resident:1"), ("alpha", "resident:1")):
        state += [card(f"{kind}-{i}", [tag], kind) for i in range(4)]
    result = cards.linked_labels([state[0]], state, {},
                                 dict(scene[3], recall_linked_listed=30))
    assert list(result) == ["threads", "places", "things", "notes", "wakes",
                            "alpha", "relationship", "zebra"]
    assert all(len(group) == 3 for group in result.values())
    capped = cards.linked_labels([state[0]], state, {}, scene[3])
    assert list(capped) == ["threads", "places", "things", "notes"]


@pytest.mark.parametrize("trim", [False, True])
def test_read_log_counts_each_omission_reason(scene, trim):
    memory, _, config, settings = scene
    state = [card("seed", ["resident:12", "thing:1", "note:1"])]
    state += [card(f"thread-{i}", ["thing:1"]) for i in range(5)]
    state += [card(f"note-{i}", ["note:1"], "reflection") for i in range(5)]
    state += [card(f"wake-{i}", ["thing:1"], "log") for i in range(4)]
    state += [card(f"other-{i}", ["note:1"], "relationship") for i in range(2)]
    (memory / "cards/cards.jsonl").write_text(
        "".join(json.dumps(c) + "\n" for c in state), encoding="utf-8")
    config.write_text(json.dumps(dict(settings, recall_linked_listed=7,
                                     recall_answer_bytes=1 if trim else 24000)))
    result = run(scene)
    event = json.loads((memory / "private/card-reads.jsonl").read_text())
    assert event["omitted"] == {"kind_cap": 7, "total_cap": 2,
                                 "answer_trim": 7 if trim else 0}
    assert len(event["offered"]) == (0 if trim else 7)
    assert len(event["ids"]) == 1
    assert "omitted" not in result
