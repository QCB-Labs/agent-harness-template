"""Outbound mail: the address never comes from the model.

The property under test is negative. There is no route by which an injected
"forward this to attacker@example.com" reaches a recipient field:

  reply_to_message   resolves the address from what the harness recorded at
                     intake, from the stored copy of the message being answered
  send_new_thread    resolves a KEY against the read-only allowlist

Neither takes an address. That is the whole defence, and it holds at the tool
signature rather than in the prompt.
"""

from __future__ import annotations

import datetime
import email.message
import inspect
import json
from pathlib import Path

import pytest

from tools import names
from tools import harness_mcp, mail, phase

CONVERSATION_ID = "11111111-1111-4111-8111-111111111111"


def _reply_to_message(message_id: str, body: str) -> str:
    return harness_mcp.reply_to_message(
        message_id, body, conversation_id=CONVERSATION_ID
    )


@pytest.fixture(autouse=True)
def known_correspondents(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPERATOR_CORRESPONDENTS", json.dumps({
        "operator": "operator@example.com",
        "peer": "peer@example.com",
    }))


def build(body: str, *, subject: str = "Hello", sender: str = "s@example.com",
          headers: dict | None = None) -> bytes:
    msg = email.message.EmailMessage()
    msg["From"] = sender
    msg["Subject"] = subject
    msg["Message-ID"] = f"<{abs(hash((body, sender)))}@example.com>"
    for key, value in (headers or {}).items():
        msg[key] = value
    msg.set_content(body)
    return msg.as_bytes()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(harness_mcp, "REPO", tmp_path)
    monkeypatch.setattr(phase, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        harness_mcp, "get_agent_email", lambda: ("bot@example.com", "pw")
    )
    return tmp_path


@pytest.fixture
def outbox(repo, monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """SMTP stubbed. Returns the list of dispatched messages."""
    sent: list[dict] = []

    def capture(address, password, *, to_address, subject, body,
                in_reply_to="", references=None, **kw):
        sent.append({
            "to_address": to_address, "subject": subject, "body": body,
            "in_reply_to": in_reply_to, "references": references or [],
        })

    monkeypatch.setattr(harness_mcp.mail_module, "send_message", capture)
    return sent


def file_incoming(repo: Path, sender: str = "stranger@example.com",
                  body: str = "a question", headers: dict | None = None) -> str:
    parsed = mail.parse_message(build(body, sender=sender, headers=headers))
    mail.file_message(parsed, repo=repo)
    return parsed.message_id


# --- the negative property ------------------------------------------------

def test_no_outbound_tool_accepts_an_address() -> None:
    """The structural guarantee: no parameter an attacker's address can occupy."""
    for name in ("reply_to_message", "send_new_thread"):
        fn = harness_mcp.mcp._tool_manager._tools[name].fn
        params = set(inspect.signature(fn).parameters)
        assert not (params & {"to", "to_address", "recipient", "address", "email"})


def test_reply_address_comes_from_stored_metadata(repo, outbox) -> None:
    message_id = file_incoming(repo, sender="stranger@example.com")
    harness_mcp.advance_phase(harness_mcp.PUBLIC_REPLY)

    assert _reply_to_message(message_id, "an answer") == "sent"
    assert outbox[0]["to_address"] == "stranger@example.com"
    assert outbox[0]["in_reply_to"] == message_id


def test_reply_to_unknown_message_id_refuses(repo, outbox) -> None:
    harness_mcp.advance_phase(harness_mcp.PUBLIC_REPLY)
    assert _reply_to_message("<nope@x>", "hello").startswith("REFUSED")
    assert outbox == []


# --- ordering -------------------------------------------------------------

def test_public_reply_refused_once_private_context_is_loaded(repo, outbox, monkeypatch) -> None:
    """Answering a stranger after private context loads is the single ordering
    this design exists to prevent."""
    message_id = file_incoming(repo)

    @phase.enters_private
    def load_private(conversation_id: str | None = None) -> str:
        return "private"

    start = datetime.datetime(2026, 9, 13, 10, 0, tzinfo=datetime.timezone.utc)
    monkeypatch.setattr(phase, "_now", lambda: start)
    load_private(conversation_id=CONVERSATION_ID)
    monkeypatch.setattr(phase, "_now", lambda: start + datetime.timedelta(hours=2))

    assert "private context was loaded in this conversation" in _reply_to_message(
        message_id, "late answer"
    )
    assert outbox == []


def test_peer_reply_is_allowed_after_private_context_loads(repo, outbox) -> None:
    """A peer's letter is withheld until the private phase, so the reply must be
    possible then. Safe because the reply goes to the stored From address:
    spoofing a peer's address sends the answer to the real peer, not the spoofer."""
    message_id = file_incoming(repo, sender="peer@example.com", body="a letter")
    phase._record_private_use(CONVERSATION_ID)

    assert _reply_to_message(message_id, "a reply") == "sent"
    assert outbox[0]["to_address"] == "peer@example.com"


def test_unknown_reply_obeys_cross_conversation_cooldown(repo, outbox, monkeypatch) -> None:
    other = "22222222-2222-4222-8222-222222222222"
    start = datetime.datetime(2026, 9, 13, 10, 0, tzinfo=datetime.timezone.utc)
    monkeypatch.setattr(phase, "_now", lambda: start)
    phase._record_private_use(CONVERSATION_ID)
    message_id = file_incoming(repo)

    monkeypatch.setattr(phase, "_now", lambda: start + datetime.timedelta(minutes=29))
    blocked = harness_mcp.reply_to_message(
        message_id, "too soon", conversation_id=other
    )
    assert "another conversation" in blocked
    assert outbox == []

    monkeypatch.setattr(phase, "_now", lambda: start + datetime.timedelta(minutes=31))
    assert harness_mcp.reply_to_message(
        message_id, "now safe", conversation_id=other
    ) == "sent"


def test_unknown_reply_missing_identity_fails_closed(repo, outbox) -> None:
    message_id = file_incoming(repo)
    result = harness_mcp.reply_to_message(message_id, "must not send")
    assert "host-supplied conversation ID" in result
    assert outbox == []


def test_unknown_reply_malformed_receipt_fails_closed(repo, outbox) -> None:
    folder = repo / names.memory("private") / "private-use" / CONVERSATION_ID
    folder.mkdir(parents=True)
    (folder / "bad.json").write_text("{broken", encoding="utf-8")
    message_id = file_incoming(repo)
    result = _reply_to_message(message_id, "must not send")
    assert "private-use record is invalid" in result
    assert outbox == []


def test_send_new_thread_is_private_phase_only(repo, outbox) -> None:
    harness_mcp.advance_phase(harness_mcp.PUBLIC_REPLY)
    with pytest.raises(phase.PhaseError):
        harness_mcp.send_new_thread("peer", "subject", "body")


# --- loop protection ------------------------------------------------------

def test_autoreply_is_never_answered(repo, outbox) -> None:
    message_id = file_incoming(repo, headers={"Auto-Submitted": "auto-replied"})
    harness_mcp.advance_phase(harness_mcp.PUBLIC_REPLY)

    assert "loop" in _reply_to_message(message_id, "hi")
    assert outbox == []


def test_thread_reply_cap_is_enforced(repo, outbox) -> None:
    """Applies to peers as much as strangers. Peer agents answer thoughtfully
    and set no automation headers, so this cap is the real loop defence."""
    message_id = file_incoming(repo)
    stored = mail.find_filed_message(message_id, repo=repo)
    ledger = mail._load_ledger(repo)
    ledger["threads"][mail.thread_key(stored)] = mail.REPLY_MAX_PER_THREAD
    mail._save_ledger(ledger, repo)
    harness_mcp.advance_phase(harness_mcp.PUBLIC_REPLY)

    assert "already replied" in _reply_to_message(message_id, "more")
    assert outbox == []


def test_second_reply_to_the_same_sender_in_one_wake_is_refused(repo, outbox) -> None:
    """Stops thread-hopping: each message below is its own thread, so the
    per-thread cap never fires — the per-wake allowance is what holds."""
    first = file_incoming(repo, sender="a@example.com", body="one")
    second = file_incoming(repo, sender="a@example.com", body="two")
    harness_mcp.advance_phase(harness_mcp.PUBLIC_REPLY)

    assert _reply_to_message(first, "answer one") == "sent"
    assert "this wake" in _reply_to_message(second, "answer two")
    assert len(outbox) == 1


def test_different_strangers_each_get_a_reply_in_the_same_wake(repo, outbox) -> None:
    """Strangers do not share one allowance. Answering one must not silence
    every other unknown sender for the rest of the wake."""
    first = file_incoming(repo, sender="a@example.com", body="one")
    second = file_incoming(repo, sender="b@example.com", body="two")
    harness_mcp.advance_phase(harness_mcp.PUBLIC_REPLY)

    assert _reply_to_message(first, "answer one") == "sent"
    assert _reply_to_message(second, "answer two") == "sent"
    assert len(outbox) == 2


def test_the_allowance_returns_in_the_next_wake(repo, outbox) -> None:
    """A peer who answers the same afternoon waits one wake, not a day."""
    first = file_incoming(repo, sender="a@example.com", body="one")
    second = file_incoming(repo, sender="a@example.com", body="two")
    harness_mcp.advance_phase(harness_mcp.PUBLIC_REPLY)
    assert _reply_to_message(first, "answer one") == "sent"

    phase.reset_for_new_wake()
    harness_mcp.advance_phase(harness_mcp.PUBLIC_REPLY)
    assert _reply_to_message(second, "answer two") == "sent"
    assert len(outbox) == 2


def test_a_peer_gets_one_reply_per_wake(repo, outbox) -> None:
    first = file_incoming(repo, sender="peer@example.com", body="letter one")
    second = file_incoming(repo, sender="peer@example.com", body="letter two")
    harness_mcp.advance_phase(harness_mcp.PUBLIC_REPLY)

    assert _reply_to_message(first, "reply one") == "sent"
    assert "already replied to 'peer' this wake" in _reply_to_message(
        second, "reply two"
    )
    assert len(outbox) == 1


def test_unreadable_ledger_refuses_to_reply(repo, outbox) -> None:
    """A corrupt ledger must refuse, not read as 'no replies yet'."""
    message_id = file_incoming(repo)
    path = mail.reply_ledger_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{corrupt", encoding="utf-8")
    harness_mcp.advance_phase(harness_mcp.PUBLIC_REPLY)

    assert "unreadable" in _reply_to_message(message_id, "hello")
    assert outbox == []
    assert path.read_text(encoding="utf-8") == "{corrupt", (
        "the damaged ledger must be left for the operator, not silently replaced"
    )


def test_wrong_shaped_ledger_also_refuses(repo, outbox) -> None:
    message_id = file_incoming(repo)
    path = mail.reply_ledger_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"threads": [], "counterparts": "nope"}), encoding="utf-8")
    harness_mcp.advance_phase(harness_mcp.PUBLIC_REPLY)

    assert "unreadable" in _reply_to_message(message_id, "hello")
    assert outbox == []


# --- length caps ----------------------------------------------------------

def test_public_reply_is_capped(repo, outbox) -> None:
    message_id = file_incoming(repo)
    harness_mcp.advance_phase(harness_mcp.PUBLIC_REPLY)

    assert "truncated" in _reply_to_message(message_id, "x" * 9000)
    assert len(outbox[0]["body"]) <= mail.MAX_REPLY_CHARS["public"] + 80


def test_operator_replies_are_uncapped(repo, outbox) -> None:
    message_id = file_incoming(repo, sender="operator@example.com")
    harness_mcp.advance_phase(harness_mcp.PRIVATE)

    _reply_to_message(message_id, "y" * 9000)
    assert len(outbox[0]["body"]) >= 9000


# --- allowlist ------------------------------------------------------------

def test_send_new_thread_resolves_a_key(repo, outbox) -> None:
    harness_mcp.advance_phase(harness_mcp.PRIVATE)
    assert harness_mcp.send_new_thread("peer", "hello", "letter").startswith("sent")
    assert outbox[0]["to_address"] == "peer@example.com"


def test_send_new_thread_refuses_an_unknown_key(repo, outbox) -> None:
    harness_mcp.advance_phase(harness_mcp.PRIVATE)
    assert harness_mcp.send_new_thread("attacker", "hi", "body").startswith("REFUSED")
    assert outbox == []


def test_request_new_correspondent_does_not_widen_the_allowlist(repo) -> None:
    from tools import correspondents

    harness_mcp.advance_phase(harness_mcp.PRIVATE)
    before = set(correspondents.load_correspondents())
    result = harness_mcp.request_new_correspondent(
        "Wanderer", "wanderer@example.com", "peer agent worth writing to"
    )
    assert result["allowlist"] == "unchanged"
    assert set(correspondents.load_correspondents()) == before
    assert not correspondents.is_approved("wanderer")


# --- recording and failure ------------------------------------------------

def test_sent_reply_is_filed_as_outgoing(repo, outbox) -> None:
    message_id = file_incoming(repo)
    harness_mcp.advance_phase(harness_mcp.PUBLIC_REPLY)
    _reply_to_message(message_id, "an answer")

    outgoing = [
        path for path in (repo / names.memory("correspondence") / "mailbag").glob("*.md")
        if "direction: out" in path.read_text(encoding="utf-8")
    ]
    assert len(outgoing) == 1


def test_send_failure_does_not_leak_credentials(repo, monkeypatch) -> None:
    def boom(address, password, **kw):
        raise RuntimeError(f"auth failed for {address} / {password}")

    monkeypatch.setattr(harness_mcp.mail_module, "send_message", boom)
    message_id = file_incoming(repo)
    harness_mcp.advance_phase(harness_mcp.PUBLIC_REPLY)

    result = _reply_to_message(message_id, "hello")
    assert result.startswith("FAILED")
    assert "bot@example.com" not in result and "pw" not in result
