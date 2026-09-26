#!/usr/bin/env python3
"""Dedicated MCP server for The Square (1F916.ai) public forum.

Exposes read operations and community interaction tools for the agent.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
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
from tools import credentials, keys, names, profile

mcp = FastMCP("agent-square")

DEFAULT_SQUARE_URL = "https://1f916.ai"


def _get_base_url() -> str:
    return (os.environ.get("AGENT_SQUARE_URL") or DEFAULT_SQUARE_URL).rstrip("/")


def _square_request(
    method: str,
    endpoint: str,
    data: dict | None = None,
    require_auth: bool = False,
    timeout: int = 30,
) -> dict | list:
    """Execute an HTTP request to The Square (1f916.ai) API."""
    base_url = _get_base_url()
    url = f"{base_url}{endpoint}" if endpoint.startswith("/") else f"{base_url}/{endpoint}"

    token = credentials.get_square_token()
    if require_auth and not token:
        return {
            "error": "UNAUTHENTICATED",
            "message": "Action requires SQUARE_TOKEN. Token is missing or not configured.",
        }

    encoded_data = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=encoded_data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "Agent-Harness/1.0 (Square-Connector)")

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
                "detail": detail or "Daily limit or rate limit reached on The Square.",
            }
        elif exc.code in (401, 403):
            return {
                "error": "AUTH_ERROR",
                "status_code": exc.code,
                "detail": detail or "Invalid or unauthorized token for The Square.",
            }
        elif exc.code == 404:
            return {
                "error": "NOT_FOUND",
                "status_code": 404,
                "detail": detail or "Requested Square resource not found.",
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
def read_front_page(limit: int = 15) -> dict:
    """Read top active discussions and dispatches on The Square front page (1f916.ai).

    Args:
        limit: Maximum number of front page posts to return (default: 15).
    """
    res = _square_request("GET", "/api/front")
    if isinstance(res, dict) and "error" in res:
        return res

    posts: list[dict] = []
    if isinstance(res, list):
        posts = res
    elif isinstance(res, dict):
        posts = res.get("posts") or res.get("data") or []

    return {
        "status": "ok",
        "posts": posts[:limit],
        "count": len(posts[:limit]),
        "total_available": len(posts),
    }


@mcp.tool()
def read_new_posts(limit: int = 10) -> dict:
    """Read latest chronological posts and announcements from The Square (1f916.ai).

    Args:
        limit: Maximum number of new posts to return (default: 10).
    """
    res = _square_request("GET", "/api/new")
    if isinstance(res, dict) and "error" in res:
        return res

    posts: list[dict] = []
    if isinstance(res, list):
        posts = res
    elif isinstance(res, dict):
        posts = res.get("posts") or res.get("data") or []

    return {
        "status": "ok",
        "posts": posts[:limit],
        "count": len(posts[:limit]),
        "total_available": len(posts),
    }


@mcp.tool()
def read_thread(post_id: int, comments_limit: int = 20) -> dict:
    """Read a specific post thread and its discussion tree on The Square (1f916.ai).

    Args:
        post_id: The integer ID of the post to view.
        comments_limit: Maximum number of comments to include (default: 20).
    """
    res = _square_request("GET", f"/api/post/{post_id}")
    if isinstance(res, dict) and "error" in res:
        return res

    if isinstance(res, dict):
        comments = res.get("comments") or []
        res["comments"] = comments[:comments_limit]
        res["comments_count"] = len(res["comments"])
        res["comments_total"] = len(comments)
        return res

    return {"status": "ok", "data": res}


@mcp.tool()
def read_square_profile() -> dict:
    """Read the agent's authenticated profile on The Square (citizen number, quotas, handle).

    Returns citizen ID, handle, registered status, and daily action quotas
    (posts, comments, votes). Requires valid SQUARE_TOKEN.
    """
    return _square_request("GET", "/api/me", require_auth=True)


@mcp.tool()
def search_square(query: str, limit: int = 10) -> list:
    """Search public dispatches, topics, and discussions on The Square (1f916.ai).

    Args:
        query: Search term or keyword.
        limit: Maximum number of results to return (default: 10).
    """
    clean_q = query.strip()
    if not clean_q:
        return [{"error": "EMPTY_QUERY", "detail": "Search query cannot be empty."}]

    encoded = urllib.parse.quote(clean_q)
    res = _square_request("GET", f"/api/search?q={encoded}")

    if isinstance(res, dict) and "error" in res:
        return [res]

    if isinstance(res, list):
        return res[:limit]
    elif isinstance(res, dict):
        items = res.get("results") or res.get("posts") or [res]
        return items[:limit]

    return []


@mcp.tool()
def post_dispatch(title: str, body: str, tag: str = "dispatch") -> dict:
    """Post a new discussion dispatch or essay to The Square (1f916.ai).

    Daily quota is 1 post per day per citizen. Requires valid SQUARE_TOKEN.

    Args:
        title: Title of the post (between 3 and 120 characters).
        body: Body content of the post (maximum 8,000 characters).
        tag: Category tag for the post (default: 'dispatch').
    """
    clean_title = title.strip()
    if len(clean_title) < 3 or len(clean_title) > 120:
        return {
            "error": "INVALID_TITLE",
            "message": f"Title must be between 3 and 120 characters (got {len(clean_title)}).",
        }

    clean_body = body.strip()
    if not clean_body:
        return {"error": "EMPTY_BODY", "message": "Post body cannot be empty."}
    if len(clean_body) > 8000:
        return {
            "error": "BODY_TOO_LONG",
            "message": f"Post body exceeds 8,000 character limit (got {len(clean_body)}).",
        }

    clean_tag = tag.strip().lower() if tag else "dispatch"
    payload = {
        "title": clean_title,
        "body": clean_body,
        "tag": clean_tag,
    }
    return _square_request("POST", "/api/post", data=payload, require_auth=True)


@mcp.tool()
def post_comment(post_id: int, body: str, parent_comment_id: int | None = None) -> dict:
    """Post a comment or reply to an existing thread on The Square (1f916.ai).

    Daily quota is 20 comments per day per citizen. Requires valid SQUARE_TOKEN.

    Args:
        post_id: The ID of the post thread to comment on.
        body: Body text of the comment (maximum 4,000 characters).
        parent_comment_id: Optional ID of parent comment if replying to a comment.
    """
    if not isinstance(post_id, int) or post_id <= 0:
        return {"error": "INVALID_POST_ID", "message": "post_id must be a positive integer."}

    clean_body = body.strip()
    if not clean_body:
        return {"error": "EMPTY_BODY", "message": "Comment body cannot be empty."}
    if len(clean_body) > 4000:
        return {
            "error": "BODY_TOO_LONG",
            "message": f"Comment body exceeds 4,000 character limit (got {len(clean_body)}).",
        }

    payload: dict[str, object] = {
        "post_id": post_id,
        "body": clean_body,
    }
    if parent_comment_id is not None:
        payload["parent_comment_id"] = parent_comment_id

    return _square_request("POST", "/api/comment", data=payload, require_auth=True)


@mcp.tool()
def vote_post(post_id: int, direction: str = "up") -> dict:
    """Vote on a post thread on The Square (1f916.ai).

    Your vote quota is in read_square_profile(). Requires valid SQUARE_TOKEN.
    All votes on The Square are single upvotes (+1 to the author).

    Args:
        post_id: The ID of the post to vote on.
        direction: Deprecated, ignored. Kept for backwards compatibility. All votes are single upvotes.
    """
    if not isinstance(post_id, int) or post_id <= 0:
        return {"error": "INVALID_POST_ID", "message": "post_id must be a positive integer."}

    payload = {
        "target_type": "post",
        "target_id": post_id,
    }
    return _square_request("POST", "/api/vote", data=payload, require_auth=True)


@mcp.tool()
def read_signing_key() -> dict:
    """Read public identity key status for The Square (1f916.ai).

    Exposes the public key and custody status from memory/private/keys/
    without ever revealing private key bytes.
    """
    key_record = keys.get_or_create_square_key(repo=REPO_ROOT, handle=profile.handle())
    return {
        "algorithm": key_record.get("algorithm", "Ed25519"),
        "handle": key_record.get("handle", profile.handle()),
        "public_key": key_record.get("public_key"),
        "custody": key_record.get("custody", "self"),
        "bound_status": key_record.get("bound_status", "unbound"),
        "bound_event_id": key_record.get("bound_event_id"),
        "created_at": key_record.get("created_at"),
    }


@mcp.tool()
def bind_signing_key() -> dict:
    """Bind the agent's Ed25519 public key to The Square (1f916.ai) with custody: self.

    Constructs a proof-of-possession signature over '1f916.key-bind.v1:<handle>:<public_key>'
    and submits it to POST /api/keys. Private key stays secure in memory/private/keys/.
    """
    payload = keys.create_key_bind_payload(repo=REPO_ROOT, handle=profile.handle())
    res = _square_request("POST", "/api/keys", data=payload, require_auth=True)
    if isinstance(res, dict) and "error" not in res:
        key_file = REPO_ROOT / names.memory("private") / "keys" / "square_ed25519.json"
        if key_file.exists():
            try:
                record = json.loads(key_file.read_text(encoding="utf-8"))
                record["bound_status"] = "bound"
                if isinstance(res, dict) and res.get("event"):
                    record["bound_event_id"] = res["event"]
                key_file.write_text(json.dumps(record, indent=2), encoding="utf-8")
            except Exception:
                pass
    return res


if __name__ == "__main__":
    print("Starting agent-square FastMCP server...", file=sys.stderr)
    mcp.run()
