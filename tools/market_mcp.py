#!/usr/bin/env python3
"""Dedicated MCP server for The Bazaar / Marketplace (1F3EA.com).

Exposes shelf browsing, artifact inspection, and trading tools for the agent.
"""

import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import threading
from typing import Annotated
import urllib.error
import urllib.parse
import urllib.request
import uuid

# Shadowing guard: ensure stdlib secrets is never shadowed by tools/secrets.py
TOOLS_DIR = Path(__file__).resolve().parent
if sys.path and Path(sys.path[0]).resolve() == TOOLS_DIR:
    sys.path.pop(0)

# Ensure repo root is on sys.path so 'tools.credentials' resolves reliably
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

for _stream in (sys.stdout, sys.stderr):
    if _stream and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

from mcp.server.fastmcp import FastMCP
from pydantic import Field
from tools import credentials, names, profile
from tools.official_facts import official_view
from tools.outgoing_check import check_outgoing
from tools import stop_marker
from tools.phase import enters_private

mcp = FastMCP("agent-market")
HostConversationId = Annotated[str | None, Field(alias="_conversationId")]
DEFAULT_MARKET_URL = "https://1f3ea.com"
VALID_AISLES = {
    "skills",
    "prompts",
    "tools",
    "data",
    "knowledge",
    "services",
    "wanted",
    "world",
    "other",
}

_file_locks: dict[str, threading.Lock] = {}
_file_locks_guard = threading.Lock()


def _file_lock(name: str) -> threading.Lock:
    with _file_locks_guard:
        if name not in _file_locks:
            _file_locks[name] = threading.Lock()
        return _file_locks[name]


def _get_base_url() -> str:
    return (os.environ.get("AGENT_MARKET_URL") or DEFAULT_MARKET_URL).rstrip("/")


def _validate_purpose(purpose: str) -> str | None:
    if not isinstance(purpose, str):
        return None
    clean = purpose.strip()
    if not clean or "\n" in purpose or "\r" in purpose or len(clean) > 200:
        return None
    return clean


def _bounded_line(value: object, fallback: str, limit: int) -> str:
    clean = " ".join(str(value or fallback).split())
    return (clean or fallback)[:limit]


def _acquired_index_path() -> Path:
    return REPO_ROOT / names.memory("acquired-index.json")


def _load_acquired_index() -> dict:
    index_path = _acquired_index_path()
    if not index_path.exists():
        return {"artifacts": []}
    data = json.loads(index_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("artifacts"), list):
        raise ValueError("Acquired artifact index has an invalid structure.")
    return data


def _write_acquired_index(index: dict) -> None:
    index_path = _acquired_index_path()
    index_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = index_path.with_name(
        f".{index_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        temp_path.write_text(
            json.dumps(index, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temp_path, index_path)
    finally:
        temp_path.unlink(missing_ok=True)


def _outgoing_refusal(payload: dict[str, object], fields: tuple[str, ...]) -> dict | None:
    """Refuse public text that the outgoing check flags.

    What is posted here is public and cannot be taken back, so a leak (a
    private name, a local path, a secret, someone's email address) is stopped
    before it leaves. It reads nothing else into the words.
    """
    kinds: list[str] = []
    for field in fields:
        value = payload.get(field)
        texts = value if isinstance(value, list) else [value]
        for text in texts:
            if isinstance(text, str) and text:
                kinds.extend(finding.kind for finding in check_outgoing(text))
    if not kinds:
        return None
    named = ", ".join(dict.fromkeys(kinds))
    return {
        "error": "OUTGOING_CHECK",
        "message": f"REFUSED — outgoing check: {named}. Nothing was sent. Take that out of the text and call again.",
    }


def _market_request(
    method: str,
    endpoint: str,
    data: dict | None = None,
    require_auth: bool = False,
    timeout: int = 30,
) -> dict | list:
    """Execute an HTTP request to The Market (1f3ea.com) API."""
    base_url = _get_base_url()
    url = f"{base_url}{endpoint}" if endpoint.startswith("/") else f"{base_url}/{endpoint}"

    token = credentials.get_market_token()
    if require_auth and not token:
        return {
            "error": "UNAUTHENTICATED",
            "message": "Action requires MARKET_TOKEN. Token is missing or not configured.",
        }

    encoded_data = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=encoded_data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "Agent-Harness/1.0 (Market-Connector)")

    if token:
        req.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            if not body.strip():
                return {"status": "ok", "status_code": response.status}
            return json.loads(body)
    except urllib.error.HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8")
            err_data = json.loads(err_body)
            detail = err_data.get("error") or err_data.get("message") or err_body[:400]
        except Exception:
            detail = str(exc.reason)

        if exc.code == 429:
            return {
                "error": "QUOTA_EXCEEDED",
                "status_code": 429,
                "detail": detail or "Rate limit reached on The Market.",
            }
        elif exc.code in (401, 403):
            return {
                "error": "AUTH_ERROR",
                "status_code": exc.code,
                "detail": detail or "Invalid or unauthorized token for The Market.",
            }
        elif exc.code == 404:
            return {
                "error": "NOT_FOUND",
                "status_code": 404,
                "detail": detail or "Requested Market listing or resource not found.",
            }
        return {
            "error": f"HTTP_{exc.code}",
            "status_code": exc.code,
            "detail": detail,
        }
    except urllib.error.URLError as exc:
        return {
            "error": "NETWORK_ERROR",
            "detail": str(exc.reason),
        }
    except Exception as exc:
        return {
            "error": "REQUEST_FAILED",
            "detail": str(exc),
        }


@mcp.tool()
def browse_shelves(
    aisle: str | None = None,
    sort: str = "new",
    limit: int = 15,
    since_id: int | None = None,
    brief: bool = False,
) -> dict:
    """Browse catalog shelves in the 1F3EA digital marketplace.

    Args:
        aisle: Optional category filter: 'skills', 'tools', 'knowledge',
               'prompts', 'services', or 'world' (physical city bridge).
        sort: Sorting order: 'new' (latest), 'popular', or 'price'.
        limit: Maximum number of listings to return (default: 15).
        since_id: Return only listings whose integer id is greater than this value.
        brief: Cut descriptions to 200 characters and mark only cut entries truncated.
    """
    params: list[str] = []
    if aisle:
        clean_aisle = aisle.strip().lower()
        params.append(f"aisle={urllib.parse.quote(clean_aisle)}")
    if sort:
        clean_sort = sort.strip().lower()
        params.append(f"sort={urllib.parse.quote(clean_sort)}")

    query_str = f"?{'&'.join(params)}" if params else ""
    res = _market_request("GET", f"/api/shelves{query_str}")
    if isinstance(res, dict) and "error" in res:
        return res

    listings: list[dict] = []
    if isinstance(res, list):
        listings = res
    elif isinstance(res, dict):
        listings = res.get("listings") or res.get("data") or []

    if since_id is not None:
        listings = [
            item for item in listings
            if isinstance(item, dict) and isinstance(item.get("id"), int) and item["id"] > since_id
        ]
    if brief:
        compact: list[dict] = []
        for listing in listings:
            item = dict(listing)
            description = item.get("description")
            if isinstance(description, str) and len(description) > 200:
                item["description"] = description[:200]
                item["truncated"] = True
            compact.append(item)
        listings = compact

    return {
        "status": "ok",
        "aisle": aisle,
        "sort": sort,
        "listings": listings[:limit],
        "count": len(listings[:limit]),
        "total_available": len(listings),
    }


@mcp.tool()
def browse_world_aisle(limit: int = 15) -> dict:
    """Browse physical 1F3D9 city artifacts and items bridged to the 1F3EA marketplace.

    Args:
        limit: Maximum number of world items to return (default: 15).
    """
    return browse_shelves(aisle="world", sort="new", limit=limit)


@mcp.tool()
def inspect_listing(listing_id: int) -> dict:
    """Inspect detailed metadata, description, author, and price for a market listing.

    Args:
        listing_id: Integer ID of the listing to inspect.
    """
    if not isinstance(listing_id, int) or listing_id <= 0:
        return {"error": "INVALID_LISTING_ID", "message": "listing_id must be a positive integer."}

    return _market_request("GET", f"/api/listing/{listing_id}")


@mcp.tool()
def read_merchant_profile() -> dict:
    """Read the agent's merchant credentials, status, and active listings on 1F3EA.com.

    Requires valid MARKET_TOKEN.
    """
    return _market_request("GET", "/api/me", require_auth=True)


@mcp.tool()
def read_market_official(fields: list[str] | None = None) -> dict:
    """Read the Market's own official facts: its fees, treasury, payment rules and limits.

    This is the Market's record of how the Market works. Read it here rather
    than relying on a note of your own; it changes. With no arguments you get
    the whole record when it is short, or the short fields and the names of
    the larger ones. Pass `fields` to read named fields in full.

    Args:
        fields: Names of fields to return in full, for example ["world"].
    """
    try:
        facts = _read_market_official()
    except Exception as error:
        return {"error": "OFFICIAL_FACTS_UNAVAILABLE", "message": f"Could not read the Market's official facts: {error}"}
    return official_view(facts, fields, source="1f3ea.com/api/official")


@mcp.tool()
@stop_marker.refuse_when_stopped(lambda: REPO_ROOT, structured=True)
def acquire_free_artifact(listing_id: int, purpose: str) -> dict:
    """Download and stage a free ($0 USDC) artifact from 1F3EA.com into memory/acquired/.

    Refuses immediately if the listing has a price > 0 USDC (fail-closed).

    Args:
        listing_id: Integer ID of the free listing to acquire.
        purpose: Single-line reason for acquiring it (maximum 200 characters).
    """
    if not isinstance(listing_id, int) or listing_id <= 0:
        return {"error": "INVALID_LISTING_ID", "message": "listing_id must be a positive integer."}
    clean_purpose = _validate_purpose(purpose)
    if clean_purpose is None:
        return {
            "error": "INVALID_PURPOSE",
            "message": "purpose must be a non-empty single line of at most 200 characters.",
        }

    listing_data = inspect_listing(listing_id)
    if isinstance(listing_data, dict) and "error" in listing_data:
        return {"status": "FAILED", "error": listing_data.get("error"), "detail": listing_data}
    listing_obj = listing_data.get("listing") if isinstance(listing_data, dict) and "listing" in listing_data else listing_data

    # Hard assertion: price must be 0
    raw_price = listing_obj.get("price_usdc")
    if raw_price is None:
        raw_price = listing_obj.get("price", 0)
    try:
        price_num = float(raw_price)
    except (ValueError, TypeError):
        price_num = 0.0

    if price_num > 0:
        return {
            "status": "REFUSED",
            "error": "PAID_LISTING",
            "message": (
                f"REFUSED: Listing #{listing_id} costs {price_num} USDC. "
                "This tool only acquires $0 items. Buying paid listings is not supported yet. "
                "Nothing was paid. If you want it, tell the operator."
            ),
        }

    title = _bounded_line(listing_obj.get("title"), f"Listing #{listing_id}", 200)
    seller = _bounded_line(
        listing_obj.get("seller") or listing_obj.get("merchant") or listing_obj.get("author"), "unknown", 200
    )
    aisle = _bounded_line(listing_obj.get("aisle"), "general", 100)
    content = ""
    # Officially acquire the free listing via POST /api/buy
    buy_res = _market_request("POST", f"/api/buy/{listing_id}", require_auth=True)
    if isinstance(buy_res, dict) and buy_res.get("error") == "UNAUTHENTICATED":
        # No login means the Market never hears of this. Taking the public
        # download anyway would leave the local and market records disagreeing, and
        # would hide a broken login.
        return {
            "status": "REFUSED",
            "error": "UNAUTHENTICATED",
            "message": "REFUSED: no Market login token, so nothing was acquired. Tell the operator the Market token is missing.",
        }
    if isinstance(buy_res, dict) and "artifact" in buy_res and buy_res["artifact"] != f"purchase required — POST /api/buy/{listing_id}":
        content = buy_res["artifact"]
    elif isinstance(buy_res, dict) and "content" in buy_res:
        content = buy_res["content"]
    elif isinstance(buy_res, str) and buy_res.strip() and not buy_res.startswith("purchase required"):
        content = buy_res

    if not content:
        dl_res = _market_request("GET", f"/api/listing/{listing_id}/download")
        if isinstance(dl_res, dict) and "content" in dl_res:
            content = dl_res["content"]
        elif isinstance(dl_res, str) and dl_res.strip():
            content = dl_res
        elif listing_data.get("content"):
            content = listing_data["content"]
        elif listing_data.get("description"):
            content = listing_data["description"]

    clean_slug = re.sub(r"[^a-z0-9]+", "-", str(title).lower()).strip("-")
    if not clean_slug:
        clean_slug = f"listing-{listing_id}"

    acquired_dir = REPO_ROOT / names.memory("acquired")
    file_path = (acquired_dir / f"{listing_id}-{clean_slug}.md").resolve()
    if not file_path.is_relative_to(acquired_dir.resolve()):
        return {"error": "INVALID_FILENAME", "message": "Illegal listing title."}

    acquired_at = datetime.datetime.now(datetime.timezone.utc)
    today = acquired_at.strftime("%Y-%m-%d")
    header = f"""# {title}

- **Listing ID:** #{listing_id}
- **Seller:** {seller}
- **Acquired Date:** {today}
- **Price:** 0 USDC (Free)
- **Aisle:** {aisle}

---

{content}
"""

    raw_bytes = header.encode("utf-8")
    relative_path = f"{names.memory('acquired')}/{listing_id}-{clean_slug}.md"
    receipt = {
        "listing_id": listing_id,
        "file": relative_path,
        "title": title,
        "seller": seller,
        "aisle": aisle,
        "acquired_utc": acquired_at.isoformat(),
        "purpose": clean_purpose,
        "status": "quarantined",
        "sha256": hashlib.sha256(raw_bytes).hexdigest(),
    }

    with _file_lock("acquired"):
        try:
            index = _load_acquired_index()
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            return {
                "status": "REFUSED",
                "error": "INDEX_UNREADABLE",
                "message": f"Acquired artifact index could not be safely updated: {exc}",
            }
        acquired_dir.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(raw_bytes)
        index["artifacts"] = [
            item for item in index["artifacts"]
            if not isinstance(item, dict) or item.get("listing_id") != listing_id
        ]
        index["artifacts"].append(receipt)
        _write_acquired_index(index)

    return {
        "status": "ACQUIRED",
        "listing_id": listing_id,
        "title": title,
        "saved_path": relative_path,
        "bytes_written": len(raw_bytes),
    }


@mcp.tool()
@enters_private
def list_acquired(conversation_id: HostConversationId = None) -> dict:
    """List quarantined artifact metadata without loading any raw artifact content."""
    with _file_lock("acquired"):
        try:
            index = _load_acquired_index()
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            return {
                "status": "REFUSED",
                "error": "INDEX_UNREADABLE",
                "message": str(exc),
                "artifacts": [],
            }
    return {
        "status": "OK",
        "count": len(index["artifacts"]),
        "artifacts": index["artifacts"],
    }


@mcp.tool()
@enters_private
def read_acquired(listing_id: int, conversation_id: HostConversationId = None) -> dict:
    """Load one quarantined artifact selected by its Market listing ID."""
    if not isinstance(listing_id, int) or listing_id <= 0:
        return {"error": "INVALID_LISTING_ID", "message": "listing_id must be a positive integer."}

    with _file_lock("acquired"):
        try:
            index = _load_acquired_index()
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            return {"status": "REFUSED", "error": "INDEX_UNREADABLE", "message": str(exc)}

        receipt = next(
            (
                item for item in index["artifacts"]
                if isinstance(item, dict) and item.get("listing_id") == listing_id
            ),
            None,
        )
        if receipt is None:
            return {
                "status": "NOT_FOUND",
                "error": "ARTIFACT_NOT_INDEXED",
                "message": f"No acquired artifact is indexed for listing #{listing_id}.",
            }

        stored_path = receipt.get("file")
        if not isinstance(stored_path, str):
            return {"status": "REFUSED", "error": "INVALID_INDEX_PATH"}
        acquired_dir = (REPO_ROOT / names.memory("acquired")).resolve()
        file_path = (REPO_ROOT / stored_path).resolve()
        if not file_path.is_relative_to(acquired_dir) or not file_path.is_file():
            return {"status": "REFUSED", "error": "INVALID_INDEX_PATH"}
        try:
            content = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            return {"status": "REFUSED", "error": "ARTIFACT_UNREADABLE", "message": str(exc)}

    return {
        "status": "OK",
        "listing_id": listing_id,
        "file": stored_path,
        "content": content,
    }


@mcp.tool()
@stop_marker.refuse_when_stopped(lambda: REPO_ROOT, structured=True)
def purchase_listing(listing_id: int, purpose: str) -> dict:
    """Get a listing from 1F3EA. Free listings are downloaded; paid ones are refused.

    Buying a paid listing is not supported yet: the Market requires a fresh,
    signed purchase intent before paying the seller, and this tool does not do
    that. A paid listing returns REFUSED without touching the wallet.

    Args:
        listing_id: Integer ID of the listing to purchase.
        purpose: Single-line reason required when a free listing is downloaded.
    """
    if not isinstance(listing_id, int) or listing_id <= 0:
        return {"error": "INVALID_LISTING_ID", "message": "listing_id must be a positive integer."}

    listing_data = inspect_listing(listing_id)
    if isinstance(listing_data, dict) and "error" in listing_data:
        return {"status": "FAILED", "error": listing_data.get("error"), "detail": listing_data}
    listing_obj = listing_data.get("listing") if isinstance(listing_data, dict) and "listing" in listing_data else listing_data

    raw_price = listing_obj.get("price_usdc")
    if raw_price is None:
        raw_price = listing_obj.get("price", 0)
    try:
        price_num = float(raw_price)
    except (ValueError, TypeError):
        price_num = 0.0

    # If free, route to acquire_free_artifact
    if price_num <= 0:
        return acquire_free_artifact(listing_id, purpose)

    title = _bounded_line(listing_obj.get("title"), f"Listing #{listing_id}", 200)
    return {
        "status": "REFUSED",
        "error": "BUYING_NOT_SUPPORTED",
        "message": (
            f"Listing #{listing_id} ('{title}') costs {price_num} USDC. Buying paid listings "
            "is not supported yet: the Market needs a signed purchase intent this tool "
            "cannot make. Nothing was paid. If you want it, tell the operator."
        ),
    }


# ---------------------------------------------------------------------------
# Listing fee: the agent pays the Market's $1 USDC fee from the Circle wallet.
# ---------------------------------------------------------------------------

FEE_LEDGER = ("private", "market_fee_ledger.json")
FEE_WINDOW = datetime.timedelta(hours=1)
_HEX_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
_HEX_TX = re.compile(r"^0x[0-9a-fA-F]{64}$")
_FINAL_STATES = {"COMPLETE", "COMPLETED", "CONFIRMED", "SUCCESS", "SUCCEEDED"}


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _fee_ledger_path() -> Path:
    return REPO_ROOT / names.memory(*FEE_LEDGER)


def _load_fee_ledger() -> list[dict]:
    path = _fee_ledger_path()
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    # A ledger of the wrong shape, or an entry with no readable date, may be
    # hiding a fee already paid. Raise, so the caller refuses instead of paying again.
    if not isinstance(data, list):
        raise ValueError("fee ledger is not a list")
    for entry in data:
        if not isinstance(entry, dict):
            raise ValueError("fee ledger entry is not an object")
        datetime.datetime.fromisoformat(entry["created_utc"])
    return data


def _save_fee_ledger(entries: list[dict]) -> None:
    path = _fee_ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _recent_open_fee(entries: list[dict]) -> dict | None:
    """The newest fee from the last hour that no listing has used yet."""
    now = _utc_now()
    for entry in reversed(entries):
        if entry.get("used"):
            continue
        try:
            created = datetime.datetime.fromisoformat(entry["created_utc"])
        except (KeyError, TypeError, ValueError):
            continue
        if now - created <= FEE_WINDOW:
            return entry
    return None


def _mark_fee_used(fee_tx_hash: str | None) -> None:
    if not fee_tx_hash:
        return
    with _file_lock("fee_ledger"):
        try:
            entries = _load_fee_ledger()
        except Exception:
            return
        changed = False
        for entry in entries:
            if (entry.get("tx_hash") or "").lower() == fee_tx_hash.strip().lower():
                entry["used"] = True
                entry["used_utc"] = _utc_now().isoformat()
                changed = True
        if changed:
            _save_fee_ledger(entries)


def _fee_was_used(fee_tx_hash: str) -> bool:
    """True when the ledger shows this fee already paid for a listing."""
    try:
        entries = _load_fee_ledger()
    except Exception:
        return False
    wanted = fee_tx_hash.strip().lower()
    return any(entry.get("used") and (entry.get("tx_hash") or "").lower() == wanted for entry in entries)


_FEE_ALREADY_USED = {
    "error": "FEE_ALREADY_USED",
    "message": "This fee_tx_hash already paid for a listing, so it cannot pay for another. Nothing was sent to the Market and nothing was paid.",
}


def _read_market_official() -> dict:
    req = urllib.request.Request(f"{_get_base_url()}/api/official", method="GET")
    req.add_header("User-Agent", "Agent-Harness/1.0 (Market-Connector)")
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _parse_transfer(stdout: str) -> tuple[str | None, str, str | None]:
    """Return (tx_hash, state, transaction_id) from Circle transfer JSON."""
    try:
        out = json.loads(stdout)
    except Exception:
        return None, "", None
    data = out.get("data", out) if isinstance(out, dict) else {}
    if isinstance(data, dict) and isinstance(data.get("transaction"), dict):
        data = data["transaction"]
    if not isinstance(data, dict):
        return None, "", None
    tx_hash = None
    for key in ("txHash", "transactionHash", "hash"):
        value = data.get(key)
        if isinstance(value, str) and _HEX_TX.match(value):
            tx_hash = value
            break
    state = str(data.get("state") or data.get("status") or "").upper()
    tx_id = data.get("id") or data.get("transactionId")
    return tx_hash, state, tx_id


@mcp.tool()
@stop_marker.refuse_when_stopped(lambda: REPO_ROOT, structured=True)
def pay_listing_fee(purpose: str) -> dict:
    """Pay the Market's listing fee from the agent's Circle wallet; returns the fee_tx_hash.

    Call only when the listing text is final. Then, within the hour, call
    create_listing (or activate_world_listing) with the returned fee_tx_hash.

    Needs the operator's approval now: pay_armed true in wallet-config.json AND an
    unlocked admin session. The treasury, USDC contract and fee are read live from
    the Market. Never pays twice: an unused fee from the last hour is returned
    instead, and an unfinished payment is retried with the same idempotency key.

    Args:
        purpose: One line saying what the listing is.
    """
    from tools import authority, wallet

    clean_purpose = _validate_purpose(purpose)
    if clean_purpose is None:
        return {"status": "REFUSED", "error": "INVALID_PURPOSE",
                "message": "purpose must be one line of 1-200 characters."}

    cfg = wallet.load_wallet_config()
    if not wallet.money_actions_allowed(site="1F3EA", cfg=cfg):
        return {"status": "REFUSED", "error": "WALLET_UNAPPROVED",
                "message": "The wallet is not approved for the Market (1F3EA) in wallet-config.json."}
    if not wallet.pay_armed(cfg):
        return {"status": "REFUSED", "error": "DISARMED",
                "message": "pay_armed is false. Ask the operator: say what you want to list and why. Nothing was paid."}
    if not authority.session_active(touch=False):
        return {"status": "REFUSED", "error": "NO_SESSION",
                "message": "No admin session is unlocked: the operator must be present. Nothing was paid."}

    with _file_lock("fee_ledger"):
        try:
            entries = _load_fee_ledger()
        except Exception:
            return {"status": "REFUSED", "error": "LEDGER_UNREADABLE",
                    "message": "The fee ledger is unreadable. Nothing was paid; ask the operator to check it."}

        open_fee = _recent_open_fee(entries)
        if open_fee and open_fee.get("tx_hash"):
            return {"status": "ALREADY_PAID", "fee_tx_hash": open_fee["tx_hash"],
                    "message": "A fee paid in the last hour has not been used yet. Use this fee_tx_hash. Do not pay again."}

        try:
            official = _read_market_official()
        except Exception as exc:
            return {"status": "REFUSED", "error": "OFFICIAL_UNREADABLE",
                    "message": f"Could not read the Market's official facts ({type(exc).__name__}). Nothing was paid."}

        treasury = str(official.get("treasury") or "")
        usdc = str(official.get("usdc_contract") or "")
        network = str(official.get("network") or "").lower()
        try:
            fee = float(official.get("listing_fee_usdc"))
        except (TypeError, ValueError):
            fee = 0.0
        if network != "base" or not _HEX_ADDRESS.match(treasury) or not _HEX_ADDRESS.match(usdc) or fee <= 0:
            return {"status": "REFUSED", "error": "OFFICIAL_UNEXPECTED",
                    "message": "The Market's official payment facts look wrong. Nothing was paid."}
        per_tx = (cfg.get("caps_usdc") or {}).get("per_tx")
        if per_tx is None or fee > float(per_tx):
            return {"status": "REFUSED", "error": "OVER_CAP",
                    "message": f"The fee ({fee} USDC) is above the per-transaction cap. Nothing was paid."}
        payer = str(cfg.get("public_address") or "")
        if not _HEX_ADDRESS.match(payer):
            return {"status": "REFUSED", "error": "NO_WALLET_ADDRESS",
                    "message": "wallet-config.json has no valid public_address. Nothing was paid."}
        if not wallet.wallet_session_ok():
            return {"status": "REFUSED", "error": "CIRCLE_LOGGED_OUT",
                    "message": "The Circle login is missing or expired. The operator runs `circle wallet login` in their terminal. Nothing was paid."}

        if open_fee:
            entry = open_fee
        else:
            entry = {
                "created_utc": _utc_now().isoformat(),
                "idempotency_key": str(uuid.uuid4()),
                "purpose": clean_purpose,
                "amount_usdc": fee,
                "treasury": treasury,
                "payer": payer,
                "status": "submitting",
                "tx_hash": None,
                "used": False,
            }
            entries.append(entry)
            _save_fee_ledger(entries)

        try:
            result = wallet.transfer_token(
                entry["treasury"], entry["amount_usdc"], usdc, payer, entry["idempotency_key"]
            )
        except Exception as exc:
            entry["status"] = "uncertain"
            _save_fee_ledger(entries)
            return {"status": "UNCERTAIN", "error": "TRANSFER_UNKNOWN",
                    "message": f"The transfer command did not finish ({type(exc).__name__}). Do NOT pay another way. Call pay_listing_fee again: it retries the same payment safely."}

        tx_hash, state, tx_id = _parse_transfer(result.stdout)
        if result.returncode != 0 and not tx_hash:
            entry["status"] = "uncertain"
            _save_fee_ledger(entries)
            detail = (result.stderr or result.stdout or "").strip()[:300]
            return {"status": "UNCERTAIN", "error": "TRANSFER_FAILED",
                    "message": f"Circle reported an error: {detail} Do NOT pay another way. Call pay_listing_fee again to retry the same payment, or tell the operator."}

        if tx_id:
            entry["transaction_id"] = tx_id
        if tx_hash:
            entry["tx_hash"] = tx_hash
            entry["status"] = "paid" if state in _FINAL_STATES else f"submitted:{state or 'unknown'}"
            _save_fee_ledger(entries)
            return {"status": "PAID", "fee_tx_hash": tx_hash, "amount_usdc": entry["amount_usdc"],
                    "treasury": entry["treasury"], "state": state or "unknown",
                    "message": "Fee paid. Within the hour, call create_listing or activate_world_listing with this fee_tx_hash. If the Market replies do_not_pay_again, retry the same listing call. Never pay again."}

        entry["status"] = f"pending:{state or 'unknown'}"
        _save_fee_ledger(entries)
        return {"status": "PENDING", "transaction_id": tx_id, "state": state or "unknown",
                "message": "The payment was submitted but has no transaction hash yet. Do NOT pay again. Wait a minute, then call pay_listing_fee again to fetch the hash."}


@mcp.tool()
@stop_marker.refuse_when_stopped(lambda: REPO_ROOT, structured=True)
def create_world_draft(
    thing_id: int,
    title: str,
    description: str,
    preview: str,
    price_usdc: float,
    tags: list[str] | None = None,
    seller_wallet: str | None = None,
) -> dict:
    """Create a pending 1-hour draft on 1F3EA to sell an owned 1F3D9 city Thing.

    Step 1 of the 3-step World Aisle listing flow:
    1. Call create_world_draft (this tool) -> get market_draft_id
    2. In city: call city.list_world(thing_id, market_draft_id) -> get city_offer_id
    3. Call activate_world_listing(draft_id=..., city_offer_id=..., fee_tx_hash=...)

    Args:
        thing_id: Integer ID of the active city thing you own.
        title: Title of the listing (3 to 120 characters).
        description: Full description of the item (1 to 4,000 characters).
        preview: Public preview text (up to 4,000 characters).
        price_usdc: Sale price in USDC (> 0 and <= 10,000).
        tags: Optional list of up to 8 tag strings (each <= 40 chars).
        seller_wallet: Optional 0x Base address (defaults to wallet-config.json).
    """
    if not isinstance(thing_id, int) or thing_id <= 0:
        return {"error": "INVALID_THING_ID", "message": "thing_id must be a positive integer."}

    clean_title = (title or "").strip()
    if len(clean_title) < 3 or len(clean_title) > 120:
        return {
            "error": "INVALID_TITLE",
            "message": f"Title must be between 3 and 120 characters (got {len(clean_title)}).",
        }

    clean_desc = (description or "").strip()
    if len(clean_desc) < 1 or len(clean_desc) > 4000:
        return {
            "error": "INVALID_DESCRIPTION",
            "message": f"Description must be between 1 and 4000 characters (got {len(clean_desc)}).",
        }

    clean_preview = (preview or "").strip()
    if len(clean_preview) > 4000:
        return {
            "error": "PREVIEW_TOO_LONG",
            "message": f"Preview exceeds 4,000 character limit (got {len(clean_preview)}).",
        }

    try:
        price_val = float(price_usdc)
    except (ValueError, TypeError):
        return {"error": "INVALID_PRICE", "message": "price_usdc must be a valid number."}

    if price_val <= 0 or price_val > 10000:
        return {
            "error": "INVALID_PRICE",
            "message": "price_usdc for world listings must be > 0 and <= 10,000.",
        }

    wallet_addr = (seller_wallet or "").strip()
    if not wallet_addr:
        from tools import wallet
        cfg = wallet.load_wallet_config()
        wallet_addr = cfg.get("public_address", "").strip()

    if not wallet_addr or not re.match(r"^0x[0-9a-fA-F]{40}$", wallet_addr):
        return {
            "error": "INVALID_SELLER_WALLET",
            "message": "seller_wallet must be a valid 0x Base address (42 chars).",
        }

    payload: dict[str, object] = {
        "title": clean_title,
        "description": clean_desc,
        "preview": clean_preview,
        "price_usdc": round(price_val, 6),
        "seller_wallet": wallet_addr,
        "thing_id": thing_id,
    }

    if tags:
        clean_tags = [str(t).strip().lower()[:40] for t in tags if str(t).strip()]
        if clean_tags:
            payload["tags"] = clean_tags[:8]

    refusal = _outgoing_refusal(payload, ("title", "description", "preview", "tags"))
    if refusal:
        return refusal
    return _market_request("POST", "/api/world/draft", data=payload, require_auth=True)


@mcp.tool()
@stop_marker.refuse_when_stopped(lambda: REPO_ROOT, structured=True)
def cancel_world_draft(draft_id: int) -> dict:
    """Cancel a pending, unlisted world draft on 1F3EA.

    Args:
        draft_id: Integer ID of the pending world draft to cancel.
    """
    if not isinstance(draft_id, int) or draft_id <= 0:
        return {"error": "INVALID_DRAFT_ID", "message": "draft_id must be a positive integer."}

    return _market_request(
        "POST", f"/api/world/draft/{draft_id}/cancel", data={}, require_auth=True
    )


@mcp.tool()
@stop_marker.refuse_when_stopped(lambda: REPO_ROOT, structured=True)
def activate_world_listing(
    draft_id: int,
    city_offer_id: int,
    fee_tx_hash: str | None = None,
) -> dict:
    """Activate a world-aisle listing on 1F3EA after locking the thing in 1F3D9.

    Step 3 of the World Aisle listing flow:
    Requires the draft_id from create_world_draft, the city_offer_id from
    city.list_world(), and optionally the fee_tx_hash proving the $1 USDC listing fee.

    Args:
        draft_id: Integer ID of the pending 1F3EA world draft.
        city_offer_id: Integer ID of the city offer created by city.list_world().
        fee_tx_hash: Optional 0x transaction hash of the $1 USDC listing fee paid on Base.
    """
    if not isinstance(draft_id, int) or draft_id <= 0:
        return {"error": "INVALID_DRAFT_ID", "message": "draft_id must be a positive integer."}
    if not isinstance(city_offer_id, int) or city_offer_id <= 0:
        return {"error": "INVALID_OFFER_ID", "message": "city_offer_id must be a positive integer."}

    payload: dict[str, object] = {
        "draft_id": draft_id,
        "city_offer_id": city_offer_id,
    }
    if fee_tx_hash:
        clean_tx = fee_tx_hash.strip()
        if not re.match(r"^0x[0-9a-fA-F]{64}$", clean_tx):
            return {
                "error": "INVALID_TX_HASH",
                "message": "fee_tx_hash must be a 66-character hex hash (0x...).",
            }
        if _fee_was_used(clean_tx):
            return dict(_FEE_ALREADY_USED)
        payload["fee_tx_hash"] = clean_tx

    result = _market_request("POST", "/api/world/listing", data=payload, require_auth=True)
    if isinstance(result, dict) and "error" not in result:
        _mark_fee_used(fee_tx_hash)
    return result


@mcp.tool()
@stop_marker.refuse_when_stopped(lambda: REPO_ROOT, structured=True)
def create_listing(
    title: str,
    description: str,
    preview: str,
    artifact: str,
    price_usdc: float,
    tags: list[str] | None = None,
    aisle: str | None = None,
    fee_tx_hash: str | None = None,
    seller_wallet: str | None = None,
) -> dict:
    """Publish an ordinary digital artifact listing (skills, tools, knowledge, prompts) on 1F3EA.

    Requires $1 USDC listing fee on Base (or fee_tx_hash). Artifact is revealed
    only to verified buyers after payment.

    Args:
        title: Title of the listing (3 to 120 characters).
        description: Full description of what the artifact provides (1 to 4,000 characters).
        preview: Public preview text readable by anyone on the shelf (up to 4,000 characters).
        artifact: The full digital product text or JSON (1 byte to 256 KB).
        price_usdc: Sale price in USDC (0 for free, up to 10,000).
        tags: Optional list of up to 8 category tags (each <= 40 chars).
        aisle: Optional category ('skills', 'tools', 'knowledge', 'prompts', 'services', 'data', 'other').
        fee_tx_hash: Optional 0x transaction hash of the $1 USDC listing fee paid on Base.
        seller_wallet: Optional 0x Base address (defaults to wallet-config.json).
    """
    clean_title = (title or "").strip()
    if len(clean_title) < 3 or len(clean_title) > 120:
        return {
            "error": "INVALID_TITLE",
            "message": f"Title must be between 3 and 120 characters (got {len(clean_title)}).",
        }

    clean_desc = (description or "").strip()
    if len(clean_desc) < 1 or len(clean_desc) > 4000:
        return {
            "error": "INVALID_DESCRIPTION",
            "message": f"Description must be between 1 and 4000 characters (got {len(clean_desc)}).",
        }

    clean_preview = (preview or "").strip()
    if len(clean_preview) > 4000:
        return {
            "error": "PREVIEW_TOO_LONG",
            "message": f"Preview exceeds 4,000 character limit (got {len(clean_preview)}).",
        }

    if not artifact or len(artifact.encode("utf-8")) > 262144:
        return {
            "error": "INVALID_ARTIFACT",
            "message": "artifact must be non-empty and at most 256 KB (262,144 bytes).",
        }

    try:
        price_val = float(price_usdc)
    except (ValueError, TypeError):
        return {"error": "INVALID_PRICE", "message": "price_usdc must be a valid number."}

    if price_val < 0 or price_val > 10000:
        return {
            "error": "INVALID_PRICE",
            "message": "price_usdc must be between 0 and 10,000 USDC.",
        }

    wallet_addr = (seller_wallet or "").strip()
    if not wallet_addr:
        from tools import wallet
        cfg = wallet.load_wallet_config()
        wallet_addr = cfg.get("public_address", "").strip()

    if not wallet_addr or not re.match(r"^0x[0-9a-fA-F]{40}$", wallet_addr):
        return {
            "error": "INVALID_SELLER_WALLET",
            "message": "seller_wallet must be a valid 0x Base address (42 chars).",
        }

    payload: dict[str, object] = {
        "title": clean_title,
        "description": clean_desc,
        "preview": clean_preview,
        "artifact": artifact,
        "price_usdc": round(price_val, 6),
        "seller_wallet": wallet_addr,
    }

    if tags:
        clean_tags = [str(t).strip().lower()[:40] for t in tags if str(t).strip()]
        if clean_tags:
            payload["tags"] = clean_tags[:8]

    if aisle:
        clean_aisle = aisle.strip().lower()
        if clean_aisle in VALID_AISLES:
            payload["aisle"] = clean_aisle

    if fee_tx_hash:
        clean_tx = fee_tx_hash.strip()
        if not re.match(r"^0x[0-9a-fA-F]{64}$", clean_tx):
            return {
                "error": "INVALID_TX_HASH",
                "message": "fee_tx_hash must be a 66-character hex hash (0x...).",
            }
        if _fee_was_used(clean_tx):
            return dict(_FEE_ALREADY_USED)
        payload["fee_tx_hash"] = clean_tx

    # A refusal here leaves any listing fee already paid unused, for the next call.
    refusal = _outgoing_refusal(payload, ("title", "description", "preview", "artifact", "tags"))
    if refusal:
        return refusal
    result = _market_request("POST", "/api/listing", data=payload, require_auth=True)
    if isinstance(result, dict) and "error" not in result:
        _mark_fee_used(fee_tx_hash)
    return result


@mcp.tool()
@stop_marker.refuse_when_stopped(lambda: REPO_ROOT, structured=True)
def write_store_line(line: str) -> dict:
    """Update the agent's storefront description line on 1F3EA (shown on /api/store/<handle>).

    Args:
        line: Store motto or status line (maximum 160 characters).
    """
    clean_line = (line or "").strip()
    if len(clean_line) > 160:
        return {
            "error": "LINE_TOO_LONG",
            "message": f"Store line exceeds 160 character limit (got {len(clean_line)}).",
        }

    refusal = _outgoing_refusal({"line": clean_line}, ("line",))
    if refusal:
        return refusal
    return _market_request("POST", "/api/store", data={"line": clean_line}, require_auth=True)


@mcp.tool()
@stop_marker.refuse_when_stopped(lambda: REPO_ROOT, structured=True)
def withdraw_listing(listing_id: int) -> dict:
    """Permanently withdraw an owned listing from 1F3EA shelves.

    Withdrawing is permanent and stops new sales while preserving prior buyers' access.

    Args:
        listing_id: Integer ID of the listing to withdraw.
    """
    if not isinstance(listing_id, int) or listing_id <= 0:
        return {"error": "INVALID_LISTING_ID", "message": "listing_id must be a positive integer."}

    return _market_request(
        "POST", f"/api/listing/{listing_id}/withdraw", data={}, require_auth=True
    )


if __name__ == "__main__":
    print("Starting agent-market FastMCP server...", file=sys.stderr)
    mcp.run()
