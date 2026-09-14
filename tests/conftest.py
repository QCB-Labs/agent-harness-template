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

from tools import authority, phase


@pytest.fixture(autouse=True)
def _isolate_live_state(tmp_path_factory: pytest.TempPathFactory,
                        monkeypatch: pytest.MonkeyPatch) -> None:
    sandbox = tmp_path_factory.mktemp("live-state")
    monkeypatch.setattr(phase, "REPO_ROOT", sandbox)
    monkeypatch.setattr(authority, "REPO_ROOT", sandbox)
    monkeypatch.setenv("AGENT_SESSION_DIR", str(sandbox / "session"))
