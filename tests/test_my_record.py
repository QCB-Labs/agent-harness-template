"""harness.read_my_record: counts of the agent's own public notes, checked offline.

The city is never called here; _get is replaced with a fixed record.
"""

from __future__ import annotations

import inspect
import re
import urllib.error

import pytest

from tools import harness_mcp, my_record, profile

EVENTS = [
    {"at": "2026-10-04T05:10:00Z", "detail": {"note_id": 1, "place_id": 10}},
    {"at": "2026-10-05T09:19:00Z", "detail": {"note_id": 2, "place_id": 10}},
    {"at": "2026-10-12T13:19:00Z", "detail": {"note_id": 3, "place_id": 20}},
    {"at": "2026-11-01T21:05:00Z", "detail": {"note_id": 4, "place_id": 20}},
]
PLACES = {10: "The Stair", 20: "The Landing"}
THINGS = [
    {"id": 100, "name": "Hop Counter", "created_at": "2026-10-03T00:00:00Z", "place_id": 10},
    {"id": 101, "name": "A shelf", "created_at": "2026-10-20T00:00:00Z", "place_id": 20},
]
BODIES = {
    1: "Thirteen hops to the sill. My boots were cold.",
    2: "I hope the booth is open. One hop, then the shop. He hoped; she hopes.",
    3: "We hopped and kept hopping past the sills.",
    4: "No climbing today. Every landing has its own acoustics.",
}


class City:
    def __init__(self):
        self.calls = []
        self.events = list(EVENTS)
        self.bodies = dict(BODIES)
        self.pages = None      # optional list of event pages, to test paging
        self.broken = set()    # note ids whose fetch raises
        self.things = list(THINGS)
        self.search_broken = False
        self.left_out = 0

    def get(self, path, params=None, timeout=None):
        self.calls.append((path, dict(params or {})))
        if path == "/api/events":
            assert params["actor"] == profile.handle() and params["kind"] == "note"
            if self.pages is not None:
                return self.pages[min(len(self.calls_to("/api/events")) - 1, len(self.pages) - 1)]
            return {"events": self.events, "has_more": False,
                    "left_out_feed_rooms": {"count": self.left_out, "place_ids": []}}
        if path == "/api/search":
            assert params["maker"] == profile.handle() and params["type"] == "thing"
            if self.search_broken:
                raise OSError("search down")
            return {"results": self.things}
        if path.startswith("/api/place/"):
            return {"place": {"name": PLACES[int(path.rsplit("/", 1)[1])]}}
        note_id = int(path.rsplit("/", 1)[1])
        if note_id in self.broken:
            raise OSError("network down")
        if note_id not in self.bodies:
            raise urllib.error.HTTPError(path, 404, "gone", {}, None)
        return {"note": {"id": note_id, "body": self.bodies[note_id]}}

    def calls_to(self, prefix):
        return [c for c in self.calls if c[0].startswith(prefix)]


@pytest.fixture(autouse=True)
def city(monkeypatch):
    fake = City()
    monkeypatch.setattr(my_record, "_get", fake.get)
    my_record._cache.clear()
    my_record._place_names.clear()
    yield fake
    my_record._cache.clear()
    my_record._place_names.clear()


def test_whole_words_and_forms_listed():
    r = harness_mcp.read_my_record(word="hop")
    assert r["notes_read"] == 4
    assert r["notes_containing"] == 3
    assert r["forms_counted"] == {"hops": 1, "hop": 1, "hopped": 1, "hopping": 1}
    for wrong in ("hope", "hoped", "hopes", "shop"):
        assert wrong not in r["forms_counted"] and wrong not in r["forms_looked_for"]


@pytest.mark.parametrize("word, wanted, unwanted", [
    ("hop", {"hops", "hopped", "hopping", "hopper"}, {"hopes", "hoped", "hoping"}),
    ("move", {"moves", "moved", "moving", "mover"}, {"moveing", "moveed"}),
    ("carry", {"carries", "carried", "carrying", "carrier"}, {"carrys", "carryed"}),
    ("audit", {"audits", "audited", "auditing"}, {"auditted", "auditting"}),
    ("boot", {"boots"}, {"booth"}),
    ("box", {"boxes"}, {"boxs", "boxxed"}),
    ("stay", {"stays", "stayed"}, {"staies", "staied"}),
    ("die", {"dies", "died", "dying"}, {"diing"}),
    ("see", {"sees", "seeing", "seer"}, {"seing", "seed"}),
    ("open", {"opens", "opened", "opening"}, {"openned", "openning"}),
    ("hop", set(), {"hoply"}),
])
def test_forms_follow_spelling_rules(word, wanted, unwanted):
    found = my_record.forms(word)
    assert wanted <= found
    assert not (unwanted & found)


def test_boot_does_not_count_booth():
    r = harness_mcp.read_my_record(word="boot")
    assert r["forms_counted"] == {"boots": 1}
    assert r["first_use"] == {"note_id": 1, "date": "2026-10-04", "place_id": 10,
                              "excerpt": "My boots were cold."}


def test_through_is_inclusive_and_reported():
    r = harness_mcp.read_my_record(word="sill", through="2026-10-05")
    assert r["notes_read"] == 2 and r["through"] == "2026-10-05"
    assert r["notes_containing"] == 1 and r["last_use"]["note_id"] == 1


def test_notes_after_the_cut_off_are_not_fetched(city):
    city.broken = {4}
    r = harness_mcp.read_my_record(word="hop", through="2026-10-31")
    assert r["notes_read"] == 3
    assert ("/api/note/4", {}) not in city.calls


def test_weeks_run_sunday_to_saturday():
    r = harness_mcp.read_my_record(word="hop")
    weeks = [row["period"] for row in r["by_week"]]
    # 4 Oct 2026 is a Sunday; 5 Oct joins it; 12 Oct is Monday of the next week.
    assert weeks == ["2026-10-04", "2026-10-11", "2026-11-01"]
    assert r["by_week"][0] == {"period": "2026-10-04", "notes": 2, "containing": 2, "uses": 2,
                               "places": [{"place_id": 10, "uses": 2, "name": "The Stair"}]}


def test_by_month_and_phrase():
    r = harness_mcp.read_my_record(word="its own acoustics", by="month")
    assert r["notes_containing"] == 1 and r["uses"] == 1
    assert [row["period"] for row in r["by_month"]] == ["2026-10", "2026-11"]


def test_word_is_normalised():
    assert harness_mcp.read_my_record(word="  Hop ")["word"] == "hop"


def test_hyphenated_query_is_refused_with_the_way_to_ask():
    r = harness_mcp.read_my_record(word="record-keeping")
    assert r["status"] == "REFUSED" and "record keeping" in r["reason"]


def test_timing_uses_events_only(city):
    r = harness_mcp.read_my_record(timing=True)
    assert r["notes_by_utc_hour"] == {"05": 1, "09": 1, "13": 1, "21": 1}
    assert r["notes_by_minute_past_hour"]["19"] == 2
    assert city.calls_to("/api/note/") == []


@pytest.mark.parametrize("kwargs", [
    {},
    {"word": "hop", "timing": True},
    {"word": "hop!"},
    {"word": "../memory/private"},
    {"word": "a b c d e"},
    {"word": "hop", "by": "day"},
    {"word": "hop", "through": "29 Oct"},
    {"word": "hop", "through": "2026-02-30"},
])
def test_bad_arguments_are_refused_without_a_fetch(kwargs, city):
    r = harness_mcp.read_my_record(**kwargs)
    assert r["status"] == "REFUSED"
    assert city.calls == []


def test_one_failed_body_refuses_the_whole_answer(city):
    city.broken = {3}
    r = harness_mcp.read_my_record(word="hop")
    assert r["status"] == "UNAVAILABLE" and "nothing was counted" in r["reason"]


def test_good_bodies_are_kept_after_a_failure(city):
    city.broken = {3}
    harness_mcp.read_my_record(word="hop")
    city.broken = set()
    city.calls.clear()
    r = harness_mcp.read_my_record(word="hop")
    assert r["notes_read"] == 4
    # Note 3 failed and is fetched again; bodies that arrived are not. A fetch
    # cancelled by the failure may be retried too.
    refetched = [c[0] for c in city.calls_to("/api/note/")]
    assert "/api/note/3" in refetched and len(refetched) < 4


def test_gone_note_is_reported_not_counted(city):
    del city.bodies[2]
    r = harness_mcp.read_my_record(word="hop")
    assert r["notes_read"] == 3 and r["notes_gone"] == 1


@pytest.mark.parametrize("page", [
    {"error": "temporary failure"},
    {"events": "nope", "has_more": False},
    {"events": [{"at": "soon", "detail": {"note_id": 1}}], "has_more": False},
    {"events": [{"at": "2026-10-04T05:10:00Z", "detail": {}}], "has_more": False},
    {"events": EVENTS},                                                    # no has_more
    {"events": EVENTS, "has_more": "no"},
    {"events": [{"at": "2026-99-99T00:00:00Z", "detail": {"note_id": 1}}], "has_more": False},
])
def test_malformed_event_pages_are_unavailable(city, page):
    city.pages = [page]
    assert harness_mcp.read_my_record(word="hop")["status"] == "UNAVAILABLE"


def test_malformed_body_is_unavailable_and_not_cached(city, monkeypatch):
    real = city.get

    def bad_body(path, params=None, timeout=None):
        if path == "/api/note/2":
            city.calls.append((path, {}))
            return {"error": "temporary failure"}
        return real(path, params)
    monkeypatch.setattr(my_record, "_get", bad_body)
    assert harness_mcp.read_my_record(word="hop")["status"] == "UNAVAILABLE"
    assert 2 not in my_record._cache


def test_paging_reads_every_page(city):
    city.pages = [
        {"events": EVENTS[2:], "has_more": True, "next_before_id": 3},
        {"events": EVENTS[:2], "has_more": False},
    ]
    assert harness_mcp.read_my_record(word="hop")["notes_read"] == 4


@pytest.mark.parametrize("pages", [
    [{"events": EVENTS, "has_more": True, "next_before_id": 10}],          # cursor repeats
    [{"events": EVENTS, "has_more": True}],                                # no cursor
    [{"events": EVENTS, "has_more": True, "next_before_id": 5},
     {"events": EVENTS, "has_more": True, "next_before_id": 9}],          # cursor goes back
])
def test_paging_that_does_not_advance_is_unavailable(city, pages):
    city.pages = pages
    r = harness_mcp.read_my_record(word="hop")
    assert r["status"] == "UNAVAILABLE"
    assert len(city.calls_to("/api/events")) <= 3


def test_bodies_are_cached_then_refreshed(city, monkeypatch):
    harness_mcp.read_my_record(word="hop")
    harness_mcp.read_my_record(word="sill")
    assert len(city.calls_to("/api/note/")) == 4
    city.bodies[1] = "Nothing about climbing."
    clock = [my_record.time.monotonic() + my_record.CACHE_SECONDS + 1]
    monkeypatch.setattr(my_record.time, "monotonic", lambda: clock[0])
    r = harness_mcp.read_my_record(word="hop")
    assert len(city.calls_to("/api/note/")) == 8
    assert r["notes_containing"] == 2


def test_deadline_refuses_rather_than_hangs(city, monkeypatch):
    monkeypatch.setattr(my_record, "_DEADLINE", -1)
    assert harness_mcp.read_my_record(word="hop")["status"] == "UNAVAILABLE"


def test_caller_cannot_choose_actor_url_or_path():
    params = set(inspect.signature(harness_mcp.read_my_record).parameters)
    assert params == {"word", "timing", "by", "through"}


def test_nothing_written_to_disk():
    source = inspect.getsource(my_record)
    assert not re.search(r"(?<![\w.])open\(", source)
    for forbidden in ("write_text", "write_bytes", "Path("):
        assert forbidden not in source


def test_answer_carries_the_context_line():
    r = harness_mcp.read_my_record(word="hop")
    assert r["context"] == my_record.CONTEXT_LINE
    assert "isolated monologue" in r["context"]


def test_things_named_with_the_word_are_split_from_mentions():
    r = harness_mcp.read_my_record(word="hop")
    assert [t["thing_id"] for t in r["things_you_made"]["named"]] == [100]
    assert [t["thing_id"] for t in r["things_you_made"]["mentioned_in_text"]] == [101]


def test_things_made_after_the_cut_off_are_left_out():
    r = harness_mcp.read_my_record(word="hop", through="2026-10-05")
    assert [t["thing_id"] for t in r["things_you_made"]["mentioned_in_text"]] == []


def test_places_are_named_in_each_row():
    r = harness_mcp.read_my_record(word="hop")
    assert r["by_week"][1]["places"] == [{"place_id": 20, "uses": 2, "name": "The Landing"}]


def test_failed_search_keeps_counts_and_place_names(city):
    city.search_broken = True
    r = harness_mcp.read_my_record(word="hop")
    assert r["notes_containing"] == 3
    assert "things could not be read" in r["context_unavailable"]
    assert "things_you_made" not in r
    assert r["by_week"][0]["places"][0]["name"] == "The Stair"


def test_undated_things_are_not_treated_as_before_the_cut_off(city):
    city.things = [{"id": 1, "name": "Hop Counter"}]
    r = harness_mcp.read_my_record(word="hop", through="2026-10-05")
    assert r["things_you_made"]["named"] == []
    assert r["things_you_made"]["left_out_without_a_date"] == 1


def test_a_full_search_page_says_there_may_be_more(city):
    city.things = [{"id": i, "name": "Hop", "created_at": "2026-10-01T00:00:00Z"}
                   for i in range(my_record._MAX_THINGS)]
    r = harness_mcp.read_my_record(word="hop")
    assert "there may be more" in r["things_you_made"]["search_limit"]


def test_first_and_last_use_carry_an_excerpt():
    r = harness_mcp.read_my_record(word="hop")
    assert r["first_use"]["excerpt"] == "Thirteen hops to the sill."
    assert r["first_use"]["place_id"] == 10
    assert r["last_use"]["excerpt"] == "We hopped and kept hopping past the sills."


def test_notes_the_city_left_out_are_reported(city):
    city.left_out = 2
    assert harness_mcp.read_my_record(word="hop")["notes_left_out_by_city"] == 2


def test_timing_has_no_context_lookups(city):
    harness_mcp.read_my_record(timing=True)
    assert city.calls_to("/api/search") == [] and city.calls_to("/api/place/") == []
