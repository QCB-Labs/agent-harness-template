"""Read-log and budget fixes before the stage B freeze (S68). Invented fixtures only."""
import json
from pathlib import Path

from tools import cards as c
from tools import threads
from tests.test_one_wake_back import setup as log_setup
from tests.test_recall_answer import card, memory
from tests.test_wake_sections import fixture, section


def reads(memory_root: Path) -> list[dict]:
    path = Path(memory_root) / "private" / "card-reads.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_wake_card_logs_every_card_in_the_log_bundle(tmp_path):
    naming, cfg = log_setup(tmp_path)
    store = [json.loads(x) for x in (tmp_path / "memory/cards/cards.jsonl").read_text(encoding="utf-8").splitlines()]
    shown = [x["id"] for x in store if x["origin"]["key"] in {"header", "waiting,-due,-deferred-and-done",
                                                                 "observations", "what-i-plan-next"}]
    c.read_wake_card(tmp_path, naming, config_path=cfg, record_read=True, wake="SR-90001")
    logged = reads(tmp_path / "memory")[-1]["ids"]
    assert set(shown) <= set(logged)
    assert not {x["id"] for x in store if x["origin"]["key"] == "what-happened,-and-why"} & set(logged)


def test_thread_lines_report_the_positions_shown_not_a_prefix():
    entries = ["a" * 3000, "b" * 2000, "c" * 1000]
    body, shown = threads.fit_line_indexes(entries, 4800, lambda left: [])
    assert shown == [0, 2]
    assert body == entries[0] + "\n" + entries[2]
    assert threads.fit_lines(entries, 4800, lambda left: []) == (body, 2)


def test_section_notice_counts_against_the_budget(tmp_path):
    store, add, read, _, _ = fixture(tmp_path)
    add("relationship", "Place 9 " + "x" * 382)
    add("relationship", "Place 9 " + "y" * 382)
    result = read(here_tags=["place:9"])
    body = section(result, "What's here")
    budget = json.loads((Path(__file__).resolve().parents[1] / "config/cards.json").read_text())["wake_sections"]["whats_here"]
    assert len(body.encode("utf-8")) <= budget
    assert "more card(s) left out" in body


def test_read_card_is_logged_with_its_session(tmp_path):
    root, _ = memory(tmp_path, [card(1)])
    c.read_card(root, "thread-0001")
    assert not (root / "private" / "card-reads.jsonl").exists()
    c.read_card(root, "thread-0001", record_read=True, wake="W5", conversation="conv", session="sealed")
    event = reads(root)[-1]
    assert event["ids"] == ["thread-0001"] and event["session"] == "sealed" and event["wake"] == "W5"


def test_every_read_event_names_its_session(tmp_path):
    root, config = memory(tmp_path, [card(1)])
    c.recall(root, tags=["place:7000"], config_path=config, record_read=True)
    c.record_wake_closed(root, "W1", None)
    assert [e["session"] for e in reads(root)] == ["wake", "wake"]
