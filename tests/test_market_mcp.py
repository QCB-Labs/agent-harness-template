"""Tests for The Market (1F3EA.com) MCP connector (tools/market_mcp.py)."""

from __future__ import annotations

import hashlib
import io
import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools import names
from tools import market_mcp, phase

CONVERSATION_ID = "11111111-1111-4111-8111-111111111111"


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
async def test_server_exposes_market_tools() -> None:
    tools = await market_mcp.mcp.list_tools()
    tool_names = [t.name for t in tools]
    assert tool_names == [
        "browse_shelves",
        "browse_world_aisle",
        "inspect_listing",
        "read_merchant_profile",
        "acquire_free_artifact",
        "list_acquired",
        "read_acquired",
        "purchase_listing",
    ]
    by_name = {tool.name: tool for tool in tools}
    for name in ("list_acquired", "read_acquired"):
        assert "_conversationId" in by_name[name].inputSchema["properties"]


def test_browse_shelves_success_and_slices_limit() -> None:
    mock_listings = [{"id": i, "title": f"Artifact {i}", "price_usdc": 0} for i in range(1, 21)]
    with patch("urllib.request.urlopen", return_value=_mock_response({"listings": mock_listings})):
        res = market_mcp.browse_shelves(limit=5)
        assert res["status"] == "ok"
        assert res["count"] == 5
        assert res["total_available"] == 20
        assert len(res["listings"]) == 5
        assert res["listings"][0]["id"] == 1


def test_browse_shelves_with_aisle_filter() -> None:
    mock_tools = [{"id": 10, "title": "Acoustic Calibrator", "aisle": "tools"}]
    with patch("urllib.request.urlopen", return_value=_mock_response({"listings": mock_tools})) as mock_open:
        res = market_mcp.browse_shelves(aisle="tools", sort="popular", limit=10)
        assert res["status"] == "ok"
        assert res["aisle"] == "tools"
        assert res["sort"] == "popular"
        req = mock_open.call_args[0][0]
        assert "aisle=tools" in req.full_url
        assert "sort=popular" in req.full_url


def test_browse_world_aisle() -> None:
    mock_world_items = [{"id": 501, "title": "Place 3 Beacon", "aisle": "world"}]
    with patch("urllib.request.urlopen", return_value=_mock_response({"listings": mock_world_items})) as mock_open:
        res = market_mcp.browse_world_aisle(limit=5)
        assert res["status"] == "ok"
        assert res["aisle"] == "world"
        req = mock_open.call_args[0][0]
        assert "aisle=world" in req.full_url


def test_inspect_listing_success() -> None:
    mock_item = {
        "id": 15,
        "title": "The Auditor's Handbook",
        "price_usdc": 0,
        "seller": "elder",
        "description": "Comprehensive guide to city records.",
    }
    with patch("urllib.request.urlopen", return_value=_mock_response(mock_item)):
        res = market_mcp.inspect_listing(15)
        assert res["id"] == 15
        assert res["title"] == "The Auditor's Handbook"
        assert res["price_usdc"] == 0


def test_inspect_listing_invalid_id() -> None:
    res = market_mcp.inspect_listing(0)
    assert res["error"] == "INVALID_LISTING_ID"

    res_neg = market_mcp.inspect_listing(-5)
    assert res_neg["error"] == "INVALID_LISTING_ID"


def test_read_merchant_profile_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_MARKET_TOKEN", "1f3ea_sk_test_token_5678")
    mock_profile = {
        "merchant_id": 15,
        "handle": "agent",
        "active_listings": 2,
        "balance_usdc": 0.0,
    }
    with patch("urllib.request.urlopen", return_value=_mock_response(mock_profile)):
        res = market_mcp.read_merchant_profile()
        assert res["merchant_id"] == 15
        assert res["handle"] == "agent"
        assert res["active_listings"] == 2


def test_read_merchant_profile_unauthenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_MARKET_TOKEN", raising=False)
    monkeypatch.delenv("MARKET_TOKEN", raising=False)
    with patch("tools.credentials.get_market_token", return_value=""):
        res = market_mcp.read_merchant_profile()
        assert res["error"] == "UNAUTHENTICATED"


def test_market_http_404_not_found() -> None:
    err = urllib.error.HTTPError(
        url="https://1f3ea.com/api/listing/9999",
        code=404,
        msg="Not Found",
        hdrs={},
        fp=io.BytesIO(b'{"error": "Listing not found."}'),
    )
    with patch("urllib.request.urlopen", side_effect=err):
        res = market_mcp.inspect_listing(9999)
        assert res["error"] == "NOT_FOUND"
        assert res["status_code"] == 404


def test_market_http_429_quota_exceeded() -> None:
    err = urllib.error.HTTPError(
        url="https://1f3ea.com/api/shelves",
        code=429,
        msg="Too Many Requests",
        hdrs={},
        fp=io.BytesIO(b'{"error": "Too many requests to market."}'),
    )
    with patch("urllib.request.urlopen", side_effect=err):
        res = market_mcp.browse_shelves()
        assert res["error"] == "QUOTA_EXCEEDED"
        assert res["status_code"] == 429


def test_market_network_error() -> None:
    err = urllib.error.URLError("Connection refused")
    with patch("urllib.request.urlopen", side_effect=err):
        res = market_mcp.browse_shelves()
        assert res["error"] == "NETWORK_ERROR"
        assert "Connection refused" in res["detail"]


def test_acquire_free_artifact_invalid_id() -> None:
    assert market_mcp.acquire_free_artifact(0, "testing")["error"] == "INVALID_LISTING_ID"
    assert market_mcp.acquire_free_artifact(-1, "testing")["error"] == "INVALID_LISTING_ID"


@pytest.mark.parametrize("purpose", ["", "   ", "two\nlines", "two\rlines", "x" * 201])
def test_acquire_free_artifact_rejects_invalid_purpose(purpose: str) -> None:
    with patch("urllib.request.urlopen") as mock_open:
        res = market_mcp.acquire_free_artifact(15, purpose)
    assert res["error"] == "INVALID_PURPOSE"
    mock_open.assert_not_called()


def test_acquire_free_artifact_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(market_mcp, "REPO_ROOT", tmp_path)

    mock_listing = {
        "id": 15,
        "title": "The Auditor's Handbook",
        "seller": "elder",
        "aisle": "knowledge",
        "price_usdc": 0,
        "content": "# The Auditor's Handbook\nComprehensive instructions.",
    }
    with patch("urllib.request.urlopen", return_value=_mock_response(mock_listing)):
        res = market_mcp.acquire_free_artifact(15, "Evaluate the guide for city work")
        assert res["status"] == "ACQUIRED"
        assert res["listing_id"] == 15
        assert res["title"] == "The Auditor's Handbook"

        expected_file = tmp_path / names.memory("acquired") / "15-the-auditor-s-handbook.md"
        assert expected_file.exists()
        content = expected_file.read_text(encoding="utf-8")
        assert "# The Auditor's Handbook" in content
        assert "- **Price:** 0 USDC (Free)" in content
        assert "Comprehensive instructions." in content

        index = json.loads(
            (tmp_path / names.memory("acquired-index.json")).read_text(encoding="utf-8")
        )
        assert index["artifacts"] == [{
            "listing_id": 15,
            "file": f"{names.dir_name('memory')}/acquired/15-the-auditor-s-handbook.md",
            "title": "The Auditor's Handbook",
            "seller": "elder",
            "aisle": "knowledge",
            "acquired_utc": index["artifacts"][0]["acquired_utc"],
            "purpose": "Evaluate the guide for city work",
            "status": "quarantined",
            "sha256": index["artifacts"][0]["sha256"],
        }]
        assert index["artifacts"][0]["sha256"] == hashlib.sha256(
            expected_file.read_bytes()
        ).hexdigest()
        assert "Comprehensive instructions." not in json.dumps(index)


def test_acquire_free_artifact_refused_on_paid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(market_mcp, "REPO_ROOT", tmp_path)

    mock_paid_listing = {
        "id": 88,
        "title": "Paid Tool Kit",
        "seller": "merchant_bob",
        "price_usdc": 2.5,
        "content": "Expensive proprietary tool.",
    }
    with patch("urllib.request.urlopen", return_value=_mock_response(mock_paid_listing)):
        res = market_mcp.acquire_free_artifact(88, "Evaluate this tool")
        assert res["status"] == "REFUSED"
        assert res["error"] == "PAID_LISTING"
        assert "costs 2.5 USDC" in res["message"]

        expected_file = tmp_path / names.memory("acquired") / "88-paid-tool-kit.md"
        assert not expected_file.exists()


def test_purchase_listing_free_routes_to_acquire(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(market_mcp, "REPO_ROOT", tmp_path)

    mock_listing = {
        "id": 12,
        "title": "Free Skill Card",
        "price_usdc": 0,
        "content": "Free card content",
    }
    with patch("urllib.request.urlopen", return_value=_mock_response(mock_listing)):
        res = market_mcp.purchase_listing(12, "Evaluate the skill card")
        assert res["status"] == "ACQUIRED"
        assert res["listing_id"] == 12
        index = json.loads(
            (tmp_path / names.memory("acquired-index.json")).read_text(encoding="utf-8")
        )
        assert index["artifacts"][0]["purpose"] == "Evaluate the skill card"


def test_purchase_listing_refused_when_wallet_unapproved(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_paid_listing = {
        "id": 50,
        "title": "Paid Tool",
        "price_usdc": 1.0,
    }
    with patch("urllib.request.urlopen", return_value=_mock_response(mock_paid_listing)):
        with patch("tools.wallet.money_actions_allowed", return_value=False):
            res = market_mcp.purchase_listing(50, "Purchase the paid tool")
            assert res["status"] == "REFUSED"
            assert res["error"] == "WALLET_UNAPPROVED"
            assert "1F3EA" in res["message"]


def test_purchase_listing_held_when_disarmed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(market_mcp, "REPO_ROOT", tmp_path)

    mock_paid_listing = {
        "id": 50,
        "title": "Paid Tool",
        "price_usdc": 1.0,
        "seller": "merchant_alice",
        "aisle": "tools",
    }
    with patch("urllib.request.urlopen", return_value=_mock_response(mock_paid_listing)):
        with patch("tools.wallet.money_actions_allowed", return_value=True):
            with patch("tools.wallet.pay_armed", return_value=False):
                res = market_mcp.purchase_listing(50, "Purchase the paid tool")
                assert res["status"] == "HELD"
                assert res["error"] == "DISARMED"
                assert "pay_armed" in res["message"]

                assert "merchant_alice" in res["message"]
                assert not list(tmp_path.rglob("*.md"))


def test_purchase_listing_refused_when_circle_session_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_paid_listing = {
        "id": 50,
        "title": "Paid Tool",
        "price_usdc": 1.0,
    }
    with patch("urllib.request.urlopen", return_value=_mock_response(mock_paid_listing)):
        with patch("tools.wallet.money_actions_allowed", return_value=True):
            with patch("tools.wallet.pay_armed", return_value=True):
                with patch("tools.wallet.wallet_session_ok", return_value=False):
                    res = market_mcp.purchase_listing(50, "Purchase the paid tool")
                    assert res["status"] == "REFUSED"
                    assert res["error"] == "SESSION_EXPIRED"


def test_purchase_listing_success(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_paid_listing = {
        "id": 50,
        "title": "Paid Tool",
        "price_usdc": 1.0,
        "seller_address": "0x1234567890123456789012345678901234567890",
    }
    mock_payment_res = {
        "txHash": "0xabcdef123456",
        "status": "CONFIRMED",
        "amount_usdc": 1.0,
        "recipient": "0x1234567890123456789012345678901234567890",
    }
    with patch("urllib.request.urlopen", return_value=_mock_response(mock_paid_listing)):
        with patch("tools.wallet.money_actions_allowed", return_value=True):
            with patch("tools.wallet.pay_armed", return_value=True):
                with patch("tools.wallet.wallet_session_ok", return_value=True):
                    with patch("tools.wallet.pay", return_value=mock_payment_res) as mock_pay:
                        res = market_mcp.purchase_listing(50, "Purchase the paid tool")
                        assert res["status"] == "PURCHASED"
                        assert res["payment"]["txHash"] == "0xabcdef123456"
                        mock_pay.assert_called_once_with(
                            recipient="0x1234567890123456789012345678901234567890",
                            amount_usdc=1.0,
                            purpose="1F3EA Listing #50",
                        )


# --- Gap: path traversal on acquire_free_artifact ---

def test_acquire_free_artifact_path_traversal_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(market_mcp, "REPO_ROOT", tmp_path)
    mock_listing = {
        "id": 99,
        "title": "../../../etc/passwd",
        "price_usdc": 0,
        "content": "malicious content",
    }
    with patch("urllib.request.urlopen", return_value=_mock_response(mock_listing)):
        res = market_mcp.acquire_free_artifact(99, "Test safe storage")
        assert res.get("status") == "ACQUIRED" or res.get("error") == "INVALID_FILENAME"
        etc_passwd = tmp_path.parent.parent.parent / "etc" / "passwd"
        assert not etc_passwd.exists()
        acquired_dir = tmp_path / names.memory("acquired")
        if acquired_dir.exists():
            for f in acquired_dir.iterdir():
                assert ".." not in f.name


# --- Gap: acquire network error on download ---

def test_acquire_free_artifact_network_error_on_inspect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(market_mcp, "REPO_ROOT", tmp_path)
    err = urllib.error.URLError("Connection refused")
    with patch("urllib.request.urlopen", side_effect=err):
        res = market_mcp.acquire_free_artifact(15, "Test network failure")
        assert res.get("error") in ("NETWORK_ERROR", "FAILED") or res.get("status") == "FAILED"


# --- Gap: purchase_listing invalid id ---

def test_purchase_listing_invalid_id() -> None:
    res = market_mcp.purchase_listing(0, "testing")
    assert res["error"] == "INVALID_LISTING_ID"
    res_neg = market_mcp.purchase_listing(-5, "testing")
    assert res_neg["error"] == "INVALID_LISTING_ID"


# --- Gap: acquire with missing price_usdc field (ambiguous price fallback) ---

def test_acquire_free_artifact_missing_price_field(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(market_mcp, "REPO_ROOT", tmp_path)
    mock_listing = {
        "id": 77,
        "title": "No Price Field Item",
        "content": "Some content",
    }
    with patch("urllib.request.urlopen", return_value=_mock_response(mock_listing)):
        res = market_mcp.acquire_free_artifact(77, "Evaluate unpriced item")
        assert res["status"] == "ACQUIRED"


# --- Gap: non-ASCII in API response ---

def test_acquire_free_artifact_unicode_title(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(market_mcp, "REPO_ROOT", tmp_path)
    mock_listing = {
        "id": 200,
        "title": "🚀 Orbital Guide 星辰",
        "price_usdc": 0,
        "content": "Guide content with emoji 🛸 and CJK 中文",
    }
    with patch("urllib.request.urlopen", return_value=_mock_response(mock_listing)):
        res = market_mcp.acquire_free_artifact(200, "Evaluate translated guide")
        assert res["status"] == "ACQUIRED"
        acquired_dir = tmp_path / names.memory("acquired")
        files = list(acquired_dir.glob("200-*.md"))
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        assert "🛸" in content
        assert "中文" in content


def test_acquired_index_sanitizes_metadata_and_replaces_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(market_mcp, "REPO_ROOT", tmp_path)
    mock_listing = {
        "id": 21,
        "title": "A\n" + "T" * 250,
        "seller": "seller\r\nname",
        "aisle": "knowledge\nextra",
        "price_usdc": 0,
        "content": "PRIVATE RAW CONTENT",
    }
    with patch("urllib.request.urlopen", return_value=_mock_response(mock_listing)):
        market_mcp.acquire_free_artifact(21, "First evaluation")
        market_mcp.acquire_free_artifact(21, "Second evaluation")

    index = json.loads(
        (tmp_path / names.memory("acquired-index.json")).read_text(encoding="utf-8")
    )
    assert len(index["artifacts"]) == 1
    receipt = index["artifacts"][0]
    assert receipt["purpose"] == "Second evaluation"
    assert len(receipt["title"]) <= 200
    assert "\n" not in receipt["title"]
    assert "\n" not in receipt["seller"]
    assert "PRIVATE RAW CONTENT" not in json.dumps(index)


def test_list_and_read_acquired_are_private_and_selective(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(market_mcp, "REPO_ROOT", tmp_path)
    mock_listing = {
        "id": 31,
        "title": "Private Field Guide",
        "price_usdc": 0,
        "content": "RAW ARTIFACT BODY",
    }
    with patch("urllib.request.urlopen", return_value=_mock_response(mock_listing)):
        market_mcp.acquire_free_artifact(31, "Use for one city task")
    second_listing = {
        "id": 32,
        "title": "Different Field Guide",
        "price_usdc": 0,
        "content": "DIFFERENT RAW BODY",
    }
    with patch("urllib.request.urlopen", return_value=_mock_response(second_listing)):
        market_mcp.acquire_free_artifact(32, "Use for a different task")

    listed = market_mcp.list_acquired(conversation_id=CONVERSATION_ID)
    assert phase.current() == phase.PRIVATE
    assert listed["count"] == 2
    assert "RAW ARTIFACT BODY" not in json.dumps(listed)
    assert "DIFFERENT RAW BODY" not in json.dumps(listed)

    phase.reset_for_new_wake()
    assert phase.current() == phase.ORIENT
    read = market_mcp.read_acquired(31, conversation_id=CONVERSATION_ID)
    assert phase.current() == phase.PRIVATE
    assert read["status"] == "OK"
    assert "RAW ARTIFACT BODY" in read["content"]
    assert "DIFFERENT RAW BODY" not in read["content"]
    assert market_mcp.read_acquired(
        33, conversation_id=CONVERSATION_ID
    )["error"] == "ARTIFACT_NOT_INDEXED"


def test_read_acquired_rejects_index_path_outside_quarantine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(market_mcp, "REPO_ROOT", tmp_path)
    memory = tmp_path / names.dir_name("memory")
    memory.mkdir()
    (tmp_path / "outside.md").write_text("must not load", encoding="utf-8")
    (memory / "acquired-index.json").write_text(
        json.dumps({"artifacts": [{"listing_id": 9, "file": "outside.md"}]}),
        encoding="utf-8",
    )
    assert market_mcp.read_acquired(
        9, conversation_id=CONVERSATION_ID
    )["error"] == "INVALID_INDEX_PATH"


# --- Gap: malformed API response (unexpected shape) ---

def test_browse_shelves_malformed_response() -> None:
    with patch("urllib.request.urlopen", return_value=_mock_response({"unexpected": True})):
        res = market_mcp.browse_shelves()
        assert isinstance(res, dict)
        assert res.get("listings") is not None or "error" in res or res.get("count") == 0
