"""Brief 18: invented keyed shapes, append-only conversion and state recall."""
import asyncio
from collections import Counter
import json
import shutil
from pathlib import Path

import pytest
from tools import cards as c, harness_mcp as harness

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/cards-shapes"
PATTERNS = ROOT / "config/tag-patterns.city.json"
CONFIG = ROOT / "config/cards.json"
CLOSED = {"removed from source file", "re-cut into keyed pieces"}


@pytest.fixture
def source(tmp_path):
    source = tmp_path / "source"
    shutil.copytree(FIXTURE, source)
    return source


def migrate(source):
    return c.migrate(source, source / "naming.json", PATTERNS, output_root=source / "memory")


def store(source):
    return c.CardStore(source / "memory", PATTERNS)


def filed(source, suffix):
    return sorted((card for card in store(source)._current_state().values()
                   if card["origin"]["file"].endswith(suffix) and card.get("status_reason") not in CLOSED),
                  key=lambda card: card["origin"]["order"])


def recut(source, logical, filename):
    return c.recut_file(source, source / "naming.json", PATTERNS, logical, source / filename)


def verify_views(source, tmp_path):
    generated = tmp_path / "generated"
    c.generate_views(source / "memory/cards/cards.jsonl", generated)
    naming = c.load_naming(source / "naming.json")
    for logical, relative in naming["paths"].items():
        for path in c._source_files(source, logical, relative, naming["indexes"].get(logical)):
            assert (generated / path.relative_to(source)).read_bytes() == path.read_bytes()


SHAPES = [
    ("W137-2080-10-05.md", ["header", "needs-operator-attention", "what-happened-and-why", "what-i-learned-and-what-i-plan-next", "what-is-not-owed", "closing-notes"], ["history"] * 6),
    ("W138-2080-10-06.md", ["header"], ["history"]),
    ("W139-2080-10-07.md", ["header", "-repeated-notes", "-repeated-notes-2"], ["history"] * 3),
    ("2080-08-15-1355.md", ["header", "plan"], ["history"] * 2),
    ("paperfold.md", ["header", "entry:2080-09-14", "entry:2080-10-05", "officer-s-impression"], ["current", "history", "history", "current"]),
    ("duplicate-date.md", ["header", "entry:2080-09-14", "entry:2080-09-14-2", "officer-s-impression"], ["current", "history", "history", "current"]),
    ("extra-section.md", ["header", "entry:2080-09-14", "entry:2080-10-05", "officer-s-impression", "shared-projects"], ["current", "history", "history", "current", "current"]),
    ("no-number.md", ["header", "entry:2080-09-14", "entry:2080-10-05", "officer-s-impression"], ["current", "history", "history", "current"]),
    ("crlf-nonascii.md", ["header", "entry:2080-09-14", "entry:2080-10-05", "officer-s-impression"], ["current", "history", "history", "current"]),
]


@pytest.mark.parametrize("filename,keys,roles", SHAPES)
def test_shapes_keys_roles_labels_and_tags(source, filename, keys, roles):
    migrate(source)
    cards = filed(source, filename)
    assert [card["origin"]["key"] for card in cards] == keys
    assert [card["role"] for card in cards] == roles
    raw = (source / cards[0]["origin"]["file"]).read_bytes()
    assert "".join(card["text"] for card in cards).encode() == raw
    if filename.endswith(".md") and cards[0]["kind"] == "log":
        prefix = c._wake_from_path(Path(filename)).upper() if filename.startswith("W") else Path(filename).stem
        assert cards[0]["title"] == prefix + " · header"
        for card in cards:
            if filename.startswith("W"):
                assert "wake:" + prefix.lower() in card["tags"]
            if filename.startswith("W137"):
                assert "date:2080-10-05" in card["tags"]
        if filename.startswith("W139"):
            assert cards[1]["title"] == "W139 · Repeated Notes"
        if filename.startswith("W137"):
            assert "place:9911" in cards[2]["tags"]
            assert all("place:9911" not in card["tags"] for card in cards if card is not cards[2])
            assert "### What I Plan Next" in cards[3]["text"]
            assert cards[3]["title"] == "W137 · What I Learned, and What I Plan Next"
    else:
        handle = Path(filename).stem
        prefix = "Resident · " + handle if handle == "no-number" else "Resident #961 · " + handle
        assert cards[0]["title"] == prefix + " · header"
        assert cards[1]["title"] == prefix + " · 2080-09-14"
        assert cards[3]["title"] == prefix + " · Officer's Impression"
        for card in cards:
            assert "name:" + handle in card["tags"]
            assert ("resident:961" in card["tags"]) == (handle != "no-number")
        assert "## Sourced Observations" in cards[0]["text"]
        assert "continuation without a date" in cards[1]["text"]
        assert "note:28611" not in cards[0]["tags"]


@pytest.mark.parametrize("logical,text,keys", [
    ("journal", "No headings. café", ["header"]),
    ("residents", "# Person\r\n\r\n## Other\r\n- **2080-01-01** Entry\r\n", ["header", "other"]),
    ("residents", "# Person\n\n## Sourced Observations\n- Undated\n\n## Other\nBody", ["header", "sourced-observations", "other"]),
    ("journal", "## 🧵 Élan 你好\nA\n## Élan 你好\nB", ["-élan-你好", "élan-你好"]),
    ("journal", "# Title\n## Header\nA\n## Header-2\nB\n## Header\nC", ["header", "header-2", "header-2-2", "header-3"]),
])
def test_fallback_and_unicode_keys(source, logical, text, keys):
    prepared = c._prepare_pieces(logical, Path("W999.md"), text, "W999.md", c.load_patterns(PATTERNS))
    assert [item[3]["key"] for item in prepared] == keys
    assert "".join(item[0]["text"] for item in prepared) == text
    if logical == "residents":
        assert all(item[4] == "current" for item in prepared)


def test_label_limit_and_settings_are_used(source):
    patterns = c.load_patterns(PATTERNS)
    cfg = patterns["card_shapes"]["residents"]
    cfg.update(entries_heading=r"^## Dated Evidence\s*$", header_label="preamble", prefix="Person {id} / {handle}", label="{prefix} / {section}")
    text = "# Person\n- **Resident ID:** 42\n- **Handle:** cafebird\n## Dated Evidence\n- **2080-01-01** evidence\n## " + "é" * 110 + "\ntext"
    prepared = c._prepare_pieces("residents", Path("different.md"), text, "different.md", patterns)
    assert prepared[0][0]["title"] == "Person 42 / cafebird / preamble"
    assert prepared[1][3]["key"] == "entry:2080-01-01"
    assert prepared[1][4] == "history"
    title = prepared[-1][0]["title"]
    assert len(title) == 100 and title.endswith("...")
    assert "name:cafebird" in prepared[1][3]["file_tags"]


def test_combined_write_keeps_header_entries_and_impression_ids(source):
    migrate(source)
    before = filed(source, "paperfold.md")
    path = source / before[0]["origin"]["file"]
    raw = path.read_bytes().replace(b"Last Interaction:** 2080-10-05", b"Last Interaction:** 2080-10-06")
    raw = raw.replace(b"## Officer's Impression", b"- **2080-10-06** [source: `city note #28612`]: Painted a cup.\r\n\r\n## Officer's Impression")
    raw = raw.replace(b"An invented paper-folding neighbour.", b"An invented cup-painting neighbour.")
    initial = store(source).cards_path.read_bytes()
    path.write_bytes(raw)
    result = recut(source, "residents", before[0]["origin"]["file"])
    after = {card["origin"]["key"]: card for card in filed(source, "paperfold.md")}
    for old in before:
        assert after[old["origin"]["key"]]["id"] == old["id"]
        versions = c.read_card(source / "memory", old["id"], history=True)["history"]
        assert len(versions) == (2 if old["role"] == "current" else 1)
        if old["role"] == "history":
            assert versions[0] == old
    assert set(result["changed"]) == {before[0]["id"], before[3]["id"]}
    assert result["new"] == [after["entry:2080-10-06"]["id"]]
    assert not result["removed"]
    assert store(source).cards_path.read_bytes().startswith(initial)


def old_store(source):
    naming = c.load_naming(source / "naming.json")
    old = store(source)
    result = []
    for logical in ("journal", "residents"):
        for path in c._source_files(source, logical, naming["paths"][logical], naming["indexes"].get(logical)):
            text = c._read_source_text(path)
            result.append(old.write({"kind": "log" if logical == "journal" else "relationship", "text": text},
                                    wake="old", source="self", channel="migration",
                                    origin={"logical": logical, "file": path.relative_to(source).as_posix(), "position": 0, "order": 1}))
    return result


@pytest.mark.parametrize("logical", ["journal", "residents"])
def test_old_store_conversion_append_only_exact_and_second_run_noop(source, tmp_path, logical):
    old = old_store(source)
    initial = store(source).cards_path.read_bytes()
    first = c.recut_all(source, source / "naming.json", PATTERNS, logical)
    assert first["files"] == first["closed"] == 10
    assert first["made"] > first["closed"]
    assert store(source).cards_path.read_bytes().startswith(initial)
    for card in old:
        read = c.read_card(source / "memory", card["id"], history=True)
        if card["origin"]["logical"] == logical:
            assert read["card"]["status_reason"] == "re-cut into keyed pieces"
            assert read["card"]["status"] == "done"
            assert read["history"][0] == card and len(read["history"]) == 2
        else:
            assert read["history"] == [card]
    before_second = store(source).cards_path.read_bytes()
    second = c.recut_all(source, source / "naming.json", PATTERNS, logical)
    assert second == dict(files=10, made=0, closed=0, changed=0, left_alone=first["made"])
    assert store(source).cards_path.read_bytes() == before_second
    generated = tmp_path / "generated"
    c.generate_views(store(source).cards_path, generated)
    for card in old:
        name = card["origin"]["file"]
        assert (generated / name).read_bytes() == (source / name).read_bytes()


def test_fresh_migration_equals_recut(source, tmp_path):
    fresh = migrate(source)
    separate = tmp_path / "separate"
    shutil.copytree(FIXTURE, separate)
    for logical in c.LOGICAL_NAMES:
        c.recut_all(separate, separate / "naming.json", PATTERNS, logical)
    def comparable(card):
        return {k: v for k, v in card.items() if k not in {"created_at", "updated_at"}}
    assert [comparable(card) for card in fresh] == [comparable(card) for card in store(separate)._current_state().values()]


def test_all_fixtures_rebuild_before_and_after_recut(source, tmp_path):
    migrate(source)
    verify_views(source, tmp_path / "before")
    before = store(source).cards_path.read_bytes()
    for logical in c.LOGICAL_NAMES:
        counts = c.recut_all(source, source / "naming.json", PATTERNS, logical)
        assert counts["made"] == counts["changed"] == counts["closed"] == 0
    assert store(source).cards_path.read_bytes() == before
    verify_views(source, tmp_path / "after")


def test_removed_key_stays_readable_but_out_of_views(source, tmp_path):
    migrate(source)
    cards = filed(source, "paperfold.md")
    removed = cards[1]
    path = source / removed["origin"]["file"]
    path.write_bytes(path.read_bytes().replace(removed["text"].encode(), b""))
    result = recut(source, "residents", removed["origin"]["file"])
    assert result["removed"] == [removed["id"]]
    read = c.read_card(source / "memory", removed["id"], history=True)
    assert read["card"]["status_reason"] == "removed from source file"
    assert read["history"][0] == removed
    before = store(source).cards_path.read_bytes()
    again = recut(source, "residents", removed["origin"]["file"])
    assert not again["removed"] and not again["changed"] and not again["new"]
    assert store(source).cards_path.read_bytes() == before
    verify_views(source, tmp_path)


def test_role_is_a_system_field(source):
    with pytest.raises(c.CardError, match="role"):
        store(source).write({"kind": "log", "text": "x", "role": "history"}, wake="test", source="self", channel="test")


def recall_memory(tmp_path, cards, budget=100, history_budget=40):
    memory = tmp_path / "memory"
    (memory / "cards").mkdir(parents=True)
    records = [dict(status="live", weight=0, title=card["id"], created_at="2080-01-01", updated_at="2080-01-01", **card) for card in cards]
    (memory / "cards/cards.jsonl").write_text("\n".join(json.dumps(card) for card in records) + "\n", encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"recall_bytes": budget, "recall_history_bytes": history_budget}))
    return memory, config


def example(card_id, role="current", size=20, kind="relationship", tags=None, file="person.md", order=1, key=None):
    card = dict(id=card_id, kind=kind, text=card_id + "x" * (size - len(card_id)), tags=tags or ["resident:961"],
                origin=dict(file=file, logical="journal" if kind == "log" else "residents", order=order))
    if role is not None:
        card["role"] = role
    if key:
        card["origin"]["key"] = key
    return card


@pytest.mark.parametrize("history,expected", [(False, ["c", "h3"]), (True, ["c", "h3", "h2"])])
def test_recall_current_first_and_history_share(tmp_path, history, expected):
    memory, config = recall_memory(tmp_path, [example("h1", "history", 40, kind="log", file="W100.md"),
                                            example("h2", "history", 40, kind="log", file="W200.md"),
                                            example("h3", "history", 40, kind="log", file="W300.md"),
                                            example("c", None, 20)])
    result = c.recall(memory, tags=["resident:961"], config_path=config, history=history)
    assert [card["id"] for card in result["cards"]] == expected
    assert result["bytes"] == (100 if history else 60)
    assert sum(c._utf8_bytes(card["text"]) for card in result["cards"] if card.get("role") == "history") <= (100 if history else 40)
    assert all(entry["role"] == "history" for entry in result["left_out_cards"])


def test_history_only_and_date_ordering(tmp_path):
    memory, config = recall_memory(tmp_path, [example("old", "history", key="entry:2080-01-01"), example("new", "history", key="entry:2080-02-01")], history_budget=20)
    result = c.recall(memory, tags=[], config_path=config)
    assert [card["id"] for card in result["cards"]] == ["new"]
    assert result["left_out_cards"][0]["id"] == "old"
    assert result["refused"] is False


def test_history_tag_count_precedes_recency(tmp_path):
    memory, config = recall_memory(tmp_path, [example("old", "history", tags=["resident:961", "name:bird"], file="W100.md"), example("new", "history", tags=["resident:961"], file="W200.md")], history_budget=20)
    result = c.recall(memory, tags=["resident:961", "name:bird"], config_path=config)
    assert [card["id"] for card in result["cards"]] == ["old"]


def test_recall_same_file_cap_and_people(tmp_path):
    cards = [example("entry", "history", tags=["resident:961", "note:99"])]
    cards += [example(str(n), size=20, order=n+2) for n in range(10)]
    memory, config = recall_memory(tmp_path, cards)
    result = c.recall(memory, tags=["note:99"], config_path=config)
    assert [card["id"] for card in result["cards"]] == ["entry"]
    siblings = result["same_file"]["person.md"]
    assert len(siblings["cards"]) == 8 and siblings["more"] == 2
    assert "people" not in result
    result = c.recall(memory, tags=["note:99", "resident:961"], config_path=config, limit=1)
    assert [entry["id"] for entry in result["people"]] == [str(n) for n in range(1, 10)]
    assert all({"id", "title", "bytes"} <= set(entry) for entry in result["people"])


def test_recall_empty_and_zero_budget(tmp_path):
    memory, config = recall_memory(tmp_path, [example("entry", "history")], history_budget=0)
    result = c.recall(memory, tags=[], config_path=config)
    assert not result["cards"] and result["refused"] and result["left_out_cards"][0]["role"] == "history"
    assert 'Read one with read_card(card_id="<id>").' in result["message"]
    empty = c.recall(memory, tags=["place:0"], config_path=config)
    assert empty["matched"] == 0 and not empty["refused"]


@pytest.mark.parametrize("cut", [False, True])
def test_wake_plan_from_newest_cut_or_old_log(source, cut):
    text = "# Invented Log W60000\r\n\r\n## What Happened\r\nCounted spoons.\r\n\r\n## What I Plan Next\r\nVisit the amber shelf.\r\n\r\n## Other\r\nOther text."
    path = source / "memory/mission_log/W60000.md"
    path.write_bytes(text.encode())
    if cut:
        migrate(source)
    else:
        old_store(source)
    result = c.read_wake_card(source, source / "naming.json", config_path=CONFIG)
    assert "Visit the amber shelf." in result["text"]
    assert "Counted spoons." not in result["text"] and "Other text." not in result["text"]
    assert ("W60000 · What I Plan Next" if cut else "# Invented Log W60000") in result["text"]


def test_cut_wake_without_plan_shows_header_and_labels(source):
    path = source / "memory/mission_log/W60000.md"
    path.write_bytes(b"# Log W60000\r\nIntro.\r\n\r\n## First\r\nHidden first body.\r\n\r\n## Second\r\nHidden second body.")
    migrate(source)
    result = c.read_wake_card(source, source / "naming.json", config_path=CONFIG)
    assert "Intro." in result["text"] and "W60000 · First" in result["text"] and "W60000 · Second" in result["text"]
    assert "Hidden first body." not in result["text"] and "Hidden second body." not in result["text"]


def test_wake_here_contains_only_current(source, tmp_path):
    migrate(source)
    cfg = json.loads(CONFIG.read_text())
    cfg["wake_sections"]["whats_here"] = 10000
    config = tmp_path / "config.json"
    config.write_text(json.dumps(cfg))
    result = c.read_wake_card(source, source / "naming.json", config_path=config, here_tags=["name:paperfold"])
    here = result["text"].split("## What's here\n\n", 1)[1]
    assert "# Resident: paperfold" in here and "paper-folding neighbour" in here
    assert "Folded paper." not in here and "Counted cups." not in here


def test_harness_history_argument_and_description(source, monkeypatch):
    migrate(source)
    (source / "config").mkdir()
    shutil.copyfile(CONFIG, source / "config/cards.json")
    monkeypatch.setattr(harness, "REPO", source)
    scheme_memory = source / harness.names.dir_name("memory")
    if not (scheme_memory / "cards").exists():
        shutil.copytree(source / "memory" / "cards", scheme_memory / "cards")
    result = harness.recall(tags=["wake:w137"], history=True)
    assert result["searched"]["history"] is True and result["cards"]
    tool = next(t for t in asyncio.run(harness.mcp.list_tools()) if t.name == "recall")
    assert "history" in tool.inputSchema["properties"]
    assert 'recall(tags=["wake:w137"], history=True)' in tool.description
    wake_tool = next(t for t in asyncio.run(harness.mcp.list_tools()) if t.name == "read_wake_card")
    assert wake_tool.description == "Read the seven-section wake card; content is returned once."
    assert len(c.read_wake_card(source, source / "naming.json", config_path=CONFIG)["sections"]) == 7


def test_recut_cli_prints_counts(source, monkeypatch, capsys):
    old_store(source)
    monkeypatch.setattr("sys.argv", ["cards", "journal", "--repo-root", str(source), "--naming", "naming.json", "--patterns", str(PATTERNS)])
    c.main()
    first = capsys.readouterr().out
    assert first.startswith("Files read: 10; cards made:") and "; closed: 10; changed: 0; left alone: 0.\n" in first
    c.main()
    second = capsys.readouterr().out
    assert second.startswith("Files read: 10; cards made: 0; closed: 0; changed: 0; left alone:")


def test_fixture_metrics(source, capsys):
    cards = migrate(source)
    first = store(source).cards_path.stat().st_size
    wake = c.read_wake_card(source, source / "naming.json", config_path=CONFIG, here_tags=["resident:20000"])
    for logical in ("journal", "residents"):
        c.recut_all(source, source / "naming.json", PATTERNS, logical)
    assert first == store(source).cards_path.stat().st_size
    print("BRIEF18_AFTER=" + json.dumps(dict(cards=len(cards), jsonl_bytes=first, wake_bytes=wake["total_bytes"], by_logical=dict(Counter(card["origin"]["logical"] for card in cards))), sort_keys=True))
    captured = capsys.readouterr()
    # Re-emit the metrics on -s runs for the operator report.
    print(captured.out.strip())



@pytest.mark.parametrize("line_endings", ["checkout", "lf"])
def test_unchanged_kinds_match_baseline_snapshot_and_keep_ids(source, line_endings):
    import hashlib

    if line_endings == "lf":
        naming = c.load_naming(source / "naming.json")
        for logical in c.LOGICAL_NAMES[:4]:
            for path in c._source_files(source, logical, naming["paths"][logical], naming["indexes"].get(logical)):
                path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
    ending = "crlf" if b"\r\n" in (source / "memory/lessons.md").read_bytes() else "lf"
    golden = json.loads((source / "unchanged-cards.json").read_text(encoding="utf-8"))[ending]
    migrated = migrate(source)
    unchanged = [card for card in migrated if card["origin"]["logical"] in c.LOGICAL_NAMES[:4]]
    def snapshot(card):
        return dict(id=card["id"], kind=card["kind"], title=card.get("title"), status=card["status"],
                    origin=card["origin"], sha256=hashlib.sha256(card["text"].encode()).hexdigest())
    assert [snapshot(card) for card in unchanged] == golden
    assert all("role" not in card and "key" not in card["origin"] for card in unchanged)
    for logical in c.LOGICAL_NAMES[:4]:
        counts = c.recut_all(source, source / "naming.json", PATTERNS, logical)
        assert counts["made"] == counts["closed"] == counts["changed"] == 0
    assert [snapshot(store(source)._current_state()[card["id"]]) for card in unchanged] == golden


def test_reordering_sections_keeps_ids_and_updates_origin(source, tmp_path):
    migrate(source)
    before = filed(source, "W137-2080-10-05.md")
    path = source / before[0]["origin"]["file"]
    reordered = [before[0], before[2], before[1], *before[3:]]
    path.write_bytes("".join(card["text"] for card in reordered).encode())
    result = recut(source, "journal", before[0]["origin"]["file"])
    after = filed(source, path.name)
    assert [card["id"] for card in after] == [card["id"] for card in reordered]
    assert not result["new"] and not result["removed"]
    assert result["changed"] == [before[2]["id"], before[1]["id"]]
    verify_views(source, tmp_path)


def test_keyed_metadata_change_versions_once_then_noop(source):
    migrate(source)
    before = filed(source, "paperfold.md")
    card = before[0]
    wrong = store(source).append_version(card, role="history", title="Incorrect metadata", status="done")
    result = recut(source, "residents", card["origin"]["file"])
    assert result["changed"] == [card["id"]]
    restored = c.read_card(source / "memory", card["id"])["card"]
    assert restored["role"] == "current" and restored["status"] == "live" and restored["title"] == card["title"]
    assert len(c.read_card(source / "memory", card["id"], history=True)["history"]) == 3
    again = recut(source, "residents", card["origin"]["file"])
    assert not again["changed"]


def test_date_in_section_is_not_a_file_identity(source):
    text = "# Log W999\n\n## Section\n**Date:** 2080-01-01\n\n## Other\nBody"
    prepared = c._prepare_pieces("journal", Path("W999.md"), text, "W999.md", c.load_patterns(PATTERNS))
    assert all(item[3]["file_tags"] == ["wake:w999"] for item in prepared)


def test_handle_from_header_and_id_not_from_other_sections(source):
    patterns = c.load_patterns(PATTERNS)
    text = "# Resident: HeaderHandle (#42)\n\n## Sourced Observations\n- **2080-01-01** Met resident #99.\n"
    prepared = c._prepare_pieces("residents", Path("different.md"), text, "different.md", patterns)
    assert all(item[3]["file_tags"] == ["name:headerhandle", "resident:42"] for item in prepared)
    assert prepared[0][0]["title"] == "Resident #42 · HeaderHandle · header"
    without = c._prepare_pieces("residents", Path("different.md"), text.replace(" (#42)", ""), "different.md", patterns)
    assert all(item[3]["file_tags"] == ["name:different"] for item in without)


def test_history_named_by_date_sorts_newest_first(tmp_path):
    memory, config = recall_memory(tmp_path, [example("old", "history", kind="log", file="2080-01-01-1000.md"), example("new", "history", kind="log", file="2080-02-01-1000.md")], history_budget=20)
    assert [card["id"] for card in c.recall(memory, tags=[], config_path=config)["cards"]] == ["new"]


def test_history_uses_remaining_budget_after_current(tmp_path):
    memory, config = recall_memory(tmp_path, [example("current", size=80), example("large", "history", size=30), example("small", "history", size=20)])
    result = c.recall(memory, tags=[], config_path=config, history=True)
    assert [card["id"] for card in result["cards"]] == ["current", "small"]
    assert result["bytes"] == 100 and result["left_out_cards"][0]["id"] == "large"


def test_labeled_metadata_does_not_truncate_100_character_label(tmp_path):
    card = example("label")
    card["origin"]["key"] = "long-heading"
    card["title"] = "é" * 97 + "..."
    assert c._left_out_entry(card)["title"] == card["title"]
    card["origin"]["logical"] = "open_threads"
    assert len(c._left_out_entry(card)["title"]) == 80


def test_custom_plan_heading_is_used(source, tmp_path):
    path = source / "memory/mission_log/W60000.md"
    path.write_text("# Log W60000\n\n## Dated Intentions\nVisit a paper lighthouse.\n\n## Other\nHidden body.", encoding="utf-8")
    migrate(source)
    cfg = json.loads(CONFIG.read_text())
    cfg["plan_heading"] = r"(?im)^(#{1,6}) Dated Intentions\r?$"
    config = tmp_path / "config.json"
    config.write_text(json.dumps(cfg))
    result = c.read_wake_card(source, source / "naming.json", config_path=config)
    assert "Visit a paper lighthouse." in result["text"] and "Hidden body." not in result["text"]
    assert "W60000 · Dated Intentions" in result["text"]


def test_legacy_recut_store_metrics(source, capsys):
    old_store(source)
    before = store(source).cards_path.stat().st_size
    results = {name: c.recut_all(source, source / "naming.json", PATTERNS, name) for name in ("journal", "residents")}
    after = store(source).cards_path.stat().st_size
    assert after > before
    print("BRIEF18_LEGACY=" + json.dumps(dict(before_bytes=before, after_bytes=after, counts=results), sort_keys=True))
    captured = capsys.readouterr()
    print(captured.out.strip())


def test_crlf_unicode_fixture_keeps_raw_shape(source):
    raw = (source / "memory/residents/crlf-nonascii.md").read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    assert not raw.endswith(b"\n")
    assert "日本語" in raw.decode("utf-8") and "Сосчитал" in raw.decode("utf-8")


def test_observations_at_first_byte_stay_in_header(source):
    text = "## Sourced Observations\r\n- **2080-01-01** Invented evidence.\r\n\r\n## Other\r\nInvented section."
    patterns = c.load_patterns(PATTERNS)
    patterns["card_shapes"]["residents"]["entries_heading"] = "Sourced Observations"
    prepared = c._prepare_pieces("residents", Path("no-preamble.md"), text, "no-preamble.md", patterns)
    assert "".join(item[0]["text"] for item in prepared).encode() == text.encode()
    assert [item[3]["key"] for item in prepared] == ["header", "entry:2080-01-01", "other"]
    assert [item[4] for item in prepared] == ["current", "history", "current"]
    assert prepared[0][0]["title"] == "Resident · no-preamble · header"



@pytest.mark.parametrize("logical,filename", [("journal", "memory/mission_log/W137-2080-10-05.md"), ("residents", "memory/residents/paperfold.md")])
def test_returning_key_reuses_its_original_id_and_history(source, tmp_path, logical, filename):
    migrate(source)
    before = filed(source, Path(filename).name)
    removed = before[1]
    path = source / filename
    original = path.read_bytes()
    path.write_bytes(original.replace(removed["text"].encode(), b"", 1))
    recut(source, logical, filename)
    path.write_bytes(original)
    restored = recut(source, logical, filename)
    assert not restored["new"] and not restored["removed"]
    assert removed["id"] in restored["changed"]
    after = filed(source, Path(filename).name)
    assert [card["id"] for card in after] == [card["id"] for card in before]
    read = c.read_card(source / "memory", removed["id"], history=True)
    assert read["card"]["status"] == "live" and read["card"].get("status_reason") is None
    assert len(read["history"]) == 3 and read["history"][0] == removed
    verify_views(source, tmp_path)
    initial = store(source).cards_path.read_bytes()
    again = recut(source, logical, filename)
    assert not again["new"] and not again["changed"] and not again["removed"]
    assert store(source).cards_path.read_bytes() == initial


@pytest.mark.parametrize("headings,keys", [("## !!!\r\nSymbol body.\r\n## \r\nEmpty body.", ["header", "-", ""]), ("## 🧵 Élan 你好!\r\nUnicode body.", ["header", "-élan-你好-"])])
def test_keys_use_raw_heading_normalization_and_empty_key_is_stable(source, headings, keys):
    path = source / "memory/mission_log/W61000.md"
    path.write_bytes(("# Invented log W61000\r\n\r\n" + headings).encode())
    migrate(source)
    cards = filed(source, path.name)
    assert [card["origin"]["key"] for card in cards] == keys
    initial = store(source).cards_path.read_bytes()
    result = recut(source, "journal", path.relative_to(source).as_posix())
    assert not result["changed"] and not result["new"] and not result["removed"]
    assert store(source).cards_path.read_bytes() == initial
    assert "".join(card["text"] for card in cards).encode() == path.read_bytes()



def test_rendered_rule_notice_uses_a_callable_tool_signature():
    import ast
    import inspect
    import re

    notice = c._wake_added_section("Rules", [{"id": "rule-0001", "text": "Invented rule body."}], 0)
    calls = re.findall(r"\brecall\([^)]*\)", notice["text"])
    assert len(calls) == 1
    call = ast.parse(calls[0], mode="eval").body
    assert call.func.id == "recall" and not call.args
    kwargs = {keyword.arg: ast.literal_eval(keyword.value) for keyword in call.keywords}
    inspect.signature(harness.recall).bind(**kwargs)
    assert kwargs == {"tags": [], "kinds": ["rule"]}



@pytest.mark.parametrize("separator", ["\u0085", "\u2028", "\u2029"])
def test_unicode_separators_are_text_not_jsonl_record_boundaries(source, tmp_path, separator):
    path = source / "memory/mission_log/W62000.md"
    text = "# Invented log W62000\r\n\r\n## Other\r\nUnicode café" + separator + "続き without final newline"
    path.write_bytes(text.encode())
    migrate(source)
    cards = filed(source, path.name)
    card = next(card for card in cards if separator in card["text"])
    read = c.read_card(source / "memory", card["id"], history=True)
    assert read["card"]["text"] == card["text"] and read["history"] == [card]
    result = c.recall(source / "memory", tags=["wake:w62000"], config_path=CONFIG)
    assert card in result["cards"]
    assert len(c.read_wake_card(source, source / "naming.json", config_path=CONFIG)["sections"]) == 7
    before = store(source).cards_path.read_bytes()
    counts = recut(source, "journal", path.relative_to(source).as_posix())
    assert not counts["changed"] and not counts["new"] and not counts["removed"]
    assert store(source).cards_path.read_bytes() == before
    verify_views(source, tmp_path)


def test_text_recall_accepts_legacy_null_titles(source):
    migrate(source)
    old = next(card for card in store(source)._current_state().values() if card["kind"] == "goal")
    updated = store(source).append_version(old, title=None)
    result = c.recall(source / "memory", tags=["kind:goal"], text=old["text"].splitlines()[0], config_path=CONFIG)
    assert updated in result["cards"] and updated["title"] is None
    assert updated["text"] == old["text"]



@pytest.mark.parametrize("logical,filename,role", [("journal", "memory/mission_log/W63000.md", "history"), ("residents", "memory/residents/empty.md", "current")])
def test_empty_source_is_one_lossless_header_and_recut_noop(source, tmp_path, logical, filename, role):
    path = source / filename
    path.write_bytes(b"")
    migrate(source)
    cards = filed(source, path.name)
    assert len(cards) == 1 and cards[0]["text"] == ""
    assert cards[0]["origin"]["key"] == "header" and cards[0]["role"] == role
    before = store(source).cards_path.read_bytes()
    again = recut(source, logical, filename)
    assert not again["new"] and not again["changed"] and not again["removed"]
    assert store(source).cards_path.read_bytes() == before
    verify_views(source, tmp_path)
    with pytest.raises(c.CardError, match="non-empty"):
        store(source).write({"kind": "rule", "text": ""}, wake="test", source="self", channel="test")
