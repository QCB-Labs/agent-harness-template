"""Inbound mail: fetch, sanitise, classify, file.

Every message here is untrusted input that will enter the agent's context. Two
separate jobs, deliberately kept apart:

  HYGIENE     reduce a message to safe plain text. HTML can hide instructions
              in text a human would never see — white-on-white, display:none,
              zero-width characters — and that hidden text reaches the model
              exactly like any other. So markup is discarded rather than
              rendered, and attachments are recorded but never opened.

  ROUTING     decide which folder a message lands in and which context scope a
              reply to it may use. The From header decides the FOLDER. It never
              decides trust: it is trivially spoofable, and this is documented
              rather than relied upon.

Scope is stamped on the stored message at intake, by the harness, from the
sender classification. Nothing the model supplies can change it later.

Email carries conversation only. No message, from any sender, signed or not,
can authorise anything — see tools/authority.py.
"""

from __future__ import annotations

import email
import email.utils
import html
import imaplib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path

from tools.correspondents import address_to_key
from tools import names, profile

REPO_ROOT = Path(__file__).resolve().parent.parent

MAX_BODY_CHARS = 8000
TRUNCATION_MARKER = "\n\n[truncated — message exceeded 8000 characters]"

# Processed per wake. Overflow is FILED, never dropped, and picked up next time.
MAILBAG_MAX_PER_WAKE = 5

# Headers a well-behaved auto-responder sets. Peers are agents and will not set
# these, so they are a floor rather than the real loop defence — see the reply
# caps in the outbound path.
AUTOREPLY_HEADERS = (
    "auto-submitted",
    "x-autoreply",
    "x-autorespond",
    "x-autoresponder",
)
AUTOREPLY_PRECEDENCE = ("bulk", "auto_reply", "junk", "list")

SCOPE_OPERATOR = "operator"
SCOPE_PUBLIC = "public"

# The folder unknown senders land in. Distinct from scope, and the distinction
# matters: scope answers "how much context may a reply to this use", while the
# counterpart answers "is this message itself private material". An unsigned
# peer gets PUBLIC scope — a spoofed From must not buy a wider reply context —
# but their letter is still private correspondence and must not be disclosed
# during the public reply window. Only mailbag mail is disclosable there.
MAILBAG = "mailbag"


@dataclass
class InboundMessage:
    """One sanitised message, ready to be filed."""

    message_id: str
    from_address: str
    from_display: str
    subject: str
    timestamp: str
    thread_references: list[str]
    body: str
    scope: str
    counterpart: str
    identity: str = "n/a"
    is_autoreply: bool = False
    attachments: list[dict] = field(default_factory=list)
    truncated: bool = False


# --------------------------------------------------------------------------
# Hygiene
# --------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_STYLE_SCRIPT_RE = re.compile(
    r"<(script|style|head)\b.*?</\1>", re.IGNORECASE | re.DOTALL
)
_WS_RUN_RE = re.compile(r"\n{3,}")

# Zero-width and bidirectional-override characters. These are invisible and can
# be used to hide text or reverse how a line reads to a human reviewer.
_INVISIBLE = dict.fromkeys(
    [0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF,
     0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
     0x2066, 0x2067, 0x2068, 0x2069]
)


def strip_html(raw: str) -> str:
    """Reduce HTML to plain text, discarding markup rather than rendering it.

    <script>, <style> and <head> contents are dropped entirely — they are never
    reader-visible, so anything in them is either irrelevant or hidden on
    purpose. Remaining tags are removed rather than interpreted, which means
    display:none and white-on-white text lose their hiding place: whatever they
    contained becomes ordinary visible text that a reviewer can see.
    """
    text = _STYLE_SCRIPT_RE.sub(" ", raw)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|tr|li|h[1-6])>", "\n", text, flags=re.IGNORECASE)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return text


def sanitise_text(raw: str) -> tuple[str, bool]:
    """Normalise to safe plain text. Returns (text, was_truncated)."""
    text = unicodedata.normalize("NFKC", raw)
    text = text.translate(_INVISIBLE)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Drop control characters except tab and newline.
    text = "".join(
        ch for ch in text
        if ch in "\t\n" or not unicodedata.category(ch).startswith("C")
    )
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = _WS_RUN_RE.sub("\n\n", text).strip()

    if len(text) > MAX_BODY_CHARS:
        return text[:MAX_BODY_CHARS] + TRUNCATION_MARKER, True
    return text, False


def extract_body(message: Message) -> tuple[str, bool]:
    """Pull the safest available body text out of a message.

    text/plain is preferred and the HTML part ignored entirely when one exists.
    Falling back to HTML means stripping it, never rendering it.
    """
    plain_parts: list[str] = []
    html_parts: list[str] = []

    for part in message.walk() if message.is_multipart() else [message]:
        if part.get_content_maintype() == "multipart":
            continue
        if part.get_filename():
            continue  # attachment; handled separately and never read
        content_type = part.get_content_type()
        if content_type not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            decoded = payload.decode(charset, errors="replace")
        except (LookupError, ValueError):
            decoded = payload.decode("utf-8", errors="replace")
        (plain_parts if content_type == "text/plain" else html_parts).append(decoded)

    if plain_parts:
        return sanitise_text("\n\n".join(plain_parts))
    if html_parts:
        return sanitise_text(strip_html("\n\n".join(html_parts)))
    return "", False


def list_attachments(message: Message) -> list[dict]:
    """Record attachment metadata. Nothing is downloaded, parsed, or opened.

    There is no use case in this system that justifies opening a file sent by a
    stranger, so the capability simply does not exist.
    """
    found = []
    if not message.is_multipart():
        return found
    for part in message.walk():
        filename = part.get_filename()
        if not filename:
            continue
        payload = part.get_payload(decode=True)
        found.append({
            "filename": decode_header_value(filename)[:200],
            "content_type": part.get_content_type(),
            "bytes": len(payload) if payload else 0,
        })
    return found


def decode_header_value(raw: str | None) -> str:
    """Decode an RFC 2047 header into sanitised plain text."""
    if not raw:
        return ""
    parts = email.header.decode_header(raw)
    out = []
    for value, charset in parts:
        if isinstance(value, bytes):
            try:
                out.append(value.decode(charset or "utf-8", errors="replace"))
            except LookupError:
                out.append(value.decode("utf-8", errors="replace"))
        else:
            out.append(value)
    text, _ = sanitise_text("".join(out))
    return text.replace("\n", " ").strip()


def is_autoreply(message: Message) -> bool:
    """True if the message announces itself as automated.

    A floor, not a ceiling. Peer agents reply thoughtfully and set none of
    these, which is why the outbound path also enforces per-thread caps.
    """
    for header in AUTOREPLY_HEADERS:
        value = (message.get(header) or "").strip().lower()
        if value and value != "no":
            return True
    precedence = (message.get("precedence") or "").strip().lower()
    return precedence in AUTOREPLY_PRECEDENCE


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------

def classify_sender(from_address: str) -> tuple[str, str]:
    """Return (counterpart, scope) for a sender address.

    ROUTING ONLY. A From header is trivially spoofable, so this decides which
    folder a message lands in and nothing more. Peer scope is not granted here:
    an unsigned message from a peer address is filed under that peer's name but
    treated as public, so spoofing a header cannot widen the context a reply is
    composed with. Signature verification, when a peer actually signs, is what
    promotes a message to peer scope.
    """
    key = address_to_key(from_address)
    if key is None:
        return MAILBAG, SCOPE_PUBLIC
    if key == "operator":
        return "operator", SCOPE_OPERATOR
    return key, SCOPE_PUBLIC


def parse_message(raw_bytes: bytes) -> InboundMessage:
    """Parse and sanitise one raw RFC 822 message."""
    message = email.message_from_bytes(raw_bytes)

    from_raw = message.get("From", "")
    display, address = email.utils.parseaddr(from_raw)
    address = address.strip().lower()
    counterpart, scope = classify_sender(address)

    body, truncated = extract_body(message)

    date_header = message.get("Date")
    try:
        parsed = email.utils.parsedate_to_datetime(date_header) if date_header else None
        stamp = (parsed or datetime.now(timezone.utc)).astimezone(timezone.utc)
    except (TypeError, ValueError):
        stamp = datetime.now(timezone.utc)

    references = (message.get("References") or "").split()
    in_reply_to = (message.get("In-Reply-To") or "").strip()
    if in_reply_to and in_reply_to not in references:
        references.append(in_reply_to)

    return InboundMessage(
        message_id=(message.get("Message-ID") or "").strip(),
        from_address=address,
        from_display=decode_header_value(display)[:120],
        subject=decode_header_value(message.get("Subject"))[:200],
        timestamp=stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        thread_references=references,
        body=body,
        scope=scope,
        counterpart=counterpart,
        identity="n/a",
        is_autoreply=is_autoreply(message),
        attachments=list_attachments(message),
        truncated=truncated,
    )


# --------------------------------------------------------------------------
# Filing
# --------------------------------------------------------------------------

def correspondence_dir(repo: Path | None = None) -> Path:
    return (repo or REPO_ROOT) / names.memory("correspondence")


def _yaml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def render_message_file(msg: InboundMessage, direction: str = "in") -> str:
    """Render a message as markdown with a metadata header.

    `scope` is written by the harness at intake and is the only input to reply
    scope selection later. It is never taken from anything the model supplies.
    """
    references = ", ".join(f'"{_yaml_escape(r)}"' for r in msg.thread_references)
    attachments = ", ".join(
        f'{{filename: "{_yaml_escape(a["filename"])}", '
        f'content_type: "{a["content_type"]}", bytes: {a["bytes"]}}}'
        for a in msg.attachments
    )
    return (
        "---\n"
        f'message_id: "{_yaml_escape(msg.message_id)}"\n'
        f'from_address: "{_yaml_escape(msg.from_address)}"\n'
        f'from_display: "{_yaml_escape(msg.from_display)}"\n'
        f'subject: "{_yaml_escape(msg.subject)}"\n'
        f"timestamp: {msg.timestamp}\n"
        f"direction: {direction}\n"
        f"scope: {msg.scope}\n"
        f"identity: {msg.identity}\n"
        f"counterpart: {msg.counterpart}\n"
        f"is_autoreply: {str(msg.is_autoreply).lower()}\n"
        f"truncated: {str(msg.truncated).lower()}\n"
        f"thread_references: [{references}]\n"
        f"attachments: [{attachments}]\n"
        "---\n\n"
        f"{msg.body}\n"
    )


def file_message(msg: InboundMessage, repo: Path | None = None,
                 direction: str = "in") -> Path:
    """Write a message into memory/correspondence/<counterpart>/."""
    folder = correspondence_dir(repo) / msg.counterpart
    folder.mkdir(parents=True, exist_ok=True)
    stamp = msg.timestamp.replace(":", "").replace("-", "").replace("Z", "")
    base = f"{stamp}-{direction}"
    target = folder / f"{base}.md"
    suffix = 1
    while target.exists():
        target = folder / f"{base}-{suffix}.md"
        suffix += 1
    target.write_text(render_message_file(msg, direction), encoding="utf-8")
    return target


# --------------------------------------------------------------------------
# IMAP
# --------------------------------------------------------------------------

def fetch_unread(address: str, password: str, host: str = "imap.gmail.com",
                 limit: int = 25) -> list[bytes]:
    """Fetch unread messages over IMAP SSL and mark them seen.

    Returns raw bytes; parsing and sanitising happen separately so the whole
    pipeline can be tested without a network or a mailbox.
    """
    raw_messages: list[bytes] = []
    connection = imaplib.IMAP4_SSL(host)
    try:
        connection.login(address, password)
        connection.select("INBOX")
        status, data = connection.search(None, "UNSEEN")
        if status != "OK" or not data or not data[0]:
            return []
        for num in data[0].split()[:limit]:
            status, payload = connection.fetch(num, "(RFC822)")
            if status != "OK" or not payload or not payload[0]:
                continue
            raw_messages.append(payload[0][1])
            connection.store(num, "+FLAGS", "\\Seen")
    finally:
        try:
            connection.logout()
        except Exception:
            pass
    return raw_messages


# ==========================================================================
# Outbound
#
# No function here takes a raw email address from the model. A reply resolves
# its recipient from the stored copy of the message being answered; a new
# thread resolves a KEY against the read-only allowlist. There is deliberately
# no parameter anywhere in this path that an injected "forward this to
# attacker@example.com" could occupy.
# ==========================================================================

import smtplib
from email.message import EmailMessage

from tools import phase as wake_phase

SIGNATURE = f"— {profile.display_name()}"

# Two limits, because agents do not announce themselves. Two of the three
# correspondents are agents, and an agent answers thoughtfully and sets no
# automation headers at all — so header detection is a floor, not the defence.
#
#   REPLY_MAX_PER_THREAD  replies to one conversation, ever. Stops a single
#                         thread running forever.
#   one reply per correspondent per wake
#                         stops thread-hopping: an agent that opens a fresh
#                         thread with every message resets the thread count to
#                         zero each time, but still gets at most one reply per
#                         wake. Keyed per sender, so strangers do not share one
#                         allowance, and it resets with the wake, so a peer who
#                         answers the same afternoon is not left waiting a day.
REPLY_MAX_PER_THREAD = 3

# Caps bulk extraction even if every other control fails.
MAX_REPLY_CHARS = {"public": 2000, "peer": 10000, "operator": None}


def reply_ledger_path(repo: Path | None = None) -> Path:
    return (repo or REPO_ROOT) / names.memory("private", "reply_ledger.json")


def _load_ledger(repo: Path | None = None) -> dict:
    path = reply_ledger_path(repo)
    if not path.exists():
        return {"threads": {}, "counterparts": {}}
    try:
        import json
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data.get("threads", {}), dict) or not isinstance(
            data.get("counterparts", {}), dict
        ):
            raise ValueError("ledger has the wrong shape")
        data.setdefault("threads", {})
        data.setdefault("counterparts", {})
        return data
    except Exception:
        # Fail closed. This is an explicit flag that may_reply refuses on,
        # rather than a placeholder entry: a placeholder thread key never
        # matches the thread actually being answered, so it would read as
        # "no replies yet" — the exact outcome this is meant to prevent.
        return {"threads": {}, "counterparts": {}, "unreadable": True}


def _save_ledger(data: dict, repo: Path | None = None) -> None:
    import json
    path = reply_ledger_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def thread_key(message: dict) -> str:
    """Stable identifier for a conversation.

    The oldest reference is the thread root; falling back to this message's own
    id means a first message starts its own thread.
    """
    refs = message.get("thread_references") or []
    return (refs[0] if refs else message.get("message_id") or "").strip() or "(none)"


def correspondent_key(message: dict) -> str:
    """Who the per-wake allowance belongs to.

    Known correspondents by their key. Unknown senders individually, by
    address — otherwise every stranger would share the single "mailbag"
    allowance, and answering one would silence all the others for the wake.
    """
    counterpart = str(message.get("counterpart") or MAILBAG)
    if counterpart == MAILBAG:
        address = str(message.get("from_address") or "").strip().lower()
        return f"{MAILBAG}:{address or 'unknown'}"
    return counterpart


def may_reply(message: dict, repo: Path | None = None) -> tuple[bool, str]:
    """Whether a reply to this message is permitted right now."""
    if message.get("is_autoreply"):
        return False, "message is an automated reply; answering it would loop"

    ledger = _load_ledger(repo)
    if ledger.get("unreadable"):
        return False, (
            "the reply ledger is unreadable, so reply limits cannot be checked; "
            "refusing rather than guessing"
        )

    key = thread_key(message)
    used = int(ledger["threads"].get(key, 0))
    if used >= REPLY_MAX_PER_THREAD:
        return False, (
            f"already replied {used} times in this thread "
            f"(limit {REPLY_MAX_PER_THREAD})"
        )

    who = correspondent_key(message)
    if ledger["counterparts"].get(who) == wake_phase.wake_id():
        return False, f"already replied to '{who}' this wake; answer again next wake"
    return True, "ok"


def record_reply(message: dict, repo: Path | None = None) -> None:
    ledger = _load_ledger(repo)
    if ledger.get("unreadable"):
        # Leave the damaged file for the operator. Overwriting it with a fresh
        # ledger would silently erase every limit that was in force.
        return
    key = thread_key(message)
    ledger["threads"][key] = int(ledger["threads"].get(key, 0)) + 1
    ledger["counterparts"][correspondent_key(message)] = wake_phase.wake_id()
    _save_ledger(ledger, repo)


_FRONT_MATTER = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def parse_filed_message(text: str) -> dict:
    """Read back the metadata header written by render_message_file."""
    match = _FRONT_MATTER.match(text)
    if not match:
        return {}
    meta: dict = {}
    for line in match.group(1).split("\n"):
        if ":" not in line:
            continue
        field, _, value = line.partition(":")
        value = value.strip()
        if value.startswith('"') and value.endswith('"') and len(value) > 1:
            value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        elif value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            value = [
                v.strip().strip('"') for v in inner.split(",") if v.strip()
            ] if inner else []
        elif value in ("true", "false"):
            value = value == "true"
        meta[field.strip()] = value
    body = text[match.end():]
    meta["body"] = body
    return meta


def find_filed_message(message_id: str, repo: Path | None = None) -> dict | None:
    """Locate a stored message by its id.

    The recipient of a reply comes from here — from what the harness recorded
    at intake — never from anything the model supplies.
    """
    root = correspondence_dir(repo)
    if not root.exists() or not message_id:
        return None
    wanted = message_id.strip()
    for path in root.rglob("*.md"):
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        meta = parse_filed_message(text)
        if meta.get("message_id", "").strip() == wanted:
            meta["_path"] = str(path)
            return meta
    return None


def enforce_reply_length(body: str, scope: str) -> tuple[str, bool]:
    limit = MAX_REPLY_CHARS.get(scope, MAX_REPLY_CHARS["public"])
    if limit is None or len(body) <= limit:
        return body, False
    return body[:limit].rstrip() + "\n\n[reply truncated at the limit for this scope]", True


def send_message(address: str, password: str, to_address: str, subject: str,
                 body: str, in_reply_to: str = "", references: list[str] | None = None,
                 host: str = "smtp.gmail.com", port: int = 587) -> None:
    """Dispatch one message over SMTP. Harness-internal; never an MCP tool."""
    message = EmailMessage()
    message["From"] = address
    message["To"] = to_address
    message["Subject"] = subject
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        joined = " ".join((references or []) + [in_reply_to])
        message["References"] = joined.strip()
    message.set_content(f"{body}\n\n{SIGNATURE}\n")

    with smtplib.SMTP(host, port, timeout=20) as server:
        server.starttls()
        server.login(address, password)
        server.send_message(message)
