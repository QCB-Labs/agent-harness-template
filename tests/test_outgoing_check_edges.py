"""Password distance boundaries and HTML state across line/feed boundaries."""

import pytest
from tools import outgoing_check


@pytest.fixture
def patterns(tmp_path, monkeypatch):
    path = tmp_path / "patterns.txt"
    path.write_text("illustrative-private-marker\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_PRIVATE_PATTERNS", str(path))


def test_password_distance_exact_threshold(patterns):
    for distance, expected in [(39, True), (40, True), (41, False)]:
        for before in (True, False):
            token = "abcdefghijklmnop"
            text = ("password" + " " * distance + token) if before else (token + " " * distance + "password")
            assert any(f.kind == "secret" for f in outgoing_check.check_outgoing(text)) is expected


def test_split_tag_preserves_tag_and_attribute_state(patterns):
    html = '<script\n onclick="illustrative()">sample</script>'
    findings = outgoing_check.check_outgoing("Sample", html=html)
    assert {f.kind for f in findings} == {"disallowed-tag", "disallowed-attribute"}
    checker = outgoing_check._HTMLChecker()
    checker.feed('<div style="sample"><a href="https://example.com/target"\n')
    assert checker.tag_stack == ["div"]
    checker.feed(' onclick="illustrative()">https://example.com/other</a></div>')
    assert checker.tag_stack == []
    assert {f.kind for f in checker.findings} == {"disallowed-attribute", "disguised-link"}
    assert outgoing_check.check_outgoing("Sample", html='<a\n href="https://example.com">https://example.com</a>') == []
