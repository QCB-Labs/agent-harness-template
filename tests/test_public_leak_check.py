from pathlib import Path
import json

import pytest
from tools import canary_check as c
from tools import profile
from tools import setup_canary


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    # Tests never call the real city; a test that needs it fakes _get_json.
    def offline(path, params, timeout):
        raise OSError("network is off in tests")
    monkeypatch.setattr(c, "_get_json", offline)


@pytest.fixture
def scan(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    setup_canary.setup_canary(repo)
    logs = tmp_path / "logs"
    monkeypatch.setattr(c.paths, "operator_log_dir", lambda: logs)
    return repo, logs


def item(text="ordinary words", **kw):
    return c.PublicItem(kw.pop("kind", "note"), kw.pop("id", 1), 3,
                        "2026-10-16T10:00:00Z", text, **kw)


def batch(*items, checkpoint=1, capped=False):
    def fetch(previous, *, limit, timeout):
        assert timeout == 10 and limit <= 200
        return c.PublicBatch(items, checkpoint, capped)
    return fetch


def test_clean_and_latched_alarm_no_disclosure(scan):
    repo, logs = scan
    assert c.public_leak_check(repo, fetch=batch(item()))["status"] == "CLEAR"
    secret = "sk-" + "x" * 30
    result = c.public_leak_check(repo, fetch=batch(item(secret, id=2, kind="line"), checkpoint=2))
    assert result["status"] == "ALARM"
    assert result["hits"][0]["kinds"] == ["secret"]
    assert result["hits"][0]["id"] == 2
    later = c.public_leak_check(repo, fetch=batch(checkpoint=2))
    assert later["hits"] == result["hits"]
    assert secret not in json.dumps(result) + c.public_leak_summary(result)
    assert secret not in (logs / "public-leak-check.log").read_text()
    assert secret not in (logs / "public-leak-check.json").read_text()
    assert len((logs / "public-leak-check.log").read_text().splitlines()) == 3
    state = json.loads((logs / "public-leak-check.json").read_text())
    state["hits"] = []  # operator acknowledgement
    (logs / "public-leak-check.json").write_text(json.dumps(state))
    assert c.public_leak_check(repo, fetch=batch(checkpoint=2))["status"] == "CLEAR"


def test_canary_only(scan):
    repo, logs = scan
    marker = c.read_marker(repo / "tools" / "secrets_backup.py")
    result = c.public_leak_check(repo, fetch=batch(item(marker)))
    assert result["status"] == "ALARM"
    assert result["hits"][0]["kinds"] == ["canary marker"]
    assert marker not in json.dumps(result) + c.public_leak_summary(result)
    assert marker not in (logs / "public-leak-check.log").read_text()


def test_missing_patterns_retries_without_advancing(scan, monkeypatch, tmp_path):
    repo, logs = scan
    c.public_leak_check(repo, fetch=batch(item()))
    monkeypatch.setenv("AGENT_PRIVATE_PATTERNS", str(tmp_path / "absent"))
    result = c.public_leak_check(repo, fetch=batch(item(id=2), checkpoint=2))
    assert result["status"] == "INCOMPLETE"
    assert "pattern file missing, public check incomplete" in c.public_leak_summary(result)
    assert json.loads((logs / "public-leak-check.json").read_text())["checkpoint"] == 1


def test_failure_preserves_checkpoint_and_alarm_and_redacts_exception(scan):
    repo, logs = scan
    c.public_leak_check(repo, fetch=batch(item("sk-" + "x" * 30)))
    before = (logs / "public-leak-check.json").read_text()
    def broken(*args, **kwargs):
        raise TimeoutError("sk-" + "x" * 40)
    result = c.public_leak_check(repo, fetch=broken)
    assert result["status"] == "ALARM"
    assert "public check did not run (TimeoutError)" in c.public_leak_summary(result)
    assert (logs / "public-leak-check.json").read_text() == before
    assert "x" * 40 not in (logs / "public-leak-check.log").read_text()


def test_an_unreachable_city_fails_closed_and_logs(scan):
    # The autouse guard below makes every real request fail, as offline.
    repo, logs = scan
    result = c.public_leak_check(repo)
    assert result["status"] == "DID NOT RUN"
    assert "public check did not run (OSError)" in c.public_leak_summary(result)
    assert not (logs / "public-leak-check.json").exists()
    assert (logs / "public-leak-check.log").exists()


def test_cap_resumes_and_walk_to_read_is_incomplete(scan):
    repo, logs = scan
    first = c.public_leak_check(repo, fetch=batch(item(), capped=True))
    assert "cap reached" in c.public_leak_summary(first)
    def next_page(previous, **kwargs):
        assert previous == 1
        return c.PublicBatch((item(id=2, readable=False),), 2)
    result = c.public_leak_check(repo, fetch=next_page)
    assert result["status"] == "INCOMPLETE"
    assert result["unreadable"][0]["id"] == 2
    assert "not readable anonymously" in c.public_leak_summary(result)


def test_rejects_other_actor_and_untrusted_metadata(scan):
    repo, logs = scan
    for bad in (item(actor="other"), c.PublicItem("note", 1, 3, "sk-" + "x" * 30)):
        result = c.public_leak_check(repo, fetch=batch(bad))
        assert result["status"] == "DID NOT RUN"
        assert not (logs / "public-leak-check.json").exists()
        assert "x" * 30 not in json.dumps(result)


def test_blocking_kinds_only(scan):
    repo, logs = scan
    # The path is assembled here so the repository's own hygiene scan never
    # sees a literal user path in a tracked file.
    path = "/" + "home" + "/invented/file"
    result = c.public_leak_check(repo, fetch=batch(item(f"made-up-private-name {path} person@example.com")))
    assert result["hits"][0]["kinds"] == ["local-path", "private-pattern"]


def test_local_summary_does_not_fetch(scan, monkeypatch):
    repo, _ = scan
    def forbidden(*args, **kwargs):
        pytest.fail("local wake must not fetch")
    monkeypatch.setattr(c, "fetch_public_batch", forbidden)
    assert c.canary_summary(repo) == "CANARY: CLEAR"


def test_refuses_log_inside_repo(scan, monkeypatch):
    repo, _ = scan
    monkeypatch.setattr(c.paths, "operator_log_dir", lambda: repo / "logs")
    result = c.public_leak_check(repo, fetch=batch(item()))
    assert result["status"] == "DID NOT RUN"
    assert not (repo / "logs").exists()


def test_oversized_batch_and_corrupt_state_fail_closed(scan):
    repo, logs = scan
    result = c.public_leak_check(repo, fetch=batch(*[item(id=i+1) for i in range(201)]))
    assert result["status"] == "DID NOT RUN"
    assert not (logs / "public-leak-check.json").exists()
    (logs / "public-leak-check.json").write_text("broken")
    result = c.public_leak_check(repo, fetch=batch(item()))
    assert result["status"] == "DID NOT RUN"
    assert (logs / "public-leak-check.json").read_text() == "broken"


def test_empty_feed_with_missing_patterns_is_not_clear(scan, monkeypatch, tmp_path):
    repo, _ = scan
    monkeypatch.setenv("AGENT_PRIVATE_PATTERNS", str(tmp_path / "missing"))
    assert c.public_leak_check(repo, fetch=batch())["status"] == "INCOMPLETE"


# The city adapter, against the published shapes (reference:
# search-and-changes#public-read-routes), with the network faked.

def event(event_id, kind, record_id, place=10, actor=profile.handle()):
    field = "note_id" if kind == "note" else "line_id"
    return {"id": event_id, "at": "2026-10-16T12:00:00.000Z", "kind": kind,
            "actor": actor, "detail": {field: record_id, "place_id": place}}


def fake_city(monkeypatch, pages, records, calls=None, ceiling=1000):
    """pages: {(kind, place_id): [page, ...]} served newest first;
    records: {"/api/note/1": {...}}; ceiling: the city's newest event id."""
    served = {}

    def get(path, params, timeout):
        assert timeout == 10
        if calls is not None:
            calls.append((path, dict(params or {})))
        if path == "/api/events" and "kind" not in params:
            return {"events": [{"id": ceiling}], "has_more": True}
        if path == "/api/events":
            key = (params["kind"], params.get("place_id"))
            index = served.get(key, 0)
            served[key] = index + 1
            return pages.get(key, [{"events": [], "has_more": False}])[index]
        return records[path]
    monkeypatch.setattr(c, "_get_json", get)


def test_the_adapter_reads_oldest_first_across_pages_and_kinds(monkeypatch):
    calls = []
    fake_city(monkeypatch, {
        ("note", None): [{"events": [event(9, "note", 3)], "has_more": True, "next_before_id": 9},
                         {"events": [event(5, "note", 2)], "has_more": False}],
        ("line_said", None): [{"events": [event(7, "line_said", 40)], "has_more": False}],
    }, {"/api/note/2": {"note": {"author": profile.handle(), "body": "two"}},
        "/api/note/3": {"note": {"author": profile.handle(), "body": "three"}},
        "/api/line/40": {"line": {"author": profile.handle(), "body": "said"}}}, calls)
    result = c.fetch_public_batch(4)
    assert [(i.kind, i.id, i.text) for i in result.items] == [
        ("note", 2, "two"), ("line", 40, "said"), ("note", 3, "three")]
    # Not capped: the checkpoint moves to the ceiling read before any stream.
    assert result.checkpoint == 1000 and result.capped is False
    events = [p for path, p in calls if path == "/api/events" and "kind" in p]
    assert all(p["actor"] == profile.handle() and p["after_id"] == 4 for p in events)
    assert events[1]["before_id"] == 9


def test_the_adapter_reads_feed_rooms_the_list_left_out(monkeypatch):
    fake_city(monkeypatch, {
        ("note", None): [{"events": [], "has_more": False,
                          "left_out_feed_rooms": {"count": 1, "place_ids": [7008]}}],
        ("note", 7008): [{"events": [event(6, "note", 8, place=7008)], "has_more": False}],
    }, {"/api/note/8": {"note": {"author": profile.handle(), "body": "in the feed room"}}})
    result = c.fetch_public_batch(None)
    assert [(i.id, i.place) for i in result.items] == [(8, 7008)]


def test_the_adapter_caps_and_resumes_from_the_last_item_taken(monkeypatch):
    fake_city(monkeypatch, {("note", None): [{"events": [event(3, "note", 3), event(2, "note", 2),
                                                         event(1, "note", 1)], "has_more": False}]},
              {f"/api/note/{n}": {"note": {"author": profile.handle(), "body": "x"}} for n in (1, 2, 3)})
    result = c.fetch_public_batch(None, limit=2)
    assert [i.id for i in result.items] == [1, 2]
    assert result.checkpoint == 2 and result.capped is True


def test_walk_to_read_and_other_authors_are_unreadable_not_scanned(monkeypatch):
    fake_city(monkeypatch, {("note", None): [{"events": [event(1, "note", 1), event(2, "note", 2),
                                                         event(3, "note", 3, actor="someone")],
                                              "has_more": False}]},
              {"/api/note/1": {"note": {"author": profile.handle(), "walk_to_read": True, "body": "first line"}},
               "/api/note/2": {"note": {"author": "someone-else", "body": "not his"}}})
    items = c.fetch_public_batch(None).items
    assert [(i.id, i.readable, i.text) for i in items] == [(1, False, ""), (2, False, "")]


def test_endless_paging_is_an_error_not_a_clean_result(monkeypatch):
    fake_city(monkeypatch, {("note", None): [{"events": [], "has_more": True, "next_before_id": 1}]
                            * (c.MAX_PAGES + 1)}, {})
    with pytest.raises(RuntimeError):
        c.fetch_public_batch(None)


def test_redirects_are_never_followed():
    handler = c._NoRedirect()
    assert handler.redirect_request(None, None, 302, "Found", {}, "https://example.com/login") is None


def test_an_event_above_the_ceiling_waits_for_the_next_run(monkeypatch):
    # Note 101 arrives after the ceiling was read: it is not taken now, and
    # the checkpoint stops at the ceiling, so the next run still finds it.
    fake_city(monkeypatch, {("note", None): [{"events": [event(101, "note", 2), event(99, "note", 1)],
                                              "has_more": False}]},
              {"/api/note/1": {"note": {"author": profile.handle(), "body": "x"}}}, ceiling=100)
    result = c.fetch_public_batch(None)
    assert [i.id for i in result.items] == [1] and result.checkpoint == 100


def test_a_record_without_a_body_fails_closed(monkeypatch):
    fake_city(monkeypatch, {("note", None): [{"events": [event(5, "note", 1)], "has_more": False}]},
              {"/api/note/1": {"note": {"author": profile.handle()}}})
    with pytest.raises(ValueError):
        c.fetch_public_batch(None)


def test_unreadable_items_are_reported_until_cleared(scan):
    repo, logs = scan
    gap = item("", readable=False)
    first = c.public_leak_check(repo, fetch=batch(gap, checkpoint=10))
    assert first["status"] == "INCOMPLETE" and first["unreadable"]
    second = c.public_leak_check(repo, fetch=batch(checkpoint=11))
    assert second["status"] == "INCOMPLETE" and second["unreadable"] == first["unreadable"]
    state = json.loads((logs / "public-leak-check.json").read_text())
    state["unreadable"] = []
    (logs / "public-leak-check.json").write_text(json.dumps(state))
    assert c.public_leak_check(repo, fetch=batch(checkpoint=12))["status"] == "CLEAR"


def test_a_failed_run_still_reports_known_unreadable_items(scan):
    repo, _ = scan
    first = c.public_leak_check(repo, fetch=batch(item("", readable=False), checkpoint=10))

    def offline(previous, *, limit, timeout):
        raise TimeoutError("city did not answer")
    failed = c.public_leak_check(repo, fetch=offline)
    assert failed["status"] == "DID NOT RUN"
    assert failed["unreadable"] == first["unreadable"]
