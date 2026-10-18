"""Brief 18b: bounded recall answers over invented temporary stores."""
import asyncio
import inspect
import json
from pathlib import Path

import pytest
from tools import cards as c, harness_mcp as harness

ROOT = Path(__file__).resolve().parents[1]


def card(number, kind="thread", role="current", size=1000, tags=None, file=None):
    logical = {"thread": "open_threads", "lesson": "lessons", "rule": "city_rules", "goal": "goals",
               "log": "journal", "relationship": "residents"}[kind]
    return dict(id=f"{kind}-{number:04d}", kind=kind, role=role, text="é" * (size // 2),
                title=f"Invented {number}", tags=tags or ["place:7000"], status="live", weight=0,
                created_at="2080-01-01", updated_at="2080-01-01",
                origin=dict(file=file or f"{logical}.md", logical=logical, order=number, key=str(number)))


def memory(tmp_path, cards, **settings):
    root = tmp_path / "memory"
    (root / "cards").mkdir(parents=True)
    (root / "cards/cards.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in cards), encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps(dict(recall_bytes=16000, recall_history_bytes=6000,
                                     recall_answer_bytes=24000, recall_left_out_listed=12, **settings)))
    return root, config


def crowd():
    cards = [card(n, size=3000) for n in range(16)]
    cards += [card(n, "log", "history", 1500, file=f"W{100+n}.md") for n in range(20)]
    cards += [card(n, "relationship", size=2000, tags=["resident:961", "place:7000"], file="paperfold.md") for n in range(10)]
    cards += [card(n+10, "relationship", "history", 1200, tags=["resident:961", "place:7000"], file="paperfold.md") for n in range(12)]
    return cards


@pytest.mark.parametrize("tags,history", [(["place:7000"], False), (["place:7000"], True), (["resident:961"], False)])
def test_whole_answer_fits_exact_harness_serialization(tmp_path, monkeypatch, tags, history):
    mem, config = memory(tmp_path, crowd())
    (tmp_path / "config").mkdir()
    (tmp_path / "config/cards.json").write_bytes(config.read_bytes())
    monkeypatch.setattr(harness, "REPO", tmp_path)
    monkeypatch.setattr(harness, "_ensure_cards", lambda: None)
    result = harness.recall(tags=tags, limit=40, history=history)
    content = asyncio.run(harness.mcp.call_tool("recall", dict(tags=tags, limit=40, history=history)))
    blocks = content[0] if isinstance(content, tuple) else content
    encoded = blocks[0].text.encode("utf-8")
    assert len(encoded) == c._recall_answer_size(result) <= 24000
    assert json.loads(encoded) == result
    assert result["bytes"] <= 16000


def test_capped_omissions_and_complete_counts(tmp_path):
    mem, config = memory(tmp_path, crowd())
    result = c.recall(mem, tags=["place:7000"], config_path=config, limit=40)
    assert len(result["left_out_cards"]) == 12
    assert all(set(entry) == {"id", "kind", "role", "title", "bytes"} for entry in result["left_out_cards"])
    counts = result["left_out_counts"]
    assert counts["total"] == sum(counts["by_kind"].values()) == sum(counts["by_role"].values()) == result["left_out"]
    returned = {x["id"] for x in result["cards"]}
    missing = [x for x in crowd() if x["id"] not in returned]
    assert counts["by_kind"] == {kind: sum(x["kind"] == kind for x in missing) for kind in counts["by_kind"]}
    assert counts["by_role"] == {role: sum(x["role"] == role for x in missing) for role in counts["by_role"]}
    assert 'Read one with read_card(card_id="<id>").' in result["message"]


@pytest.mark.parametrize("removed", [1, 2, 3])
def test_lists_give_way_in_order_without_dropping_cards(tmp_path, removed):
    mem, config = memory(tmp_path, crowd())
    cfg = json.loads(config.read_text())
    cfg["recall_answer_bytes"] = 100000
    config.write_text(json.dumps(cfg))
    full = c.recall(mem, tags=["resident:961", "place:7000"], limit=3, config_path=config)
    order = ["same_file", "people", "left_out_cards"]
    expected = dict(full)
    for field in order[:removed]:
        expected.pop(field)
    cfg["recall_answer_bytes"] = c._recall_answer_size(expected)
    config.write_text(json.dumps(cfg))
    result = c.recall(mem, tags=["resident:961", "place:7000"], limit=3, config_path=config)
    assert result == expected
    assert result["cards"] == full["cards"] and result["bytes"] == full["bytes"]
    assert c._recall_answer_size(result) <= cfg["recall_answer_bytes"]


@pytest.mark.parametrize("kind", ["thread", "lesson", "rule", "goal"])
def test_unchanged_kinds_have_no_sibling_index(tmp_path, kind):
    mem, config = memory(tmp_path, [card(n, kind) for n in range(3)])
    result = c.recall(mem, tags=[], limit=1, config_path=config)
    assert "same_file" not in result


def test_siblings_once_per_file_and_compact_references(tmp_path):
    mem, config = memory(tmp_path, [card(n, "relationship", size=40) for n in range(14)])
    result = c.recall(mem, tags=[], limit=3, config_path=config)
    assert len(result["cards"]) == 3 and len(result["same_file"]) == 1
    siblings = result["same_file"]["residents.md"]
    assert len(siblings["cards"]) == 8 and siblings["more"] == 3
    assert all(set(entry) == {"id", "title", "bytes"} for entry in siblings["cards"])
    assert not {x["id"] for x in result["cards"]}.intersection(x["id"] for x in siblings["cards"])


@pytest.mark.parametrize("tags,people", [(["place:7000"], False), (["resident:961"], True), (["RESIDENT:961", "place:7000"], True)])
def test_people_requires_resident_query_tag(tmp_path, tags, people):
    mem, config = memory(tmp_path, [card(n, "relationship", tags=["resident:961", "place:7000"]) for n in range(3)])
    result = c.recall(mem, tags=tags, limit=1, config_path=config)
    assert ("people" in result) == people
    if people:
        assert all(set(entry) == {"id", "title", "bytes"} for entry in result["people"])


def test_empty_indexes_are_absent(tmp_path):
    mem, config = memory(tmp_path, [card(1)])
    for tags in ([], ["place:0"]):
        result = c.recall(mem, tags=tags, config_path=config)
        assert not {"left_out_cards", "same_file", "people"}.intersection(result)
        assert result["left_out_counts"] == {"total": 0, "by_kind": {}, "by_role": {}}


def test_history_is_reserved_even_with_current_matches_beyond_limit(tmp_path):
    mem, config = memory(tmp_path, [card(n, size=3000) for n in range(12)] + [card(1, "log", "history", 5000)])
    result = c.recall(mem, tags=[], config_path=config)
    assert result["bytes"] == 14000
    assert sum(x["role"] == "current" for x in result["cards"]) == 3
    assert result["cards"][-1]["role"] == "history"


def test_current_has_full_budget_when_no_history_matches(tmp_path):
    mem, config = memory(tmp_path, [card(n, size=3000) for n in range(12)] + [card(1, "log", "history", 5000, tags=["place:8000"])])
    result = c.recall(mem, tags=["place:7000"], config_path=config)
    assert result["bytes"] == 15000 and len(result["cards"]) == 5


def test_history_true_preserves_current_first_allocation(tmp_path):
    mem, config = memory(tmp_path, [card(n, size=3000) for n in range(6)] + [card(1, "log", "history", 5000)])
    result = c.recall(mem, tags=[], config_path=config, history=True)
    assert result["bytes"] == 15000 and all(x["role"] == "current" for x in result["cards"])


def test_cards_alone_over_answer_limit_are_preserved_and_reported(tmp_path):
    mem, config = memory(tmp_path, [card(n, size=1000) for n in range(3)])
    cfg = json.loads(config.read_text())
    cfg["recall_answer_bytes"] = 1000
    config.write_text(json.dumps(cfg))
    result = c.recall(mem, tags=[], limit=1, config_path=config)
    assert result["cards"][0]["text"] == card(2)["text"]
    assert not {"left_out_cards", "same_file", "people"}.intersection(result)
    assert "exceed the whole-answer limit of 1000 bytes." in result["message"]
    assert 'Read one with read_card(card_id="<id>").' in result["message"]


def test_restored_read_call_binds_real_harness_signature(tmp_path):
    mem, config = memory(tmp_path, [card(n) for n in range(3)])
    result = c.recall(mem, tags=[], limit=1, config_path=config)
    sentence = 'Read one with read_card(card_id="<id>").'
    assert result["message"].endswith(sentence)
    assert inspect.signature(harness.read_card).bind(card_id="<id>").arguments == {"card_id": "<id>"}


def test_small_message_still_fits_after_lists_are_removed(tmp_path):
    mem, config = memory(tmp_path, crowd())
    full = c.recall(mem, tags=[], limit=1, config_path=config)
    base = {k: v for k, v in full.items() if k not in {"same_file", "people", "left_out_cards", "message"}}
    base["message"] = f'{full["left_out"]} matching card(s) left out. Read one with read_card(card_id="<id>").'
    cfg = json.loads(config.read_text())
    cfg["recall_answer_bytes"] = c._recall_answer_size(base)
    config.write_text(json.dumps(cfg))
    result = c.recall(mem, tags=[], limit=1, config_path=config)
    assert result == base
