"""Wake phase state. Monotonic, harness-owned, injection-resistant.

    orient  ──▶  public_reply  ──▶  private

    orient        Full public context. Everything committed to the public repo.
                  It is completely itself and completely oriented.
    public_reply  Replies to unknown senders. Private context not yet loaded,
                  so there is nothing private in context to leak into a reply.
    private       Drafts, correspondence, operator commands. From here on,
                  replies to unknown senders are refused.

WHY THIS SHAPE

The dangerous state is "public reply available while private context is
loaded" — that is the one combination in which a stranger's message can pull
private data out. Making the phase monotonic makes that state unreachable:
once a private tool has been touched the public reply window is closed, and it
cannot be reopened.

The reverse — advancing to private early, before replying to anyone — is safe.
It costs the agent the ability to answer strangers this wake and leaks nothing.
So private tools simply ADVANCE the phase rather than refusing; the refusal
lives on the public-reply side, where the actual risk is.

That asymmetry is deliberate. A design that refused private tools during
orient would have to be sequenced behind the mail tools that advance the
phase, and would break every wake until those existed.

The model is never trusted to declare its own phase. It can only move forward,
and every forward move costs it capability rather than granting any.
"""

from __future__ import annotations

import functools
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools import names

REPO_ROOT = Path(__file__).resolve().parent.parent

ORIENT = "orient"
PUBLIC_REPLY = "public_reply"
PRIVATE = "private"
PHASE_ORDER = (ORIENT, PUBLIC_REPLY, PRIVATE)

# A phase file older than this means a new wake has begun. A model trying to
# reset in order to reopen the reply window must wait this long, by which point
# its context is gone.
WAKE_IDLE_RESET = timedelta(minutes=30)


class PhaseError(PermissionError):
    """Raised when a tool is called in a phase that does not permit it."""


def normalize_conversation_id(value: object) -> str | None:
    """Return a canonical UUID supplied by the host, or None if it is invalid."""
    if not isinstance(value, str):
        return None
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError):
        return None


def _private_use_root() -> Path:
    return REPO_ROOT / names.memory("private") / "private-use"


def _record_private_use(conversation_id: str) -> datetime:
    from tools import stop_marker

    with stop_marker.stop_lock(REPO_ROOT):
        used_at = _write_private_use(conversation_id)
        try:
            _prune_private_use()
        except (PhaseError, OSError):
            # Pruning is housekeeping. A receipt it can't read still blocks
            # unknown email, as before; it must not also block this read.
            pass
        return used_at


def _write_private_use(conversation_id: str) -> datetime:
    used_at = _now()
    directory = _private_use_root() / conversation_id
    directory.mkdir(parents=True, exist_ok=True)
    unique = uuid.uuid4().hex
    stamp = used_at.strftime("%Y%m%dT%H%M%S.%fZ")
    receipt = directory / f"{stamp}-{unique}.json"
    temporary = directory / f".{stamp}-{unique}.tmp"
    payload = {
        "conversation_id": conversation_id,
        "private_used_utc": used_at.isoformat(),
    }
    try:
        temporary.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        if receipt.exists():
            raise FileExistsError(f"private-use receipt already exists: {receipt.name}")
        os.replace(temporary, receipt)
    finally:
        temporary.unlink(missing_ok=True)
    return used_at


def _prune_private_use() -> None:
    # Validate everything before removing anything. A permanent latest receipt
    # keeps the conversation blocked even after its timestamp files expire.
    receipts = _read_private_use_receipts()
    latest = {}
    for conversation_id, used_at in receipts:
        latest[conversation_id] = max(latest.get(conversation_id, used_at), used_at)
    for conversation_id, used_at in latest.items():
        directory = _private_use_root() / conversation_id
        target = directory / "latest.json"
        temporary = directory / f".{uuid.uuid4().hex}.tmp"
        try:
            temporary.write_text(json.dumps({"conversation_id": conversation_id,
                                            "private_used_utc": used_at.isoformat()}) + "\n",
                                 encoding="utf-8")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        for path in directory.glob("*.json"):
            if path != target:
                path.unlink(missing_ok=True)


def _read_private_use_receipts() -> list[tuple[str, datetime]]:
    root = _private_use_root()
    if not root.exists():
        return []

    receipts: list[tuple[str, datetime]] = []
    for path in root.rglob("*.json"):
        try:
            relative = path.relative_to(root)
            if len(relative.parts) != 2:
                raise ValueError("receipt is not directly inside a conversation directory")
            directory_id = normalize_conversation_id(relative.parts[0])
            data = json.loads(path.read_text(encoding="utf-8"))
            receipt_id = normalize_conversation_id(data.get("conversation_id"))
            if directory_id is None or receipt_id != directory_id:
                raise ValueError("receipt conversation ID is invalid")
            used_at = datetime.fromisoformat(data["private_used_utc"])
            if used_at.tzinfo is None:
                raise ValueError("receipt timestamp has no timezone")
            receipts.append((receipt_id, used_at.astimezone(timezone.utc)))
        except Exception as exc:
            raise PhaseError("Private-use receipts are malformed; unknown email is blocked.") from exc
    return receipts


def unknown_email_status(conversation_id: object) -> dict[str, object]:
    """Report whether this invocation may fetch or reply to unknown email."""
    canonical_id = normalize_conversation_id(conversation_id)
    if canonical_id is None:
        return {"available": False, "reason": "missing_conversation_id"}

    try:
        receipts = _read_private_use_receipts()
    except PhaseError:
        return {"available": False, "reason": "malformed_receipt"}

    if any(receipt_id == canonical_id for receipt_id, _ in receipts):
        latest = max(used_at for _, used_at in receipts)
        return {
            "available": False,
            "reason": "same_conversation_private",
            "last_private_utc": latest,
        }

    if receipts:
        latest = max(used_at for _, used_at in receipts)
        reopens = latest + WAKE_IDLE_RESET
        if _now() < reopens:
            return {
                "available": False,
                "reason": "recent_private_use",
                "last_private_utc": latest,
                "reopens_utc": reopens,
            }
        return {
            "available": True,
            "reason": "available",
            "last_private_utc": latest,
        }

    return {"available": True, "reason": "available", "last_private_utc": None}


def _phase_file() -> Path:
    """Resolved at call time so tests can repoint REPO_ROOT."""
    return REPO_ROOT / names.memory("private") / "wake_phase.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read() -> dict:
    path = _phase_file()
    if not path.exists():
        return {"phase": ORIENT, "changed_utc": _now().isoformat()}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "phase" not in data:
            raise ValueError("malformed phase file")
        return data
    except Exception:
        # Fail closed. PRIVATE is the restrictive direction: it withholds the
        # public reply window rather than granting it on a corrupt file.
        return {"phase": PRIVATE, "changed_utc": _now().isoformat()}


def current() -> str:
    """Current phase, auto-resetting to orient once a new wake has begun."""
    state = _read()
    try:
        changed = datetime.fromisoformat(state["changed_utc"])
    except Exception:
        return PRIVATE
    if changed.tzinfo is None:
        changed = changed.replace(tzinfo=timezone.utc)
    if _now() - changed > WAKE_IDLE_RESET:
        return ORIENT
    phase = state.get("phase", ORIENT)
    return phase if phase in PHASE_ORDER else PRIVATE


def _is_stale(state: dict) -> bool:
    """True if the recorded phase is older than WAKE_IDLE_RESET.

    An unparseable timestamp is NOT stale. That is the strict direction here:
    staleness starts a new wake, and a new wake re-opens per-wake allowances.
    """
    try:
        changed = datetime.fromisoformat(state["changed_utc"])
    except Exception:
        return False
    if changed.tzinfo is None:
        changed = changed.replace(tzinfo=timezone.utc)
    return _now() - changed > WAKE_IDLE_RESET


def _write(phase: str, *, new_wake: bool = False) -> None:
    """Record the phase, carrying the wake's start time forward.

    A new wake begins when there is no phase file, when the existing one has
    gone idle past WAKE_IDLE_RESET, or when the operator resets explicitly.
    Within a wake, wake_started_utc never changes — it is what per-wake limits
    elsewhere key on, so they share this module's idea of where a wake begins.
    """
    path = _phase_file()
    existed = path.exists()
    state = _read() if existed else {}
    now = _now().isoformat()
    started = state.get("wake_started_utc")
    if new_wake or not existed or not started or _is_stale(state):
        started = now
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"phase": phase, "changed_utc": now, "wake_started_utc": started}),
        encoding="utf-8",
    )


def wake_id() -> str:
    """Identifier for the current wake: the moment it began.

    Before anything has been recorded this wake there is no id yet. A phase
    file written before wake ids existed yields one constant value, so a
    per-wake limit is stricter, not looser, until the next write.
    """
    path = _phase_file()
    if not path.exists():
        return "no-wake-yet"
    state = _read()
    if _is_stale(state):
        return "no-wake-yet"
    return str(state.get("wake_started_utc") or "legacy-wake")


def advance(to: str) -> str:
    """Move the phase forward. Backward transitions raise."""
    if to not in PHASE_ORDER:
        raise PhaseError(f"Unknown phase: {to}")
    now = current()
    if PHASE_ORDER.index(to) < PHASE_ORDER.index(now):
        raise PhaseError(
            f"Phase is monotonic: cannot go back from '{now}' to '{to}'. "
            "Reopening the public reply window after private context has been "
            "loaded is exactly the state this prevents."
        )
    _write(to)
    return to


def reset_for_new_wake() -> str:
    """Operator escape hatch: force the phase back to orient.

    Needed when a wake dies mid-window and is rerun inside WAKE_IDLE_RESET.
    Without this the rerun inherits 'private' and silently cannot reply to
    strangers, which looks like a bug rather than a policy.

    Deliberately NOT an MCP tool — the model must never be able to call it.
    Also starts a new wake id, so per-wake reply allowances reset with it.
    """
    _write(ORIENT, new_wake=True)
    return ORIENT


def requires_phase(*allowed: str):
    """Guard a tool so it only runs in the listed phases.

    Apply BELOW @mcp.tool() so FastMCP wraps the guarded callable:

        @mcp.tool()
        @requires_phase(phase.PUBLIC_REPLY, phase.PRIVATE)
        def reply_to_message(...): ...
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            now = current()
            if now not in allowed:
                raise PhaseError(
                    f"{fn.__name__} is not available during '{now}'. "
                    f"Allowed: {', '.join(allowed)}."
                )
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def enters_private(fn):
    """Mark a tool whose use closes the public reply window.

    The AGY invocation hook supplies ``_conversationId``. Missing or malformed
    identity fails closed before the decorated function can return private
    context. Every call updates a permanent per-conversation receipt, even if
    the coarse phase is already private.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        supplied_id = kwargs.pop("_conversationId", None)
        if supplied_id is None:
            supplied_id = kwargs.pop("conversation_id", None)
        conversation_id = normalize_conversation_id(supplied_id)
        if conversation_id is None:
            raise PhaseError(
                f"{fn.__name__} requires a valid host-supplied conversation ID."
            )
        _record_private_use(conversation_id)
        if current() != PRIVATE:
            advance(PRIVATE)
        return fn(*args, **kwargs)
    return wrapper
