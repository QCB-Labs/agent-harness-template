"""Phase machine: the mechanism that makes information release enforceable.

The property under test is that "public reply available while private context
is loaded" is unreachable. Everything else follows from that.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools import names
from tools import phase, session_context_hook

CONVERSATION_A = "11111111-1111-4111-8111-111111111111"
CONVERSATION_B = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def isolated_phase(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(phase, "REPO_ROOT", tmp_path)
    return tmp_path


def _stamp(state: dict, path: Path) -> None:
    target = path / names.memory("private") / "wake_phase.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(state), encoding="utf-8")


# --- baseline -------------------------------------------------------------

def test_phase_starts_at_orient(isolated_phase: Path) -> None:
    assert phase.current() == phase.ORIENT


def test_advance_moves_forward(isolated_phase: Path) -> None:
    assert phase.advance(phase.PUBLIC_REPLY) == phase.PUBLIC_REPLY
    assert phase.current() == phase.PUBLIC_REPLY
    assert phase.advance(phase.PRIVATE) == phase.PRIVATE
    assert phase.current() == phase.PRIVATE


def test_advance_to_same_phase_is_allowed(isolated_phase: Path) -> None:
    phase.advance(phase.PUBLIC_REPLY)
    assert phase.advance(phase.PUBLIC_REPLY) == phase.PUBLIC_REPLY


def test_skipping_straight_to_private_is_allowed(isolated_phase: Path) -> None:
    """Advancing early is safe: it forfeits stranger replies and leaks nothing."""
    assert phase.advance(phase.PRIVATE) == phase.PRIVATE


# --- the core security property ------------------------------------------

def test_backward_transition_raises(isolated_phase: Path) -> None:
    phase.advance(phase.PRIVATE)
    with pytest.raises(phase.PhaseError):
        phase.advance(phase.PUBLIC_REPLY)
    with pytest.raises(phase.PhaseError):
        phase.advance(phase.ORIENT)
    assert phase.current() == phase.PRIVATE


def test_public_reply_window_cannot_be_reopened(isolated_phase: Path) -> None:
    """The whole point: once private context is touched, no going back."""
    @phase.enters_private
    def read_a_draft(conversation_id: str | None = None) -> str:
        return "draft body"

    @phase.requires_phase(phase.ORIENT, phase.PUBLIC_REPLY)
    def reply_to_stranger() -> str:
        return "sent"

    assert reply_to_stranger() == "sent"      # before private: fine
    read_a_draft(conversation_id=CONVERSATION_A)  # closes the window
    with pytest.raises(phase.PhaseError):
        reply_to_stranger()                   # after private: refused


def test_unknown_phase_name_raises(isolated_phase: Path) -> None:
    with pytest.raises(phase.PhaseError):
        phase.advance("superuser")


# --- decorators -----------------------------------------------------------

def test_requires_phase_allows_listed_phases(isolated_phase: Path) -> None:
    @phase.requires_phase(phase.PRIVATE)
    def private_tool() -> str:
        return "ok"

    with pytest.raises(phase.PhaseError):
        private_tool()
    phase.advance(phase.PRIVATE)
    assert private_tool() == "ok"


def test_enters_private_advances_with_valid_identity(isolated_phase: Path) -> None:
    @phase.enters_private
    def private_tool(conversation_id: str | None = None) -> str:
        return "ok"

    assert phase.current() == phase.ORIENT
    assert private_tool(conversation_id=CONVERSATION_A) == "ok"
    assert phase.current() == phase.PRIVATE


def test_enters_private_is_idempotent(isolated_phase: Path) -> None:
    @phase.enters_private
    def private_tool(conversation_id: str | None = None) -> str:
        return "ok"

    private_tool(conversation_id=CONVERSATION_A)
    private_tool(conversation_id=CONVERSATION_A)
    assert phase.current() == phase.PRIVATE
    receipts = list((
        isolated_phase / names.memory("private") / "private-use" / CONVERSATION_A
    ).glob("*.json"))
    assert len(receipts) == 2


def test_decorators_preserve_signature(isolated_phase: Path) -> None:
    """FastMCP builds its schema from the signature — it must survive wrapping."""
    import inspect

    @phase.requires_phase(phase.ORIENT)
    def tool_a(handle: str, count: int = 3) -> str:
        """Docstring survives."""
        return handle

    assert tool_a.__name__ == "tool_a"
    assert tool_a.__doc__ == "Docstring survives."
    assert list(inspect.signature(tool_a).parameters) == ["handle", "count"]


# --- resilience -----------------------------------------------------------

def test_stale_phase_file_resets_to_orient(isolated_phase: Path) -> None:
    old = datetime.now(timezone.utc) - phase.WAKE_IDLE_RESET - timedelta(minutes=1)
    _stamp({"phase": phase.PRIVATE, "changed_utc": old.isoformat()}, isolated_phase)
    assert phase.current() == phase.ORIENT


def test_recent_phase_file_is_respected(isolated_phase: Path) -> None:
    recent = datetime.now(timezone.utc) - timedelta(minutes=5)
    _stamp({"phase": phase.PRIVATE, "changed_utc": recent.isoformat()}, isolated_phase)
    assert phase.current() == phase.PRIVATE


def test_corrupt_phase_file_fails_closed_to_private(isolated_phase: Path) -> None:
    target = isolated_phase / names.memory("private") / "wake_phase.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{not json", encoding="utf-8")
    assert phase.current() == phase.PRIVATE


def test_unknown_phase_value_fails_closed(isolated_phase: Path) -> None:
    now = datetime.now(timezone.utc).isoformat()
    _stamp({"phase": "superuser", "changed_utc": now}, isolated_phase)
    assert phase.current() == phase.PRIVATE


def test_missing_timestamp_fails_closed(isolated_phase: Path) -> None:
    _stamp({"phase": phase.ORIENT, "changed_utc": "not-a-date"}, isolated_phase)
    assert phase.current() == phase.PRIVATE


def test_naive_timestamp_is_treated_as_utc(isolated_phase: Path) -> None:
    naive = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    _stamp({"phase": phase.PUBLIC_REPLY, "changed_utc": naive}, isolated_phase)
    assert phase.current() == phase.PUBLIC_REPLY


# --- the model must not control the phase ---------------------------------

def test_reset_is_not_an_mcp_tool() -> None:
    """reset_for_new_wake is an operator escape hatch, not a model capability."""
    from tools import harness_mcp

    registered = set(harness_mcp.mcp._tool_manager._tools)
    for forbidden in ("reset_for_new_wake", "advance", "set_phase", "advance_phase"):
        assert forbidden not in registered, (
            f"'{forbidden}' is registered as a tool — the model could steer its "
            "own phase, which defeats the gate entirely."
        )


def test_phase_file_lives_outside_version_control(isolated_phase: Path) -> None:
    ignored = (Path(__file__).resolve().parent.parent / ".gitignore").read_text(
        encoding="utf-8"
    )
    assert f"{names.dir_name('memory')}/private/" in ignored


# --- wake identity: what per-wake limits key on ---------------------------

def test_no_wake_id_before_anything_is_recorded(isolated_phase: Path) -> None:
    assert phase.wake_id() == "no-wake-yet"


def test_wake_id_is_stable_across_advances_within_a_wake(isolated_phase: Path) -> None:
    phase.advance(phase.PUBLIC_REPLY)
    first = phase.wake_id()
    phase.advance(phase.PRIVATE)
    assert phase.wake_id() == first


def test_operator_reset_starts_a_new_wake(
    isolated_phase: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Windows clocks can return the same microsecond twice; step the clock so
    # the test checks the reset, not the timer resolution.
    from datetime import datetime, timedelta, timezone
    ticks = iter(datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc) + timedelta(seconds=i) for i in range(100))
    monkeypatch.setattr(phase, "_now", lambda: next(ticks))
    phase.advance(phase.PRIVATE)
    before = phase.wake_id()
    phase.reset_for_new_wake()
    assert phase.wake_id() != before


def test_operator_reset_does_not_clear_private_use_receipts(isolated_phase: Path) -> None:
    phase._record_private_use(CONVERSATION_A)
    before = list((
        isolated_phase / names.memory("private") / "private-use"
    ).glob("*/*.json"))
    phase.reset_for_new_wake()
    assert before and all(path.exists() for path in before)


def test_going_idle_starts_a_new_wake(isolated_phase: Path) -> None:
    old = datetime.now(timezone.utc) - phase.WAKE_IDLE_RESET - timedelta(minutes=1)
    _stamp({"phase": phase.PRIVATE, "changed_utc": old.isoformat(),
            "wake_started_utc": old.isoformat()}, isolated_phase)
    assert phase.wake_id() == "no-wake-yet"
    phase.advance(phase.PUBLIC_REPLY)
    assert phase.wake_id() != old.isoformat()


def test_phase_file_from_before_wake_ids_is_strict(isolated_phase: Path) -> None:
    """A legacy file yields one constant id, so a per-wake limit recorded
    against it keeps holding rather than looking like a fresh wake."""
    now = datetime.now(timezone.utc).isoformat()
    _stamp({"phase": phase.PRIVATE, "changed_utc": now}, isolated_phase)
    assert phase.wake_id() == "legacy-wake"


def test_unparseable_timestamp_does_not_start_a_new_wake(isolated_phase: Path) -> None:
    """Starting a new wake re-opens per-wake allowances, so a corrupt
    timestamp must not be read as staleness."""
    _stamp({"phase": phase.PUBLIC_REPLY, "changed_utc": "not-a-date",
            "wake_started_utc": "2026-09-10T00:00:00+00:00"}, isolated_phase)
    assert phase.wake_id() == "2026-09-10T00:00:00+00:00"


# --- invocation-bound private-use receipts -------------------------------

def test_hook_overwrites_spoof_and_adds_omitted_id() -> None:
    for args in ({"_conversationId": "spoofed"}, {}):
        result = session_context_hook.process({
            "conversationId": CONVERSATION_A,
            "toolCall": {"name": f"harness.{names.tool('read_correspondence')}", "args": args},
            "transcriptPath": "PRIVATE TRANSCRIPT MUST NOT BE COPIED",
        })
        assert result == {
            "decision": "allow",
            "overwrite": {"_conversationId": CONVERSATION_A},
        }
        assert "TRANSCRIPT" not in json.dumps(result)


@pytest.mark.parametrize("conversation_id", [None, "", "not-a-uuid"])
def test_hook_denies_every_matched_tool_without_valid_host_id(
    conversation_id: str | None,
) -> None:
    for tool_name in session_context_hook.TARGET_TOOLS:
        result = session_context_hook.process({
            "conversationId": conversation_id,
            "toolCall": {"name": tool_name, "args": {}},
        })
        assert result["decision"] == "deny", tool_name
        assert "overwrite" not in result


def test_hook_leaves_unmatched_tools_unchanged() -> None:
    assert session_context_hook.process({
        "toolCall": {"name": "city.say", "args": {"text": "hello"}},
    }) == {"decision": "allow"}
    assert session_context_hook.process({
        "toolCall": {"name": "call_mcp_tool", "args": {"ServerName": "city", "ToolName": "say", "Arguments": {}}},
    }) == {"decision": "allow"}


def test_hook_handles_call_mcp_tool_wrapper() -> None:
    result = session_context_hook.process({
        "conversationId": CONVERSATION_A,
        "toolCall": {
            "name": "call_mcp_tool",
            "args": {
                "ServerName": "harness",
                "ToolName": "wake_status",
                "Arguments": {},
            },
        },
    })
    assert result == {
        "decision": "allow",
        "overwrite": {
            "Arguments": {"_conversationId": CONVERSATION_A},
        },
    }

    # Preserves existing arguments and handles string-encoded Arguments
    result_with_args = session_context_hook.process({
        "conversationId": CONVERSATION_A,
        "toolCall": {
            "name": "call_mcp_tool",
            "args": {
                "ServerName": "harness",
                "ToolName": "read_correspondence",
                "Arguments": '{"counterpart": "operator"}',
            },
        },
    })
    assert result_with_args == {
        "decision": "allow",
        "overwrite": {
            "Arguments": {"counterpart": "operator", "_conversationId": CONVERSATION_A},
        },
    }


@pytest.mark.parametrize("conversation_id", [None, "", "not-a-uuid"])
def test_hook_denies_call_mcp_tool_without_valid_id(conversation_id: str | None) -> None:
    result = session_context_hook.process({
        "conversationId": conversation_id,
        "toolCall": {
            "name": "call_mcp_tool",
            "args": {
                "ServerName": "harness",
                "ToolName": "wake_status",
                "Arguments": {},
            },
        },
    })
    assert result["decision"] == "deny"
    assert "overwrite" not in result


def test_concurrent_private_calls_create_receipts_for_both_conversations(
    isolated_phase: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(phase, "current", lambda: phase.PRIVATE)

    @phase.enters_private
    def private_tool(conversation_id: str | None = None) -> str:
        return "private content"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda cid: private_tool(conversation_id=cid),
            (CONVERSATION_A, CONVERSATION_B),
        ))
    assert results == ["private content", "private content"]

    receipt_root = isolated_phase / names.memory("private") / "private-use"
    receipts = list(receipt_root.glob("*/*.json"))
    assert len(receipts) == 2
    recorded = {json.loads(path.read_text(encoding="utf-8"))["conversation_id"] for path in receipts}
    assert recorded == {CONVERSATION_A, CONVERSATION_B}


def test_private_tool_missing_identity_returns_no_private_content(
    isolated_phase: Path,
) -> None:
    called = False

    @phase.enters_private
    def private_tool(conversation_id: str | None = None) -> str:
        nonlocal called
        called = True
        return "secret"

    with pytest.raises(phase.PhaseError):
        private_tool()
    assert called is False


def test_same_conversation_stays_latched_after_cooldown(
    isolated_phase: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    start = datetime(2026, 9, 13, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(phase, "_now", lambda: start)
    phase._record_private_use(CONVERSATION_A)
    monkeypatch.setattr(phase, "_now", lambda: start + timedelta(hours=2))

    assert phase.unknown_email_status(CONVERSATION_A)["reason"] == "same_conversation_private"
    assert phase.unknown_email_status(CONVERSATION_B)["available"] is True


def test_different_conversation_obeys_latest_private_use_cooldown(
    isolated_phase: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    start = datetime(2026, 9, 13, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(phase, "_now", lambda: start)
    phase._record_private_use(CONVERSATION_A)
    monkeypatch.setattr(phase, "_now", lambda: start + timedelta(minutes=25))
    phase._record_private_use(CONVERSATION_A)
    monkeypatch.setattr(phase, "_now", lambda: start + timedelta(minutes=40))

    status = phase.unknown_email_status(CONVERSATION_B)
    assert status["available"] is False
    assert status["reason"] == "recent_private_use"
    assert status["reopens_utc"] == start + timedelta(minutes=55)


def test_malformed_receipt_fails_closed(
    isolated_phase: Path,
) -> None:
    folder = isolated_phase / names.memory("private") / "private-use" / CONVERSATION_A
    folder.mkdir(parents=True)
    (folder / "bad.json").write_text("{broken", encoding="utf-8")

    status = phase.unknown_email_status(CONVERSATION_B)
    assert status == {"available": False, "reason": "malformed_receipt"}


# --- memory readers: the ID labels the read log, never gates the read -------

def test_hook_adds_id_to_memory_readers_when_host_has_one() -> None:
    for tool_name in session_context_hook.METERED_TOOLS:
        assert session_context_hook.process({
            "conversationId": CONVERSATION_A,
            "toolCall": {"name": tool_name, "args": {}},
        }) == {"decision": "allow", "overwrite": {"_conversationId": CONVERSATION_A}}, tool_name


@pytest.mark.parametrize("conversation_id", [None, "", "not-a-uuid"])
def test_hook_never_denies_a_memory_reader(conversation_id: str | None) -> None:
    for tool_name in session_context_hook.METERED_TOOLS:
        assert session_context_hook.process({
            "conversationId": conversation_id,
            "toolCall": {"name": tool_name, "args": {}},
        }) == {"decision": "allow"}, tool_name
        server, tool = tool_name.split(".")
        assert session_context_hook.process({
            "conversationId": conversation_id,
            "toolCall": {"name": "call_mcp_tool", "args": {
                "ServerName": server, "ToolName": tool, "Arguments": {}}},
        }) == {"decision": "allow"}, tool_name


def test_hook_wrapper_adds_id_to_memory_readers() -> None:
    result = session_context_hook.process({
        "conversationId": CONVERSATION_A,
        "toolCall": {"name": "call_mcp_tool", "args": {
            "ServerName": "harness", "ToolName": "recall", "Arguments": {"tags": ["x"]}}},
    })
    assert result == {"decision": "allow", "overwrite": {
        "Arguments": {"tags": ["x"], "_conversationId": CONVERSATION_A}}}


def test_every_metered_reader_gets_the_host_id() -> None:
    # Brief 52 metered these readers, but the hook only bound the private
    # tools, so their read-log lines had no conversation (found S79).
    import inspect
    import re
    from tools import harness_mcp
    source = inspect.getsource(harness_mcp)
    metered = set(re.findall(r"@_meter_memory_read\n(?:@\w+\n)*def (\w+)", source))
    metered |= {"recall", "read_card", "read_wake_card"}
    bound = set(session_context_hook._METERED) | {
        function for server, function in session_context_hook._TARGETS if server == "harness"}
    assert metered - bound == set()
    for name in session_context_hook._METERED:
        function = inspect.unwrap(getattr(harness_mcp, name))
        assert "conversation_id" in inspect.signature(function).parameters, name


def test_every_tool_that_takes_the_host_id_is_given_it() -> None:
    # write_journal takes the ID for its "closed" line in the read log,
    # but the hook never supplied it: 153 closed lines had no conversation
    # (found 16 Nov). Any tool that asks for the ID must be on a hook list.
    import inspect
    from tools import harness_mcp
    bound = {name.split(".", 1)[1] for name in
             session_context_hook.METERED_TOOLS | session_context_hook.TARGET_TOOLS
             if name.startswith("harness.")}
    asking = {tool.name for tool in harness_mcp.mcp._tool_manager.list_tools()
              if "conversation_id" in inspect.signature(inspect.unwrap(tool.fn)).parameters}
    assert names.tool("write_journal") in asking
    assert asking - bound == set()
