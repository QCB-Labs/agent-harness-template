"""Automated security and secret hygiene test suite.

Verifies that:
1. Live credentials, wallet files, and plaintext MCP configs are strictly gitignored.
2. No tracked file in the git index contains live bearer tokens or private keys.
3. Safe example templates (*.example.*) exist and contain only placeholders.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from tools import names

REPO_ROOT = Path(__file__).resolve().parent.parent

SECRET_PATTERNS = [
    re.compile(r"1f3d9_sk_[a-f0-9]{30,}", re.IGNORECASE),
    re.compile(r"1f916_sk_[a-f0-9]{30,}", re.IGNORECASE),
    re.compile(r"1f3ea_sk_[a-f0-9]{30,}", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
]


def test_gitignore_contains_sensitive_targets() -> None:
    gitignore_path = REPO_ROOT / ".gitignore"
    assert gitignore_path.exists(), ".gitignore must exist in the repo root"
    content = gitignore_path.read_text(encoding="utf-8")

    expected_rules = [
        "secrets.py",
        "tools/secrets.py",
        "wallet-config.json",
        "WALLET-SETUP.md",
        ".agents/mcp_config.json",
        f"{names.dir_name('memory')}/private/",
    ]
    for rule in expected_rules:
        assert rule in content, f"Expected '{rule}' to be listed in .gitignore"


def test_sensitive_files_are_gitignored() -> None:
    sensitive_files = [
        "tools/secrets.py",
        "secrets.py",
        "wallet-config.json",
        "WALLET-SETUP.md",
        ".agents/mcp_config.json",
        f"{names.dir_name('memory')}/private/keys/square_ed25519.json",
    ]
    cmd = ["git", "check-ignore"] + sensitive_files
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)

    # git check-ignore returns exit code 0 when matching files are ignored
    assert result.returncode == 0, f"git check-ignore failed: {result.stderr}"
    ignored_lines = {line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()}

    for target in sensitive_files:
        assert any(
            ignored == target or ignored == target.split("/")[-1]
            for ignored in ignored_lines
        ), f"File '{target}' is not recognized as gitignored by git"


def test_tracked_files_contain_no_live_secrets() -> None:
    cmd = ["git", "ls-files"]
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    assert result.returncode == 0, f"git ls-files failed: {result.stderr}"

    tracked_files = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    assert len(tracked_files) > 0, "Expected git ls-files to find tracked files"

    violations: list[str] = []
    for rel_path in tracked_files:
        file_path = REPO_ROOT / rel_path
        if not file_path.is_file():
            continue

        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # Binary file
            continue

        for line_no, line in enumerate(content.splitlines(), start=1):
            for pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    violations.append(f"{rel_path}:{line_no} matches secret pattern {pattern.pattern}")

    assert not violations, "Found live secrets committed in tracked git files:\n" + "\n".join(violations)


def test_example_templates_exist_and_are_safe() -> None:
    # 1. tools/secrets.example.py
    secrets_example = REPO_ROOT / "tools" / "secrets.example.py"
    assert secrets_example.exists(), "tools/secrets.example.py must exist"
    secrets_text = secrets_example.read_text(encoding="utf-8")
    assert "CITY_TOKEN" in secrets_text
    assert "SQUARE_TOKEN" in secrets_text
    assert "MARKET_TOKEN" in secrets_text
    assert "your_secret_here" in secrets_text
    for pattern in SECRET_PATTERNS:
        assert not pattern.search(secrets_text), "tools/secrets.example.py contains live secrets!"

    # 2. wallet-config.example.json
    wallet_example = REPO_ROOT / "wallet-config.example.json"
    assert wallet_example.exists(), "wallet-config.example.json must exist"
    wallet_data = json.loads(wallet_example.read_text(encoding="utf-8"))
    assert wallet_data["enabled"] is False
    assert wallet_data["mode"] == "disabled"
    assert wallet_data["site_scope"] == ["1F3EA"]
    assert wallet_data["public_address"] == "0x0000000000000000000000000000000000000000"

    # 3. .agents/mcp_config.example.json
    mcp_example = REPO_ROOT / ".agents" / "mcp_config.example.json"
    assert mcp_example.exists(), ".agents/mcp_config.example.json must exist"
    mcp_data = json.loads(mcp_example.read_text(encoding="utf-8"))
    servers = mcp_data.get("mcpServers", {})
    assert "harness" in servers
    assert "city" in servers
    assert "square" in servers
    assert "market" in servers
    for pattern in SECRET_PATTERNS:
        assert not pattern.search(mcp_example.read_text(encoding="utf-8")), ".agents/mcp_config.example.json contains live secrets!"

    # 4. WALLET-SETUP.example.md
    wallet_setup_example = REPO_ROOT / "WALLET-SETUP.example.md"
    assert wallet_setup_example.exists(), "WALLET-SETUP.example.md must exist"
    for pattern in SECRET_PATTERNS:
        assert not pattern.search(wallet_setup_example.read_text(encoding="utf-8")), "WALLET-SETUP.example.md contains live secrets!"
