"""Shared test isolation for the agent's live state.

Several modules keep their own module-level root rather than reading
harness_mcp.REPO: the wake phase machine (tools.phase), the operator-authority
nonce ledger and command drops (tools.authority), and the operator session
token directory (AGENT_SESSION_DIR). A fixture that redirects
harness_mcp.REPO to a temp folder does not reach any of them.

So without this, a test that calls a private-phase tool can write the agent's
LIVE wake phase — closing its stranger reply window and changing the wake id its
per-wake reply allowance keys on — and a test that checks for a session can
touch the operator's real session token.

Autouse and per test. A test that needs its own location still monkeypatches
it, and the later setattr wins.
"""

from __future__ import annotations

import pytest

from tools import authority, mail, phase


@pytest.fixture(autouse=True)
def _isolate_live_state(tmp_path_factory: pytest.TempPathFactory,
                        monkeypatch: pytest.MonkeyPatch) -> None:
    sandbox = tmp_path_factory.mktemp("live-state")
    monkeypatch.setattr(phase, "REPO_ROOT", sandbox)
    monkeypatch.setattr(authority, "REPO_ROOT", sandbox)
    monkeypatch.setenv("AGENT_SESSION_DIR", str(sandbox / "session"))
    # Every test uses made-up patterns and never reads the operator's file.
    patterns = sandbox / "private-patterns.txt"
    patterns.write_text("made-up-private-name\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_PRIVATE_PATTERNS", str(patterns))
    # Reading correspondence collects unread mail first, and real mailbox
    # credentials may be on the machine running the tests. A fetch marks mail
    # as seen, so a test that reached the mailbox would take real letters from
    # the agent. No test connects: the inbox is empty unless a test supplies one.
    monkeypatch.setattr(mail, "fetch_unread", lambda *args, **kwargs: [])


# --- Run-safety guard -------------------------------------------------------
# An elevated test run once wrote its scratch folders into a notes folder as
# Administrator; the operator's user could not read or delete them, and the
# notes app stopped loading. So: test scratch stays in the system temp folder,
# and tests never run elevated (CI runners are the exception).


def _is_elevated() -> bool:
    import ctypes
    import os
    import sys

    if not sys.platform.startswith("win"):
        return hasattr(os, "geteuid") and os.geteuid() == 0
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def pytest_configure(config: pytest.Config) -> None:
    import os
    import tempfile
    from pathlib import Path

    basetemp = config.option.basetemp
    if basetemp:
        temp_root = Path(tempfile.gettempdir()).resolve()
        target = Path(basetemp).resolve()
        if temp_root != target and temp_root not in target.parents:
            raise pytest.UsageError(
                f"--basetemp must be inside the system temp folder ({temp_root}), "
                f"not {target}. Leave --basetemp unset to use the default."
            )
    if _is_elevated() and not os.environ.get("CI"):
        raise pytest.UsageError(
            "Refusing to run tests as administrator: files created elevated "
            "cannot be read or deleted by your normal user. Run from a normal window."
        )
