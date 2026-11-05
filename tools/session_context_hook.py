#!/usr/bin/env python3
"""Bind AGY's host conversation ID to security-sensitive MCP tool calls."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import names  # noqa: E402


# (server, Python function name). Exposed names come from naming.json.
_TARGETS = (
    ("harness", "check_inbox"),
    ("harness", "reply_to_message"),
    ("harness", "wake_status"),
    ("harness", "read_correspondence"),
    ("harness", "write_operator_message"),
    ("harness", "stage_draft"),
    ("harness", "read_drafts"),
    ("harness", "archive_draft"),
    ("market", "list_acquired"),
    ("market", "read_acquired"),
)
TARGET_TOOLS = frozenset(
    f"{server}.{names.tool(function) if server == 'harness' else function}"
    for server, function in _TARGETS
)


# Memory readers (brief 52's read meter): the ID only labels the read-log
# line, so it is added when the host has one and never required.
_METERED = (
    "compute_route",
    "list_continents",
    "query_almanac",
    "read_almanac_status",
    "read_card",
    "read_city_rules",
    "read_goal",
    "read_goals",
    "read_governance",
    "read_identity",
    "read_last_seen",
    "read_lessons",
    "read_map",
    "read_open_threads",
    "read_operator",
    "read_place",
    "read_project",
    "read_recent_journal",
    "read_registry",
    "read_resident",
    "read_thread",
    "read_threads",
    "read_wake_card",
    "read_wishlist",
    "read_work",
    "read_works_index",
    "recall",
    "recover_unrecorded",
)
METERED_TOOLS = frozenset(f"harness.{names.tool(function)}" for function in _METERED)


def _canonical_uuid(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError):
        return None


def process(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        return {"decision": "deny", "reason": "Malformed hook input."}
    tool_call = payload.get("toolCall")
    if not isinstance(tool_call, dict):
        return {"decision": "allow"}

    tool_name = tool_call.get("name")
    tool_args = tool_call.get("args")
    if not isinstance(tool_args, dict):
        tool_args = {}

    conversation_id = _canonical_uuid(payload.get("conversationId"))

    # Direct tool invocation (e.g., in stdio client tests: "harness.wake_status")
    if tool_name in TARGET_TOOLS:
        if conversation_id is None:
            return {
                "decision": "deny",
                "reason": "Missing or malformed host conversation ID.",
            }
        return {
            "decision": "allow",
            "overwrite": {"_conversationId": conversation_id},
        }

    if tool_name in METERED_TOOLS:
        if conversation_id is None:
            return {"decision": "allow"}
        return {
            "decision": "allow",
            "overwrite": {"_conversationId": conversation_id},
        }

    # AGY lazy tool wrapper (e.g., call_mcp_tool with ServerName and ToolName)
    if tool_name == "call_mcp_tool":
        server_name = tool_args.get("ServerName")
        target_tool = tool_args.get("ToolName")
        qualified_name = f"{server_name}.{target_tool}"
        metered = qualified_name in METERED_TOOLS
        if qualified_name in TARGET_TOOLS or (metered and conversation_id is not None):
            if conversation_id is None:
                return {
                    "decision": "deny",
                    "reason": "Missing or malformed host conversation ID.",
                }
            raw_args = tool_args.get("Arguments", {})
            if isinstance(raw_args, str):
                try:
                    inner_args = json.loads(raw_args)
                except Exception:
                    inner_args = {}
            elif isinstance(raw_args, dict):
                inner_args = dict(raw_args)
            else:
                inner_args = {}
            inner_args["_conversationId"] = conversation_id
            return {
                "decision": "allow",
                "overwrite": {"Arguments": inner_args},
            }

    return {"decision": "allow"}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        result = process(payload)
        sys.stdout.write(json.dumps(result))
        return 0
    except Exception:
        sys.stdout.write(json.dumps({"decision": "deny", "reason": "Hook failure."}))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
