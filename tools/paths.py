"""Out-of-repo locations: trust anchors and operator session state.

Windows is the operator machine; POSIX is the cloud VM. This module is the only
place that knows the difference — never hardcode a platform path elsewhere.

WHY THESE LIVE OUTSIDE THE REPOSITORY

If operator.pub sat inside the repo, and the agent can write files, it could
overwrite the key with one it generated and then sign its own orders. The same
reasoning applies to the approved-correspondents allowlist: an agent that can
edit the allowlist can add an attacker's address and mail it. Both are trust
anchors and both must be somewhere the agent cannot write.

The session token is different. It is necessarily written by the operator, who
runs as the same OS user as the agent tooling, so no permission scheme keeps it
out of reach on a single-user machine. Its integrity comes from the Ed25519
signature inside it, not from where it sits — a readable token cannot be forged
or extended. Confidentiality of "a session is active" is not defended; the
integrity of "who authorised this" is.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from tools import profile

REPO_ROOT = Path(__file__).resolve().parent.parent


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def trust_anchor_dir() -> Path:
    """Read-only anchors: operator.pub, peer keys, correspondents.json, blocklist.

    Override with AGENT_TRUST_DIR. Defaults:
        Windows  %PROGRAMDATA%\\<handle>\\trust   admin-writable, user-readable
        POSIX    /etc/<handle>/trust              root-writable, world-readable

    <handle> is the handle in agent-profile.json, so each agent on a machine
    gets its own trust anchor.

    Deliberately NOT %LOCALAPPDATA% or $HOME. The harness runs as the same user
    as the agent tooling, so a user-writable directory is not a trust anchor —
    it is just a file the agent could rewrite.
    """
    override = os.environ.get("AGENT_TRUST_DIR")
    if override:
        return Path(override)
    if _is_windows():
        program_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        return Path(program_data) / profile.handle() / "trust"
    return Path("/etc") / profile.handle() / "trust"


def session_dir() -> Path:
    """Operator session token directory. Ephemeral, outside the repo.

    Override with AGENT_SESSION_DIR. Defaults:
        Windows  %LOCALAPPDATA%\\<handle>\\session
        POSIX    $XDG_RUNTIME_DIR/<handle>, else /run/user/<uid>/<handle>,
                 else /tmp/<handle>
    """
    override = os.environ.get("AGENT_SESSION_DIR")
    if override:
        return Path(override)
    if _is_windows():
        local = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        return Path(local) / profile.handle() / "session"
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return Path(xdg) / profile.handle()
    getuid = getattr(os, "getuid", None)
    if getuid is not None:
        uid_dir = Path(f"/run/user/{getuid()}")
        if uid_dir.is_dir():
            return uid_dir / profile.handle()
    return Path("/tmp") / profile.handle()


def session_token_path() -> Path:
    return session_dir() / "operator_session.json"


def operator_pubkey_path() -> Path:
    return trust_anchor_dir() / "operator.pub"


def peer_pubkey_path(handle: str) -> Path:
    """Public key for a peer agent. Handle is validated — it names a file."""
    import re

    clean_handle = handle.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", clean_handle):
        raise ValueError(f"invalid peer handle: {handle!r}")
    candidate = (trust_anchor_dir() / f"{clean_handle}.pub").resolve()
    if not candidate.is_relative_to(trust_anchor_dir().resolve()):
        raise ValueError(f"invalid peer handle: {handle!r}")
    return candidate


def correspondents_path() -> Path:
    return trust_anchor_dir() / "correspondents.json"


def inside_repo(path: Path, also: Path | None = None) -> bool:
    """True if ``path`` resolves inside the code repository, or inside
    ``also`` (the harness's active data repository, when it differs)."""
    resolved = Path(path).resolve()
    for root in (REPO_ROOT, also):
        if root is None:
            continue
        root = Path(root).resolve()
        if resolved == root or root in resolved.parents:
            return True
    return False


def operator_log_dir() -> Path:
    """Where the harness logs what only the operator should read: beside the
    session folder, outside the repository."""
    return session_dir().parent / "logs"


def assert_outside_repo(path: Path) -> None:
    """Raise if a trust-anchor path resolves inside the repository.

    Called before reading any anchor. A key or allowlist that has drifted into
    the repo is writable by the agent, which voids the thing it anchors.
    """
    resolved = path.resolve()
    repo = REPO_ROOT.resolve()
    if resolved == repo or repo in resolved.parents:
        raise RuntimeError(
            f"SECURITY: trust anchor {resolved} is inside the repository, where "
            "the agent can write. It could install its own operator key or add "
            "its own correspondent. Move it out before starting the harness."
        )
