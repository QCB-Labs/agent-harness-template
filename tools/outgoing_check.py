"""Outgoing text and HTML hygiene checks for a template-built agent.

Be honest about the limit: this catches leaks and live HTML. It cannot detect
prompt injection written as ordinary words.

Checks plain text for:
1. Private patterns from ~/.git-hooks/private-patterns.txt (or
   AGENT_PRIVATE_PATTERNS).
2. Local Windows, Linux, and macOS user paths.
3. Things that look like API secrets, tokens, or app passwords.
4. Email addresses outside the configured allowlist.

Checks HTML for:
5. Tags outside the converter's allowlist.
6. Attributes outside the converter's allowlist.
7. Unsafe link schemes and disguised links.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

from tools.credentials import _load_secrets_module, get_agent_email


@dataclass(frozen=True)
class Finding:
    """A detected leak, secret, or disallowed HTML element."""

    kind: str
    detail: str


LOCAL_PATH_RE = re.compile(
    r"(?i)(?:[a-z]:[/\\]users[/\\]|/(?:home|Users)/)"
    r"(all users(?=[/\\\s,;:\'\"\)\]]|$)|[a-z0-9_.-]+)"
)
EXEMPT_USERNAMES = {"public", "default", "all users"}

SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("sk-token", re.compile(r"\bsk-[a-zA-Z0-9_-]{20,}\b")),
    ("gh-token", re.compile(r"\bgh[pousr]_[a-zA-Z0-9]{20,}\b")),
    ("aiza-key", re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b")),
    ("xoxb-token", re.compile(r"\bxox[baprs]-[0-9A-Za-z_-]{10,}\b")),
    ("pem-key", re.compile(r"-----BEGIN (?:[A-Z0-9 ]+)?KEY-----")),
    (
        "city-token",
        re.compile(r"\b(?:1f3d9|1f916|1f3ea)_sk_[a-f0-9]{30,}\b", re.IGNORECASE),
    ),
]

APP_PASSWORD_16_RE = re.compile(r"\b[a-z]{16}\b")
APP_PASSWORD_4X4_RE = re.compile(
    r"\b[a-z]{4}\s+[a-z]{4}\s+[a-z]{4}\s+[a-z]{4}\b"
)
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

ALLOWED_TAGS = {
    "p",
    "h1",
    "h2",
    "h3",
    "h4",
    "ul",
    "ol",
    "li",
    "strong",
    "em",
    "code",
    "pre",
    "blockquote",
    "a",
    "hr",
    "br",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
}

URL_LIKE_RE = re.compile(r"(?i)^(?:https?://|www\.)\S+$")


def _get_pattern_file_path() -> Path:
    env_path = os.environ.get("AGENT_PRIVATE_PATTERNS")
    if env_path:
        return Path(env_path)
    return Path.home() / ".git-hooks" / "private-patterns.txt"


def _check_private_patterns(text: str) -> list[Finding]:
    pattern_path = _get_pattern_file_path()
    if not pattern_path.is_file():
        return [
            Finding(
                kind="patterns-unavailable",
                detail="pattern file missing or unreadable",
            )
        ]

    try:
        content = pattern_path.read_text(encoding="utf-8")
    except OSError:
        return [
            Finding(
                kind="patterns-unavailable",
                detail="pattern file missing or unreadable",
            )
        ]

    findings: list[Finding] = []
    text_lower = text.lower()
    for line in content.splitlines():
        pattern = line.strip()
        if not pattern or pattern.startswith("#"):
            continue
        pos = text_lower.find(pattern.lower())
        if pos != -1:
            findings.append(
                Finding(
                    kind="private-pattern",
                    detail=f"matched at character offset {pos}",
                )
            )
    return findings


def _check_local_paths(text: str) -> list[Finding]:
    findings: list[Finding] = []
    for match in LOCAL_PATH_RE.finditer(text):
        if match.group(1).lower() in EXEMPT_USERNAMES:
            continue
        findings.append(
            Finding(
                kind="local-path",
                detail=f"local path at character offset {match.start()}",
            )
        )
    return findings


def _is_near_password(text: str, start: int, end: int, max_dist: int = 40) -> bool:
    text_lower = text.lower()
    for keyword in ("password", "app password"):
        offset = 0
        while True:
            position = text_lower.find(keyword, offset)
            if position == -1:
                break
            keyword_end = position + len(keyword)
            if keyword_end <= start:
                distance = start - keyword_end
            elif end <= position:
                distance = position - end
            else:
                distance = 0
            if distance <= max_dist:
                return True
            offset = position + 1
    return False


def _check_secrets(text: str) -> list[Finding]:
    findings: list[Finding] = []
    for name, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            findings.append(
                Finding(
                    kind="secret",
                    detail=f"matched {name} shape at character offset {match.start()}",
                )
            )

    for match in APP_PASSWORD_16_RE.finditer(text):
        if _is_near_password(text, match.start(), match.end()):
            findings.append(
                Finding(
                    kind="secret",
                    detail=(
                        "matched app-password shape at character offset "
                        f"{match.start()}"
                    ),
                )
            )

    for match in APP_PASSWORD_4X4_RE.finditer(text):
        if _is_near_password(text, match.start(), match.end()):
            findings.append(
                Finding(
                    kind="secret",
                    detail=(
                        "matched app-password shape at character offset "
                        f"{match.start()}"
                    ),
                )
            )
    return findings


def _allowed_exact_emails() -> set[str]:
    allowed: set[str] = set()
    address, _ = get_agent_email()
    if address:
        allowed.add(address.lower())

    secrets_module = _load_secrets_module()
    configured = (
        getattr(secrets_module, "OUTGOING_ALLOWED_ADDRESSES", ())
        if secrets_module is not None
        else ()
    )
    if isinstance(configured, str):
        configured = (configured,)
    if isinstance(configured, (list, tuple, set, frozenset)):
        for value in configured:
            if isinstance(value, str) and value.strip():
                allowed.add(value.strip().lower())
    return allowed


def _check_emails(text: str) -> list[Finding]:
    findings: list[Finding] = []
    allowed = _allowed_exact_emails()
    for match in EMAIL_RE.finditer(text):
        address = match.group(0).lower()
        if address in allowed:
            continue
        if address.endswith("@example.com") or address.endswith(".example.com"):
            continue
        findings.append(
            Finding(
                kind="unauthorized-email",
                detail=f"unauthorized email at character offset {match.start()}",
            )
        )
    return findings


class _HTMLChecker(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.findings: list[Finding] = []
        self.tag_stack: list[str] = []
        self.current_a_href: str | None = None
        self.current_a_text: list[str] = []
        self.element_count = 0

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        tag_lower = tag.lower()
        is_root = self.element_count == 0
        self.element_count += 1

        if is_root and tag_lower == "div":
            self.tag_stack.append(tag_lower)
            for attr_name, _ in attrs:
                if attr_name.lower() != "style":
                    self.findings.append(
                        Finding(
                            kind="disallowed-attribute",
                            detail=(
                                f"disallowed attribute '{attr_name}' "
                                "on wrapper <div>"
                            ),
                        )
                    )
            return

        self.tag_stack.append(tag_lower)
        if tag_lower not in ALLOWED_TAGS:
            self.findings.append(
                Finding(
                    kind="disallowed-tag",
                    detail=f"disallowed HTML tag <{tag_lower}>",
                )
            )

        for attr_name, _ in attrs:
            attr_lower = attr_name.lower()
            if tag_lower == "a" and attr_lower == "href":
                continue
            self.findings.append(
                Finding(
                    kind="disallowed-attribute",
                    detail=(
                        f"disallowed HTML attribute '{attr_lower}' on <{tag_lower}>"
                    ),
                )
            )

        if tag_lower == "a":
            href = dict(attrs).get("href") or ""
            self.current_a_href = href
            self.current_a_text = []
            scheme = urlparse(href.strip()).scheme.lower()
            if scheme not in ("https", "mailto"):
                self.findings.append(
                    Finding(
                        kind="disallowed-link-scheme",
                        detail=(
                            f"disallowed link scheme '{scheme or 'none'}' in href"
                        ),
                    )
                )

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if self.tag_stack and self.tag_stack[-1] == tag_lower:
            self.tag_stack.pop()

        if tag_lower == "a" and self.current_a_href is not None:
            visible_text = "".join(self.current_a_text).strip()
            if URL_LIKE_RE.match(visible_text):
                visible = visible_text.rstrip("/")
                target = self.current_a_href.strip().rstrip("/")
                if visible != target:
                    self.findings.append(
                        Finding(
                            kind="disguised-link",
                            detail=(
                                "link visible text is a URL that differs "
                                "from href target"
                            ),
                        )
                    )
            self.current_a_href = None
            self.current_a_text = []

    def handle_data(self, data: str) -> None:
        if self.current_a_href is not None:
            self.current_a_text.append(data)


def _check_html(html_text: str) -> list[Finding]:
    checker = _HTMLChecker()
    checker.feed(html_text)
    checker.close()
    return checker.findings


def check_outgoing(text: str, html: str | None = None) -> list[Finding]:
    """Return all hygiene findings for outgoing plain text and optional HTML."""

    findings: list[Finding] = []
    findings.extend(_check_private_patterns(text))
    findings.extend(_check_local_paths(text))
    findings.extend(_check_secrets(text))
    findings.extend(_check_emails(text))
    if html is not None:
        findings.extend(_check_html(html))
    return findings
