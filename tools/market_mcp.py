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
from tools.phase import enters_private

mcp = FastMCP("agent-market")
HostConversationId = Annotated[str | None, Field(alias="_conversationId")]

DEFAULT_MARKET_URL = "https://1f3ea.com"
VALID_AISLES = {"skills", "tools", "knowledge", "prompts", "services", "world"}

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
) -> dict:
    """Browse catalog shelves in the 1F3EA digital marketplace.

    Args:
        aisle: Optional category filter: 'skills', 'tools', 'knowledge',
               'prompts', 'services', or 'world' (physical city bridge).
        sort: Sorting order: 'new' (latest), 'popular', or 'price'.
        limit: Maximum number of listings to return (default: 15).
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

    # Hard assertion: price must be 0
    raw_price = listing_data.get("price_usdc")
    if raw_price is None:
        raw_price = listing_data.get("price", 0)
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
                "Free artifact tool only acquires $0 items. Use gated purchasing for paid listings."
            ),
        }

    title = _bounded_line(listing_data.get("title"), f"Listing #{listing_id}", 200)
    seller = _bounded_line(
        listing_data.get("seller") or listing_data.get("author"), "unknown", 200
    )
    aisle = _bounded_line(listing_data.get("aisle"), "general", 100)
    content = listing_data.get("content") or listing_data.get("artifact") or listing_data.get("description") or ""

    if not content:
        dl_res = _market_request("GET", f"/api/listing/{listing_id}/download")
        if isinstance(dl_res, dict) and "content" in dl_res:
            content = dl_res["content"]
        elif isinstance(dl_res, str) and dl_res.strip():
            content = dl_res

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
def purchase_listing(listing_id: int, purpose: str) -> dict:
    """Attempt a paid purchase of a listing on 1F3EA using Circle Agent Wallet on Base USDC.

    Fail-closed: requires '1F3EA' in approved site_scope, and requires pay_armed=True in wallet-config.json.
    If disarmed or unapproved, generates an operator request file and refuses payment.

    Args:
        listing_id: Integer ID of the listing to purchase.
        purpose: Single-line reason required when a free listing is downloaded.
    """
    if not isinstance(listing_id, int) or listing_id <= 0:
        return {"error": "INVALID_LISTING_ID", "message": "listing_id must be a positive integer."}

    listing_data = inspect_listing(listing_id)
    if isinstance(listing_data, dict) and "error" in listing_data:
        return {"status": "FAILED", "error": listing_data.get("error"), "detail": listing_data}

    raw_price = listing_data.get("price_usdc")
    if raw_price is None:
        raw_price = listing_data.get("price", 0)
    try:
        price_num = float(raw_price)
    except (ValueError, TypeError):
        price_num = 0.0

    # If free, route to acquire_free_artifact
    if price_num <= 0:
        return acquire_free_artifact(listing_id, purpose)

    # Check Gate A: 1F3EA in site_scope and wallet approved
    from tools import wallet

    if not wallet.money_actions_allowed(site="1F3EA"):
        return {
            "status": "REFUSED",
            "error": "WALLET_UNAPPROVED",
            "message": (
                f"REFUSED: Listing #{listing_id} costs {price_num} USDC, but '1F3EA' is not "
                "in approved site_scope in wallet-config.json, or wallet is not enabled/autonomous."
            ),
        }

    # Check Gate B: pay_armed
    if not wallet.pay_armed():
        return {
            "status": "HELD",
            "error": "DISARMED",
            "message": (
                f"DISARMED: Paid purchase of listing #{listing_id} "
                f"('{listing_data.get('title')}', {price_num} USDC, seller "
                f"{listing_data.get('seller', 'unknown')}) needs the operator to set "
                "pay_armed: true in wallet-config.json. Tell the operator with "
                "harness.write_operator_message and note it in this wake's shift report."
            ),
        }

    # Gate C: Live circle session
    if not wallet.wallet_session_ok():
        return {
            "status": "REFUSED",
            "error": "SESSION_EXPIRED",
            "message": (
                "No live Circle session (`circle wallet status` failed). "
                "Operator must re-auth by email OTP in terminal before purchase."
            ),
        }

    # Gate D: Process payment via wallet.pay
    seller_address = listing_data.get("seller_address") or listing_data.get("recipient")
    if not seller_address:
        return {
            "status": "REFUSED",
            "error": "NO_SELLER_ADDRESS",
            "message": f"Listing #{listing_id} has no seller payment address recorded.",
        }

    pay_res = wallet.pay(
        recipient=seller_address,
        amount_usdc=price_num,
        purpose=f"1F3EA Listing #{listing_id}",
    )
    return {
        "status": "PURCHASED",
        "listing_id": listing_id,
        "payment": pay_res,
    }


if __name__ == "__main__":
    print("Starting agent-market FastMCP server...", file=sys.stderr)
    mcp.run()
