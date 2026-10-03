"""The agent reads each site's rules from the site, in bounded pieces. Made-up facts only."""
import pytest

from tools import market_mcp, square_mcp
from tools.official_facts import INLINE_UNDER_BYTES, WHOLE_UNDER_BYTES, official_view

SMALL = {"society": "made-up", "rate_limit": {"posts_per_day": 1}}
LARGE = {"society": "made-up", "rate_limit": {"posts_per_day": 1}, "windows": "x" * (WHOLE_UNDER_BYTES + 1)}


def test_a_small_record_comes_back_whole():
    assert official_view(SMALL, None, source="made-up") == {"source": "made-up", "facts": SMALL}


def test_a_large_record_comes_back_as_an_index():
    view = official_view(LARGE, None, source="made-up")
    assert view["facts"] == SMALL
    assert list(view["larger_fields"]) == ["windows"]
    assert view["larger_fields"]["windows"] > INLINE_UNDER_BYTES
    assert "fields" in view["message"]


def test_named_fields_come_back_in_full():
    view = official_view(LARGE, ["windows"], source="made-up")
    assert view["facts"] == {"windows": LARGE["windows"]}


def test_an_unknown_field_is_refused_with_the_real_names():
    view = official_view(LARGE, ["wrong"], source="made-up")
    assert view["error"] == "UNKNOWN_FIELD"
    assert view["fields"] == sorted(LARGE)


def test_an_upstream_error_passes_through():
    assert official_view({"error": "HTTP_503"}, None, source="made-up") == {"error": "HTTP_503"}
    assert official_view(["not", "an", "object"], None, source="made-up")["error"] == "MALFORMED_RESPONSE"


def test_square_tool_reads_the_squares_own_record(monkeypatch: pytest.MonkeyPatch):
    seen = []

    def fake(method, endpoint, **kwargs):
        seen.append((method, endpoint, kwargs.get("require_auth", False)))
        return SMALL

    monkeypatch.setattr(square_mcp, "_square_request", fake)
    assert square_mcp.read_square_official()["facts"] == SMALL
    assert seen == [("GET", "/api/official", False)]


def test_market_tool_reads_the_markets_own_record(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(market_mcp, "_read_market_official", lambda: LARGE)
    assert list(market_mcp.read_market_official(fields=["society"])["facts"]) == ["society"]


def test_market_tool_reports_a_failed_read(monkeypatch: pytest.MonkeyPatch):
    def broken():
        raise OSError("made-up network failure")

    monkeypatch.setattr(market_mcp, "_read_market_official", broken)
    result = market_mcp.read_market_official()
    assert result["error"] == "OFFICIAL_FACTS_UNAVAILABLE"
