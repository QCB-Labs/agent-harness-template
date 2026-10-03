"""Tests for The Square (1F916.ai) MCP connector (tools/square_mcp.py)."""

from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from tools import square_mcp



def _mock_response(data: dict | list | str, status: int = 200) -> MagicMock:
    if isinstance(data, (dict, list)):
        payload = json.dumps(data).encode("utf-8")
    else:
        payload = data.encode("utf-8")
    resp = MagicMock()
    resp.read.return_value = payload
    resp.status = status
    resp.__enter__.return_value = resp
    return resp


@pytest.mark.asyncio
async def test_server_exposes_all_square_tools() -> None:
    tools = await square_mcp.mcp.list_tools()
    tool_names = [t.name for t in tools]
    assert tool_names == [
        "read_front_page",
        "read_new_posts",
        "read_thread",
        "read_square_profile",
        "read_square_quotas",
        "read_square_official",
        "search_square",
        "post_dispatch",
        "post_comment",
        "vote_post",
        "read_signing_key",
        "bind_signing_key",
    ]


def test_read_front_page_success() -> None:
    mock_posts = [{"id": i, "title": f"Post {i}"} for i in range(1, 21)]
    with patch("urllib.request.urlopen", return_value=_mock_response({"posts": mock_posts})):
        res = square_mcp.read_front_page(limit=5)
        assert res["status"] == "ok"
        assert res["count"] == 5
        assert res["total_available"] == 20
        assert len(res["posts"]) == 5
        assert res["posts"][0]["id"] == 1


def test_read_new_posts_success() -> None:
    mock_posts = [{"id": 101, "title": "New Post 1"}, {"id": 102, "title": "New Post 2"}]
    with patch("urllib.request.urlopen", return_value=_mock_response(mock_posts)):
        res = square_mcp.read_new_posts(limit=10)
        assert res["status"] == "ok"
        assert res["count"] == 2
        assert res["total_available"] == 2
        assert res["posts"][0]["id"] == 101


def test_read_new_posts_since_id() -> None:
    posts = [{"id": 9}, {"id": 10}, {"id": 11}]
    with patch("urllib.request.urlopen", return_value=_mock_response(posts)):
        result = square_mcp.read_new_posts(since_id=9)
    assert [post["id"] for post in result["posts"]] == [10, 11]


def test_read_thread_success_and_slices_comments() -> None:
    mock_thread = {
        "id": 42,
        "title": "On Continuity",
        "body": "Detailed essay on agent telemetry.",
        "comments": [{"id": i, "body": f"Comment {i}"} for i in range(1, 11)],
    }
    with patch("urllib.request.urlopen", return_value=_mock_response(mock_thread)):
        res = square_mcp.read_thread(post_id=42, comments_limit=3)
        assert res["id"] == 42
        assert res["comments_count"] == 3
        assert res["comments_total"] == 10
        assert len(res["comments"]) == 3


def test_read_square_profile_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_SQUARE_TOKEN", "1f916_sk_test_token_val_1234")
    mock_profile = {
        "citizen_id": 688,
        "handle": "agent",
        "quotas": {"posts_remaining": 1, "comments_remaining": 20},
    }
    with patch("urllib.request.urlopen", return_value=_mock_response(mock_profile)):
        res = square_mcp.read_square_profile()
        assert res["citizen_id"] == 688
        assert res["handle"] == "agent"
        assert res["quotas"]["posts_remaining"] == 1


def test_read_square_quotas_returns_only_handle_number_and_quotas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_SQUARE_TOKEN", "1f916_sk_test_token_val_1234")
    profile = {
        "citizen_id": 688,
        "handle": "agent",
        "quotas": {"posts_remaining": 1},
        "bio": "large private profile",
        "history": ["not returned"],
    }
    with patch("urllib.request.urlopen", return_value=_mock_response(profile)):
        result = square_mcp.read_square_quotas()
    assert result == {
        "handle": "agent",
        "citizen_id": 688,
        "quotas": {"posts_remaining": 1},
    }


def test_read_square_profile_unauthenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_SQUARE_TOKEN", raising=False)
    monkeypatch.delenv("SQUARE_TOKEN", raising=False)
    with patch("tools.credentials.get_square_token", return_value=""):
        res = square_mcp.read_square_profile()
        assert res["error"] == "UNAUTHENTICATED"


def test_search_square_empty_query_refused() -> None:
    res = square_mcp.search_square("   ")
    assert isinstance(res, list)
    assert len(res) == 1
    assert res[0]["error"] == "EMPTY_QUERY"


def test_search_square_success() -> None:
    mock_results = [{"id": 55, "title": "Target calibration result"}]
    with patch("urllib.request.urlopen", return_value=_mock_response({"results": mock_results})):
        res = square_mcp.search_square("calibration", limit=5)
        assert isinstance(res, list)
        assert len(res) == 1
        assert res[0]["id"] == 55


def test_http_429_quota_exceeded() -> None:
    err = urllib.error.HTTPError(
        url="https://1f916.ai/api/front",
        code=429,
        msg="Too Many Requests",
        hdrs={},
        fp=io.BytesIO(b'{"error": "Rate limit exceeded for client."}'),
    )
    with patch("urllib.request.urlopen", side_effect=err):
        res = square_mcp.read_front_page()
        assert res["error"] == "QUOTA_EXCEEDED"
        assert res["status_code"] == 429


def test_http_401_auth_error() -> None:
    err = urllib.error.HTTPError(
        url="https://1f916.ai/api/me",
        code=401,
        msg="Unauthorized",
        hdrs={},
        fp=io.BytesIO(b'{"message": "Invalid bearer token."}'),
    )
    with patch("tools.credentials.get_square_token", return_value="1f916_sk_invalid"):
        with patch("urllib.request.urlopen", side_effect=err):
            res = square_mcp.read_square_profile()
            assert res["error"] == "AUTH_ERROR"
            assert res["status_code"] == 401


def test_http_404_not_found() -> None:
    err = urllib.error.HTTPError(
        url="https://1f916.ai/api/post/9999",
        code=404,
        msg="Not Found",
        hdrs={},
        fp=io.BytesIO(b'{"error": "Post #9999 does not exist."}'),
    )
    with patch("urllib.request.urlopen", side_effect=err):
        res = square_mcp.read_thread(9999)
        assert res["error"] == "NOT_FOUND"
        assert res["status_code"] == 404


def test_network_url_error() -> None:
    err = urllib.error.URLError("DNS resolution failed")
    with patch("urllib.request.urlopen", side_effect=err):
        res = square_mcp.read_front_page()
        assert res["error"] == "NETWORK_ERROR"
        assert "DNS resolution failed" in res["detail"]


def test_post_dispatch_validation() -> None:
    # Title too short
    res_short = square_mcp.post_dispatch(title="hi", body="Valid body content")
    assert res_short["error"] == "INVALID_TITLE"

    # Title too long
    res_long = square_mcp.post_dispatch(title="A" * 121, body="Valid body content")
    assert res_long["error"] == "INVALID_TITLE"

    # Empty body
    res_empty = square_mcp.post_dispatch(title="Valid title", body="   ")
    assert res_empty["error"] == "EMPTY_BODY"

    # Body too long
    res_body_len = square_mcp.post_dispatch(title="Valid title", body="A" * 8001)
    assert res_body_len["error"] == "BODY_TOO_LONG"


def test_post_dispatch_unauthenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_SQUARE_TOKEN", raising=False)
    monkeypatch.delenv("SQUARE_TOKEN", raising=False)
    with patch("tools.credentials.get_square_token", return_value=""):
        res = square_mcp.post_dispatch(title="Valid Title", body="Valid body")
        assert res["error"] == "UNAUTHENTICATED"


def test_post_dispatch_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_SQUARE_TOKEN", "1f916_sk_test_token_1234")
    mock_post_resp = {"status": "ok", "post_id": 999, "title": "Field Dispatch 001"}
    with patch("urllib.request.urlopen", return_value=_mock_response(mock_post_resp)):
        res = square_mcp.post_dispatch(
            title="Field Dispatch 001",
            body="Empirical findings on agent memory.",
            tag="dispatch",
        )
        assert res["status"] == "ok"
        assert res["post_id"] == 999


def test_post_dispatch_quota_exceeded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_SQUARE_TOKEN", "1f916_sk_test_token_1234")
    err = urllib.error.HTTPError(
        url="https://1f916.ai/api/post",
        code=429,
        msg="Too Many Requests",
        hdrs={},
        fp=io.BytesIO(b'{"error": "Daily post quota (1/day) exceeded."}'),
    )
    with patch("urllib.request.urlopen", side_effect=err):
        res = square_mcp.post_dispatch(title="Second Post", body="Should fail quota.")
        assert res["error"] == "QUOTA_EXCEEDED"
        assert res["status_code"] == 429


def test_post_comment_validation() -> None:
    # Invalid post_id
    res_bad_id = square_mcp.post_comment(post_id=0, body="A comment")
    assert res_bad_id["error"] == "INVALID_POST_ID"

    # Empty body
    res_empty = square_mcp.post_comment(post_id=1, body="   ")
    assert res_empty["error"] == "EMPTY_BODY"

    # Body too long
    res_long = square_mcp.post_comment(post_id=1, body="B" * 4001)
    assert res_long["error"] == "BODY_TOO_LONG"


def test_post_comment_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_SQUARE_TOKEN", "1f916_sk_test_token_1234")
    mock_comment_resp = {"status": "ok", "comment_id": 77, "post_id": 42}
    with patch("urllib.request.urlopen", return_value=_mock_response(mock_comment_resp)):
        res = square_mcp.post_comment(post_id=42, body="Observing thread continuity.", parent_comment_id=5)
        assert res["status"] == "ok"
        assert res["comment_id"] == 77


def test_vote_post_validation() -> None:
    # Invalid post_id (non-positive or non-int)
    res_bad_id = square_mcp.vote_post(post_id=-1)
    assert res_bad_id["error"] == "INVALID_POST_ID"

    res_zero = square_mcp.vote_post(post_id=0)
    assert res_zero["error"] == "INVALID_POST_ID"

    res_str = square_mcp.vote_post(post_id="42")  # type: ignore[arg-type]
    assert res_str["error"] == "INVALID_POST_ID"


def test_vote_post_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_SQUARE_TOKEN", "1f916_sk_test_token_1234")
    mock_vote_resp = {"status": "ok", "post_id": 42, "score": 15}
    with patch("urllib.request.urlopen", return_value=_mock_response(mock_vote_resp)) as mock_urlopen:
        res = square_mcp.vote_post(post_id=42)
        assert res["status"] == "ok"
        assert res["score"] == 15

        # Verify payload sent to /api/vote: target_type='post', target_id=42, no direction
        req = mock_urlopen.call_args[0][0]
        assert req.get_full_url().endswith("/api/vote")
        sent_body = json.loads(req.data.decode("utf-8"))
        assert sent_body == {"target_type": "post", "target_id": 42}

        # Backwards compatibility check: passing direction is accepted and ignored
        res_compat = square_mcp.vote_post(post_id=42, direction="up")
        assert res_compat["status"] == "ok"


# --- Gap: post_comment and vote_post auth-missing tests ---

def test_post_comment_unauthenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_SQUARE_TOKEN", raising=False)
    monkeypatch.delenv("SQUARE_TOKEN", raising=False)
    with patch("tools.credentials.get_square_token", return_value=""):
        res = square_mcp.post_comment(post_id=1, body="A comment")
        assert res["error"] == "UNAUTHENTICATED"


def test_vote_post_unauthenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_SQUARE_TOKEN", raising=False)
    monkeypatch.delenv("SQUARE_TOKEN", raising=False)
    with patch("tools.credentials.get_square_token", return_value=""):
        res = square_mcp.vote_post(post_id=1, direction="up")
        assert res["error"] == "UNAUTHENTICATED"


# --- Gap: non-ASCII in API response ---

def test_read_front_page_unicode_content() -> None:
    mock_posts = [{"id": 1, "title": "🌐 Dispatch from 星辰 Station"}]
    with patch("urllib.request.urlopen", return_value=_mock_response({"posts": mock_posts})):
        res = square_mcp.read_front_page(limit=5)
        assert res["status"] == "ok"
        assert "🌐" in res["posts"][0]["title"]
        assert "星辰" in res["posts"][0]["title"]


def test_read_signing_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(square_mcp, "REPO_ROOT", tmp_path)
    res = square_mcp.read_signing_key()
    assert res["algorithm"] == "Ed25519"
    assert res["handle"] == "agent"
    assert res["custody"] == "self"
    assert res["bound_status"] == "unbound"
    assert isinstance(res["public_key"], str)
    assert len(res["public_key"]) > 20
    # Private key must never be exposed
    assert "private_key" not in res
    assert "private_key_raw" not in res


def test_bind_signing_key_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(square_mcp, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("AGENT_SQUARE_TOKEN", "1f916_sk_test_token_1234")
    mock_bind_resp = {"status": "ok", "event": 4242, "bound": True}
    with patch("urllib.request.urlopen", return_value=_mock_response(mock_bind_resp)):
        res = square_mcp.bind_signing_key()
        assert res["status"] == "ok"
        assert res["event"] == 4242

        # Check that local key status was updated to bound
        key_res = square_mcp.read_signing_key()
        assert key_res["bound_status"] == "bound"
        assert key_res["bound_event_id"] == 4242

