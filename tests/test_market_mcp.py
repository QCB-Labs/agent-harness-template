"""Tests for The Market (1F3EA.com) MCP connector (tools/market_mcp.py)."""

from __future__ import annotations

import hashlib
import io
import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools import market_mcp, names, phase

CONVERSATION_ID = "11111111-1111-4111-8111-111111111111"


@pytest.fixture(autouse=True)
def _fake_market_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Authenticated tools need a token; tests never use a real one."""
    monkeypatch.setenv("AGENT_MARKET_TOKEN", "1f3ea_sk_test_token_fixture")


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
        "read_market_official",
        "acquire_free_artifact",
        "list_acquired",
        "read_acquired",
        "purchase_listing",
        "pay_listing_fee",
        "create_world_draft",
        "cancel_world_draft",
        "activate_world_listing",
        "create_listing",
        "write_store_line",
        "withdraw_listing",
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


def test_browse_shelves_since_id_and_brief() -> None:
    listings = [
        {"id": 4, "description": "old"},
        {"id": 5, "description": "x" * 250},
        {"id": 6, "description": "short"},
    ]
    with patch("urllib.request.urlopen", return_value=_mock_response({"listings": listings})):
        result = market_mcp.browse_shelves(since_id=4, brief=True)
    assert [item["id"] for item in result["listings"]] == [5, 6]
    assert result["listings"][0]["description"] == "x" * 200
    assert result["listings"][0]["truncated"] is True
    assert "truncated" not in result["listings"][1]


def test_browse_world_aisle() -> None:
    mock_world_items = [{"id": 501, "title": "Plaza Lantern", "aisle": "world"}]
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


def test_purchase_listing_refuses_paid_listings_without_touching_the_wallet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import wallet

    touched: list[str] = []
    for name in ("money_actions_allowed", "pay_armed", "wallet_session_ok", "transfer_token"):
        monkeypatch.setattr(wallet, name, lambda *a, _n=name, **k: touched.append(_n))
    mock_paid_listing = {"id": 50, "title": "Paid Tool", "price_usdc": 1.0}
    with patch("urllib.request.urlopen", return_value=_mock_response(mock_paid_listing)):
        res = market_mcp.purchase_listing(50, "Purchase the paid tool")

    assert res["status"] == "REFUSED"
    assert res["error"] == "BUYING_NOT_SUPPORTED"
    assert "Nothing was paid" in res["message"]
    assert touched == []


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


# --- Seller Tools Tests ---

def test_create_world_draft_success() -> None:
    mock_draft = {
        "status": "pending",
        "draft_id": 42,
        "expires_at": "2026-09-14T21:00:00Z",
    }
    with patch("urllib.request.urlopen", return_value=_mock_response(mock_draft)) as mock_open:
        res = market_mcp.create_world_draft(
            thing_id=101,
            title="Field Lantern",
            description="A lantern for field work.",
            preview="Lights the way.",
            price_usdc=1.0,
            tags=["lantern", "field", "tool"],
            seller_wallet="0x1111111111111111111111111111111111111111",
        )
        assert res["status"] == "pending"
        assert res["draft_id"] == 42
        req = mock_open.call_args[0][0]
        sent = json.loads(req.data.decode("utf-8"))
        assert sent["thing_id"] == 101
        assert sent["price_usdc"] == 1.0
        assert sent["seller_wallet"] == "0x1111111111111111111111111111111111111111"


def test_create_world_draft_validation() -> None:
    assert market_mcp.create_world_draft(0, "Title", "Desc", "Prev", 1.0)["error"] == "INVALID_THING_ID"
    assert market_mcp.create_world_draft(1, "ab", "Desc", "Prev", 1.0)["error"] == "INVALID_TITLE"
    assert market_mcp.create_world_draft(1, "Title", "", "Prev", 1.0)["error"] == "INVALID_DESCRIPTION"
    assert market_mcp.create_world_draft(1, "Title", "Desc", "Prev", 0)["error"] == "INVALID_PRICE"
    assert market_mcp.create_world_draft(1, "Title", "Desc", "Prev", 1.0, seller_wallet="bad")["error"] == "INVALID_SELLER_WALLET"


def test_cancel_world_draft() -> None:
    assert market_mcp.cancel_world_draft(0)["error"] == "INVALID_DRAFT_ID"
    with patch("urllib.request.urlopen", return_value=_mock_response({"status": "canceled", "draft_id": 42})):
        res = market_mcp.cancel_world_draft(42)
        assert res["status"] == "canceled"
        assert res["draft_id"] == 42


def test_activate_world_listing() -> None:
    assert market_mcp.activate_world_listing(0, 10)["error"] == "INVALID_DRAFT_ID"
    assert market_mcp.activate_world_listing(10, 0)["error"] == "INVALID_OFFER_ID"
    assert market_mcp.activate_world_listing(10, 10, fee_tx_hash="not_a_hash")["error"] == "INVALID_TX_HASH"

    valid_hash = "0x" + "a" * 64
    mock_res = {"status": "live", "listing_id": 100}
    with patch("urllib.request.urlopen", return_value=_mock_response(mock_res)) as mock_open:
        res = market_mcp.activate_world_listing(42, 105, fee_tx_hash=valid_hash)
        assert res["status"] == "live"
        assert res["listing_id"] == 100
        req = mock_open.call_args[0][0]
        sent = json.loads(req.data.decode("utf-8"))
        assert sent["draft_id"] == 42
        assert sent["city_offer_id"] == 105
        assert sent["fee_tx_hash"] == valid_hash


def test_create_listing_success() -> None:
    mock_res = {"status": "live", "listing_id": 88}
    valid_wallet = "0x1111111111111111111111111111111111111111"
    with patch("urllib.request.urlopen", return_value=_mock_response(mock_res)) as mock_open:
        res = market_mcp.create_listing(
            title="10-Continent Wayfinder's Atlas",
            description="Complete topology of 1F3D9.",
            preview="A guide to the outer continents.",
            artifact="# The Full Atlas...",
            price_usdc=1.0,
            tags=["navigation", "atlas"],
            aisle="knowledge",
            seller_wallet=valid_wallet,
        )
        assert res["status"] == "live"
        assert res["listing_id"] == 88
        req = mock_open.call_args[0][0]
        sent = json.loads(req.data.decode("utf-8"))
        assert sent["title"] == "10-Continent Wayfinder's Atlas"
        assert sent["price_usdc"] == 1.0
        assert sent["aisle"] == "knowledge"


def test_create_listing_validation() -> None:
    valid_wallet = "0x1111111111111111111111111111111111111111"
    assert market_mcp.create_listing("no", "desc", "prev", "art", 1.0, seller_wallet=valid_wallet)["error"] == "INVALID_TITLE"
    assert market_mcp.create_listing("Title", "", "prev", "art", 1.0, seller_wallet=valid_wallet)["error"] == "INVALID_DESCRIPTION"
    assert market_mcp.create_listing("Title", "desc", "prev", "", 1.0, seller_wallet=valid_wallet)["error"] == "INVALID_ARTIFACT"
    assert market_mcp.create_listing("Title", "desc", "prev", "art", -1.0, seller_wallet=valid_wallet)["error"] == "INVALID_PRICE"


def test_write_store_line() -> None:
    assert market_mcp.write_store_line("a" * 161)["error"] == "LINE_TOO_LONG"
    with patch("urllib.request.urlopen", return_value=_mock_response({"status": "ok"})) as mock_open:
        res = market_mcp.write_store_line("Field guides and small tools.")
        assert res["status"] == "ok"
        req = mock_open.call_args[0][0]
        sent = json.loads(req.data.decode("utf-8"))
        assert "Field guides" in sent["line"]


def test_withdraw_listing() -> None:
    assert market_mcp.withdraw_listing(0)["error"] == "INVALID_LISTING_ID"
    with patch("urllib.request.urlopen", return_value=_mock_response({"status": "withdrawn", "listing_id": 42})):
        res = market_mcp.withdraw_listing(42)
        assert res["status"] == "withdrawn"
        assert res["listing_id"] == 42


# --- pay_listing_fee: never touches a real wallet --------------------------

TREASURY = "0x" + "a" * 40
USDC = "0x" + "b" * 40
PAYER = "0x" + "c" * 40
TX_HASH = "0x" + "d" * 64
OFFICIAL = {"treasury": TREASURY, "usdc_contract": USDC, "network": "base", "listing_fee_usdc": 1}
APPROVED_WALLET = {
    "enabled": True, "mode": "autonomous-approved", "caps_verified": True,
    "public_address": PAYER, "site_scope": ["1F3EA"], "caps_usdc": {"per_tx": 2},
    "pay_armed": True,
}


class FakeCircle:
    """Stands in for subprocess.run; records every command it is given."""

    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **_kwargs):
        import subprocess

        self.calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, self.returncode, stdout=self.stdout, stderr="")


@pytest.fixture
def fee_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from tools import authority, wallet

    state = {"wallet": dict(APPROVED_WALLET), "session": True, "circle_login": True}
    monkeypatch.setattr(market_mcp, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(wallet, "load_wallet_config", lambda: state["wallet"])
    monkeypatch.setattr(authority, "session_active", lambda touch=True: state["session"])
    monkeypatch.setattr(wallet, "wallet_session_ok", lambda: state["circle_login"])
    monkeypatch.setattr(wallet, "circle_command", lambda: "circle")
    monkeypatch.setattr(market_mcp, "_read_market_official", lambda: dict(OFFICIAL))
    fake = FakeCircle(stdout=json.dumps({"data": {"id": "t1", "state": "COMPLETE", "txHash": TX_HASH}}))
    monkeypatch.setattr(wallet.subprocess, "run", fake)
    state["circle"] = fake
    state["ledger"] = tmp_path / names.memory("private", "market_fee_ledger.json")
    return state


def test_pay_listing_fee_pays_usdc_to_the_live_treasury(fee_env) -> None:
    res = market_mcp.pay_listing_fee("Field guide listing")

    assert res["status"] == "PAID"
    assert res["fee_tx_hash"] == TX_HASH
    (cmd,) = fee_env["circle"].calls
    assert cmd[:4] == ["circle", "wallet", "transfer", TREASURY]
    assert cmd[cmd.index("--token") + 1] == USDC, "must send USDC, never the native token"
    assert cmd[cmd.index("--address") + 1] == PAYER
    assert cmd[cmd.index("--amount") + 1] == "1.0"
    assert "--idempotency-key" in cmd
    ledger = json.loads(fee_env["ledger"].read_text(encoding="utf-8"))
    assert ledger[0]["tx_hash"] == TX_HASH and ledger[0]["used"] is False


@pytest.mark.parametrize(
    ("change", "error"),
    [
        (lambda s: s["wallet"].update(pay_armed=False), "DISARMED"),
        (lambda s: s.update(session=False), "NO_SESSION"),
        (lambda s: s["wallet"].update(site_scope=["1F3D9"]), "WALLET_UNAPPROVED"),
        (lambda s: s["wallet"].update(caps_usdc={"per_tx": 0.5}), "OVER_CAP"),
        (lambda s: s.update(circle_login=False), "CIRCLE_LOGGED_OUT"),
    ],
)
def test_pay_listing_fee_refuses_without_every_approval(fee_env, change, error) -> None:
    change(fee_env)

    res = market_mcp.pay_listing_fee("Field guide listing")

    assert res["status"] == "REFUSED"
    assert res["error"] == error
    assert fee_env["circle"].calls == [], "nothing may be sent when a check fails"


def test_pay_listing_fee_refuses_odd_official_facts(fee_env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(market_mcp, "_read_market_official", lambda: {**OFFICIAL, "network": "ethereum"})

    res = market_mcp.pay_listing_fee("Field guide listing")

    assert res["error"] == "OFFICIAL_UNEXPECTED"
    assert fee_env["circle"].calls == []


def test_pay_listing_fee_never_pays_twice_within_the_hour(fee_env) -> None:
    first = market_mcp.pay_listing_fee("Field guide listing")
    second = market_mcp.pay_listing_fee("Field guide listing")

    assert first["status"] == "PAID"
    assert second["status"] == "ALREADY_PAID"
    assert second["fee_tx_hash"] == TX_HASH
    assert len(fee_env["circle"].calls) == 1


def test_pay_listing_fee_retries_an_unfinished_payment_with_the_same_key(fee_env) -> None:
    fee_env["circle"].stdout = json.dumps({"data": {"id": "t1", "state": "INITIATED"}})
    pending = market_mcp.pay_listing_fee("Field guide listing")
    fee_env["circle"].stdout = json.dumps({"data": {"id": "t1", "state": "COMPLETE", "txHash": TX_HASH}})
    paid = market_mcp.pay_listing_fee("Field guide listing")

    assert pending["status"] == "PENDING"
    assert paid["status"] == "PAID"
    first, second = fee_env["circle"].calls
    key = first[first.index("--idempotency-key") + 1]
    assert second[second.index("--idempotency-key") + 1] == key


def test_pay_listing_fee_reports_a_failed_transfer_as_uncertain(fee_env) -> None:
    fee_env["circle"].stdout = ""
    fee_env["circle"].returncode = 1

    res = market_mcp.pay_listing_fee("Field guide listing")

    assert res["status"] == "UNCERTAIN"
    assert "Do NOT pay" in res["message"]


def test_a_successful_listing_marks_the_fee_used(fee_env, monkeypatch: pytest.MonkeyPatch) -> None:
    market_mcp.pay_listing_fee("Field guide listing")
    monkeypatch.setenv("AGENT_MARKET_TOKEN", "1f3ea_sk_test_token_1234")
    with patch("urllib.request.urlopen", return_value=_mock_response({"id": 7, "status": "active"})):
        market_mcp.create_listing(
            title="Field guide", description="d", preview="p", artifact="a",
            price_usdc=1.0, seller_wallet=PAYER, fee_tx_hash=TX_HASH,
        )

    ledger = json.loads(fee_env["ledger"].read_text(encoding="utf-8"))
    assert ledger[0]["used"] is True
    fee_env["circle"].stdout = json.dumps({"data": {"id": "t2", "state": "COMPLETE", "txHash": "0x" + "e" * 64}})
    again = market_mcp.pay_listing_fee("Next listing")
    assert again["status"] == "PAID", "a used fee must not block the next listing"
