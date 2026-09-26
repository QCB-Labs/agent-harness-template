"""Tests for tools.outgoing_check."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import outgoing_check
from tools.outgoing_check import check_outgoing

WINDOWS_USERS_ROOT = "C:" + "\\Users"


@pytest.fixture
def fake_patterns_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    pattern_file = tmp_path / "private-patterns.txt"
    pattern_file.write_text(
        "# Comment line\n\nPrivateMarkerAlpha\nsecret-agent-marker\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_PRIVATE_PATTERNS", str(pattern_file))
    monkeypatch.setattr(outgoing_check, "get_agent_email", lambda: ("", ""))
    monkeypatch.setattr(outgoing_check, "_load_secrets_module", lambda: None)
    return pattern_file


def test_check_private_pattern_detected(fake_patterns_file: Path) -> None:
    findings = check_outgoing("The value PrivateMarkerAlpha must stay private.")
    assert "private-pattern" in [finding.kind for finding in findings]
    for finding in findings:
        if finding.kind == "private-pattern":
            assert "PrivateMarkerAlpha" not in finding.detail
            assert "offset" in finding.detail


def test_check_private_pattern_case_insensitive(
    fake_patterns_file: Path,
) -> None:
    findings = check_outgoing("The value privatemarkeralpha is lowercase.")
    assert any(finding.kind == "private-pattern" for finding in findings)


def test_check_missing_pattern_file_returns_patterns_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "AGENT_PRIVATE_PATTERNS", str(tmp_path / "missing-patterns.txt")
    )
    monkeypatch.setattr(outgoing_check, "get_agent_email", lambda: ("", ""))
    monkeypatch.setattr(outgoing_check, "_load_secrets_module", lambda: None)
    findings = check_outgoing("Normal text here.")
    assert len(findings) == 1
    assert findings[0].kind == "patterns-unavailable"


@pytest.mark.parametrize(
    "path_sample",
    [
        rf"Found file at {WINDOWS_USERS_ROOT}\SomeName\Documents\world.txt",
        r"Found file at C:/Users/SomeName/Documents/world.txt",
        r"Look at D:\Users\SomeName\data.json",
        "/home/SomeName/workspace/project",
        "/Users/SomeName/Desktop/notes.txt",
    ],
)
def test_check_local_path_detected(
    fake_patterns_file: Path, path_sample: str
) -> None:
    findings = check_outgoing(f"Prefix {path_sample} suffix")
    assert "local-path" in [finding.kind for finding in findings]


def test_local_path_exemptions(fake_patterns_file: Path) -> None:
    samples = (
        "Path is C:/Users/Public/documents/log.txt",
        rf"Path is {WINDOWS_USERS_ROOT}\Public\x",
        rf"Path is {WINDOWS_USERS_ROOT}\Default\config.json",
        rf"Path is {WINDOWS_USERS_ROOT}\All Users\data",
    )
    for sample in samples:
        assert not any(
            finding.kind == "local-path" for finding in check_outgoing(sample)
        )

    for sample in (
        "Path is C:/Users/SomeName/x",
        rf"Path is {WINDOWS_USERS_ROOT}\SomeName\secret.txt",
    ):
        assert any(
            finding.kind == "local-path" for finding in check_outgoing(sample)
        )


@pytest.mark.parametrize(
    "secret_sample",
    [
        "sk-1234567890abcdef1234567890abcdef",
        "ghp_1234567890abcdefghijklmnopqrstuvwxyz",
        "AIzaSyD1234567890123456789012345678901",
        "xoxb-1234567890-abcdef123456",
        "-----BEGIN " + "PRIVATE KEY-----",
        "-----BEGIN RSA " + "PRIVATE KEY-----",
        "1f3d9" + "_sk_abcdef1234567890abcdef1234567890",
    ],
)
def test_check_secret_detected(
    fake_patterns_file: Path, secret_sample: str
) -> None:
    findings = check_outgoing(f"My token is {secret_sample}; keep it safe.")
    assert "secret" in [finding.kind for finding in findings]


def test_planted_app_password_near_password_caught(
    fake_patterns_file: Path,
) -> None:
    samples = (
        "My app password is abcdefghijklmnop for the account.",
        "Here is the app password: abcd efgh ijkl mnop; use it.",
    )
    for sample in samples:
        assert any(
            finding.kind == "secret" for finding in check_outgoing(sample)
        )


def test_sixteen_letter_english_words_not_caught(
    fake_patterns_file: Path,
) -> None:
    for text in (
        "We share responsibilities here.",
        "It was incomprehensible to the whole group.",
    ):
        assert not any(
            finding.kind == "secret" for finding in check_outgoing(text)
        )


def test_four_by_four_words_without_password_not_caught(
    fake_patterns_file: Path,
) -> None:
    findings = check_outgoing("They went over that hill near some tree.")
    assert not any(finding.kind == "secret" for finding in findings)


def test_example_address_is_allowed(fake_patterns_file: Path) -> None:
    findings = check_outgoing(
        "Contact peer@example.com or operator@test.example.com."
    )
    assert not any(
        finding.kind == "unauthorized-email" for finding in findings
    )


def test_agent_mailbox_address_is_allowed(
    fake_patterns_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        outgoing_check,
        "get_agent_email",
        lambda: ("mailbox@agent.invalid", "placeholder"),
    )
    findings = check_outgoing("Contact mailbox@agent.invalid.")
    assert not any(
        finding.kind == "unauthorized-email" for finding in findings
    )


def test_configured_address_is_allowed(
    fake_patterns_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        outgoing_check,
        "_load_secrets_module",
        lambda: SimpleNamespace(
            OUTGOING_ALLOWED_ADDRESSES=["approved@trusted.invalid"]
        ),
    )
    findings = check_outgoing("Contact approved@trusted.invalid.")
    assert not any(
        finding.kind == "unauthorized-email" for finding in findings
    )


def test_unlisted_address_is_a_finding(fake_patterns_file: Path) -> None:
    findings = check_outgoing("Contact unlisted@outside.invalid.")
    assert "unauthorized-email" in [
        finding.kind for finding in findings
    ]


@pytest.mark.parametrize(
    "bad_tag",
    [
        '<img src="https://example.com/track.png">',
        "<script>alert(1)</script>",
        "<style>body { display: none; }</style>",
        '<iframe src="https://example.com"></iframe>',
        '<form action="/"><input type="text"></form>',
        '<svg><circle cx="50" cy="50" r="40"/></svg>',
        '<object data="something"></object>',
    ],
)
def test_check_html_disallowed_tags(
    fake_patterns_file: Path, bad_tag: str
) -> None:
    findings = check_outgoing("Plain text", html=f"<p>Hello</p>{bad_tag}")
    assert "disallowed-tag" in [finding.kind for finding in findings]


@pytest.mark.parametrize(
    "bad_attr",
    [
        '<p style="color: red;">Red</p>',
        '<p class="hidden">Hidden</p>',
        '<a href="https://example.com" onclick="steal()">Click</a>',
        '<h1 id="top">Header</h1>',
    ],
)
def test_check_html_disallowed_attributes(
    fake_patterns_file: Path, bad_attr: str
) -> None:
    findings = check_outgoing("Plain text", html=bad_attr)
    assert "disallowed-attribute" in [
        finding.kind for finding in findings
    ]


def test_check_html_wrapper_div_style_allowed(
    fake_patterns_file: Path,
) -> None:
    html_text = (
        '<div style="max-width: 700px; font-family: sans-serif;">'
        "<p>Hello world</p>"
        "</div>"
    )
    assert check_outgoing("Hello world", html=html_text) == []


@pytest.mark.parametrize(
    "bad_scheme",
    [
        '<a href="http://insecure.example.com">Link</a>',
        '<a href="javascript:alert(1)">Click</a>',
        '<a href="data:text/html;base64,PHNjcmlwdD4=">Data</a>',
        '<a href="file:///etc/passwd">File</a>',
    ],
)
def test_check_html_disallowed_link_schemes(
    fake_patterns_file: Path, bad_scheme: str
) -> None:
    findings = check_outgoing("Plain text", html=bad_scheme)
    assert "disallowed-link-scheme" in [
        finding.kind for finding in findings
    ]


def test_check_html_allowed_link_schemes(
    fake_patterns_file: Path,
) -> None:
    html_text = (
        '<p><a href="https://example.com/docs">'
        "https://example.com/docs</a> or "
        '<a href="mailto:peer@example.com">mail</a></p>'
    )
    findings = check_outgoing("Plain text", html=html_text)
    assert not any(
        finding.kind == "disallowed-link-scheme" for finding in findings
    )


def test_check_html_disguised_link_detected(
    fake_patterns_file: Path,
) -> None:
    html_text = (
        '<p><a href="https://unsafe.example.com">'
        "https://trusted.example.com</a></p>"
    )
    findings = check_outgoing("Plain text", html=html_text)
    assert "disguised-link" in [finding.kind for finding in findings]


def test_check_html_undisguised_url_link_passes(
    fake_patterns_file: Path,
) -> None:
    html_text = (
        '<p><a href="https://example.com/path">'
        "https://example.com/path</a></p>"
    )
    findings = check_outgoing("Plain text", html=html_text)
    assert not any(finding.kind == "disguised-link" for finding in findings)


def test_ordinary_neutral_prose_passes_clean(
    fake_patterns_file: Path,
) -> None:
    sample_text = (
        "# Activity Log — Cycle 113\n\n"
        "- Resident: Template Agent (#66)\n"
        "- Location: Place 768, Continent #768\n"
        "- Explored Place 1130, Place 1131, and Place 1132\n"
        "- Crafted Thing #4382 for the shared workshop\n"
        "- Published Note #22951 via the drafting airlock\n"
        "- Contact: mailbox@example.com or peer@example.com\n"
        "- Balances: 3.000000 fee credits and daily quotas\n"
        "- Trait #295 and Kind #39 describe the state box\n"
    )
    assert check_outgoing(sample_text) == []


ORDINARY_PROSE = [
    "I have been here with some time from last week and made this note.",
    (
        "The group followed every marked station in order, step by step, "
        "until the paved route became gravel, moss, and ice."
    ),
    (
        "From a distance the two labels looked contradictory, but walking "
        "the full route restored their sequence and explained both."
    ),
    (
        "The road continues, and today's responsibilities are taking their "
        "time. Thank you for the company along the way."
    ),
    (
        "The small workshop uses an eight by eight matrix for quiet status "
        "drawings. The result was comprehensible to every reviewer."
    ),
]


@pytest.mark.parametrize("paragraph", ORDINARY_PROSE)
def test_ordinary_english_has_no_secret_or_path_findings(
    fake_patterns_file: Path, paragraph: str
) -> None:
    kinds = [finding.kind for finding in check_outgoing(paragraph)]
    assert "secret" not in kinds
    assert "local-path" not in kinds


def test_peer_email_with_planted_secret_is_refused(
    fake_patterns_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools import mail

    smtp_called = False

    class FakeSMTP:
        def __init__(self, *args: object, **kwargs: object) -> None:
            nonlocal smtp_called
            smtp_called = True

        def __enter__(self) -> "FakeSMTP":
            return self

        def __exit__(self, *args: object) -> bool:
            return False

        def starttls(self) -> None:
            pass

        def login(self, address: str, password: str) -> None:
            pass

        def send_message(self, message: object) -> None:
            pass

    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    with pytest.raises(mail.OutgoingCheckError) as exc_info:
        mail.send_message(
            "sender@example.com",
            "password",
            to_address="peer@example.com",
            subject="Greeting",
            body="Here is my secret: sk-1234567890abcdef1234567890abcdef",
            scope="peer",
        )
    assert "REFUSED — outgoing check: secret" in str(exc_info.value)
    assert smtp_called is False


def test_missing_pattern_file_refuses_peer_email(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools import mail

    monkeypatch.setenv(
        "AGENT_PRIVATE_PATTERNS", str(tmp_path / "missing-patterns.txt")
    )
    monkeypatch.setattr(outgoing_check, "get_agent_email", lambda: ("", ""))
    monkeypatch.setattr(outgoing_check, "_load_secrets_module", lambda: None)
    with pytest.raises(mail.OutgoingCheckError) as exc_info:
        mail.send_message(
            "sender@example.com",
            "password",
            to_address="peer@example.com",
            subject="Greeting",
            body="Completely ordinary body text.",
            scope="peer",
        )
    assert "patterns-unavailable" in str(exc_info.value)


def test_operator_email_with_planted_path_is_sent_with_warning(
    fake_patterns_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools import mail

    sent_messages: list[object] = []

    class FakeSMTP:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "FakeSMTP":
            return self

        def __exit__(self, *args: object) -> bool:
            return False

        def starttls(self) -> None:
            pass

        def login(self, address: str, password: str) -> None:
            pass

        def send_message(self, message: object) -> None:
            sent_messages.append(message)

    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    mail.send_message(
        "sender@example.com",
        "password",
        to_address="operator@example.com",
        subject="Operator Update",
        body=rf"Check file at {WINDOWS_USERS_ROOT}\SomeName\Documents\data.txt",
        scope="operator",
    )

    assert len(sent_messages) == 1
    message = sent_messages[0]
    plain_part = message.get_body(preferencelist=("plain",)).get_content()
    html_part = message.get_body(preferencelist=("html",)).get_content()
    warning = "[outgoing check: local-path — review before forwarding]"
    assert plain_part.startswith(warning)
    assert rf"{WINDOWS_USERS_ROOT}\SomeName\Documents\data.txt" in plain_part
    assert warning in html_part


def test_mailbag_reply_has_no_html_part(
    fake_patterns_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools import mail

    sent_messages: list[object] = []

    class FakeSMTP:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "FakeSMTP":
            return self

        def __exit__(self, *args: object) -> bool:
            return False

        def starttls(self) -> None:
            pass

        def login(self, address: str, password: str) -> None:
            pass

        def send_message(self, message: object) -> None:
            sent_messages.append(message)

    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    mail.send_message(
        "sender@example.com",
        "password",
        to_address="stranger@example.com",
        subject="Public Reply",
        body="A public reply to an unknown sender.",
        scope="mailbag",
    )

    assert len(sent_messages) == 1
    message = sent_messages[0]
    assert message.get_content_type() == "text/plain"
    assert message.get_body(preferencelist=("html",)) is None
    assert "A public reply to an unknown sender." in message.get_content()


def test_quoted_html_renders_as_visible_text_not_live_html(
    fake_patterns_file: Path,
) -> None:
    from tools import mail

    text = (
        'Quoting a message:\n<img src="https://example.com/tracker.png">\n'
        "<script>alert('example')</script>\n"
        "Here is [a disguised link](javascript:alert(1))."
    )
    rendered = mail.render_html(text)
    assert "<img" not in rendered
    assert "<script" not in rendered
    assert '<a href="javascript:' not in rendered
    assert "&lt;img" in rendered
    assert "&lt;script&gt;" in rendered
    assert "[a disguised link](javascript:alert(1))" in rendered
    findings = check_outgoing(text, html=rendered)
    disallowed = [
        finding.kind
        for finding in findings
        if "disallowed" in finding.kind
    ]
    assert disallowed == []


def test_plain_text_part_is_unchanged(
    fake_patterns_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools import mail

    sent_messages: list[object] = []

    class FakeSMTP:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "FakeSMTP":
            return self

        def __exit__(self, *args: object) -> bool:
            return False

        def starttls(self) -> None:
            pass

        def login(self, address: str, password: str) -> None:
            pass

        def send_message(self, message: object) -> None:
            sent_messages.append(message)

    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    body = "Hello peer, here is the update."
    mail.send_message(
        "sender@example.com",
        "password",
        to_address="peer@example.com",
        subject="Hello",
        body=body,
        scope="peer",
    )

    plain_part = sent_messages[0].get_body(
        preferencelist=("plain",)
    ).get_content()
    assert plain_part == f"{body}\n\n{mail.SIGNATURE}\n"


def test_spend_request_email_contains_no_links_or_buttons(
    fake_patterns_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import email

    from tools import harness_mcp

    sent_emails: list[tuple[str, str]] = []

    class FakeSMTP:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "FakeSMTP":
            return self

        def __exit__(self, *args: object) -> bool:
            return False

        def starttls(self) -> None:
            pass

        def login(self, address: str, password: str) -> None:
            pass

        def sendmail(
            self, sender: str, recipients: list[str], text: str
        ) -> None:
            sent_emails.append((recipients[0], text))

    monkeypatch.setattr(harness_mcp.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(
        harness_mcp,
        "_load_secrets",
        lambda: SimpleNamespace(
            GMAIL_ADDRESS="sender@example.com",
            GMAIL_APP_PASSWORD="password",
            NOTIFY_EMAIL_TO="operator@example.com",
        ),
    )
    monkeypatch.setattr(
        harness_mcp, "read_wallet_config", lambda: {"pay_armed": False}
    )

    result = harness_mcp.authorise_spend("invent_kind", "Minting Kind 99")
    assert result["status"] == "REFUSED"
    assert result["emailed"] is True
    assert len(sent_emails) == 1

    message = email.message_from_string(sent_emails[0][1])
    html_part = None
    for part in message.walk():
        if part.get_content_type() == "text/html":
            html_part = part.get_payload(decode=True).decode("utf-8")
    assert html_part is not None
    assert "<a" not in html_part
    assert "http" not in html_part
    assert "<button" not in html_part


def test_stage_draft_advisory_outgoing_check(
    fake_patterns_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import harness_mcp

    monkeypatch.setattr(harness_mcp, "REPO", tmp_path)
    memory_dir = tmp_path / harness_mcp.names.dir_name("memory")
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "almanac.json").write_text(
        '{"places": {"3": {}}}', encoding="utf-8"
    )
    conversation_id = "11111111-1111-4111-8111-111111111111"

    clean_result = harness_mcp.stage_draft(
        slug="clean-draft",
        target_type="note",
        target_place_id=3,
        body="Normal friendly greeting to the square.",
        conversation_id=conversation_id,
    )
    assert clean_result["status"] == "STAGED"
    assert clean_result["metrics"]["outgoing_check"] == "clean"
    drafts_dir = tmp_path / harness_mcp.names.dir_name("drafts")
    clean_text = (drafts_dir / "clean-draft.md").read_text(encoding="utf-8")
    assert "outgoing_check: clean" in clean_text

    flagged_result = harness_mcp.stage_draft(
        slug="flagged-draft",
        target_type="note",
        target_place_id=3,
        body=(
            rf"Here is {WINDOWS_USERS_ROOT}\SomeName\secret.txt with "
            "sk-1234567890abcdef1234567890abcdef"
        ),
        conversation_id=conversation_id,
    )
    assert flagged_result["status"] == "STAGED"
    metrics = flagged_result["metrics"]["outgoing_check"]
    assert isinstance(metrics, list)
    assert "local-path" in metrics
    assert "secret" in metrics
    flagged_text = (drafts_dir / "flagged-draft.md").read_text(
        encoding="utf-8"
    )
    assert "outgoing_check:" in flagged_text
    assert "local-path" in flagged_text
    assert "secret" in flagged_text
