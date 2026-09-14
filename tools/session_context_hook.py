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

    # AGY lazy tool wrapper (e.g., call_mcp_tool with ServerName and ToolName)
    if tool_name == "call_mcp_tool":
        server_name = tool_args.get("ServerName")
        target_tool = tool_args.get("ToolName")
        qualified_name = f"{server_name}.{target_tool}"
        if qualified_name in TARGET_TOOLS:
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
