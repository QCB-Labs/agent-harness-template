"""Information-release boundary, derived from .gitignore.

The agent's repository is public. Anything committed to it is already
world-readable, so withholding it from a reply protects nothing — the reader
can simply open the repo. Anything gitignored is genuinely private and loads
only after replies to unknown senders have been sent.

So the publication boundary and the release boundary are the SAME boundary,
and this module derives one from the other rather than keeping a second list.
A hand-maintained copy drifts; the day someone adds a sensitive file and
forgets to gitignore it they would get both failures at once — published to
GitHub *and* readable while composing a reply to a stranger.

Three categories:

    public   Everything committed. Released freely in correspondence.
    private  Gitignored working state. Loaded after public replies are sent.
    never    Credentials and keys. Not loaded in any phase, ever.

"never" is a prohibition, not an ordering. Saying "loaded late" would imply
there is some phase in which it would be acceptable. There is not.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Gitignored, but build noise rather than private context. Excluded so the
# derived set stays meaningful.
IGNORE_NOISE = frozenset({
    "__pycache__/",
    "*.py[cod]",
    ".pytest_cache/",
    ".coverage",
    "htmlcov/",
    ".venv/",
    "venv/",
    "env/",
    "*.tmp",
    "*.bak",
    "*.bak_ids",
    "_UNRECORDED-*.md",
    ".env",
    ".env.*",
    "!.env.example",
})

# Never loaded into the agent's context in ANY phase. Credentials and keys.
# No MCP tool returns these; no phase reads them. tools/credentials.py may read
# secrets.py because it is harness-internal — what matters is that no tool the
# model can call ever surfaces a value.
NEVER_LOAD = frozenset({
    "secrets.py",
    "tools/secrets.py",
    "tools/secrets_backup.py",
    ".env",
    "wallet-config.json",
    ".agents/mcp_config.json",
    ".gemini/settings.json",
    "gemini.local.json",
    "operator_private.pem",
    "WALLET-SETUP.md",
})


def _gitignore_entries() -> list[str]:
    path = REPO_ROOT / ".gitignore"
    if not path.exists():
        return []
    entries = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        entries.append(line)
    return entries


def private_prefixes() -> frozenset[str]:
    """Paths that are private context — loaded after public replies are sent.

    Derived from .gitignore, minus build noise and minus NEVER_LOAD (a stricter
    category, not a subset of "load later"). Negations and bare wildcards are
    skipped: they cannot name a directory to withhold.
    """
    result = set()
    for entry in _gitignore_entries():
        if entry in IGNORE_NOISE or entry in NEVER_LOAD:
            continue
        if entry.startswith("!") or entry.startswith("*"):
            continue
        result.add(entry.rstrip("/"))
    return frozenset(result)


def _normalise(relative_path: str) -> str:
    """Normalise separators and strip a leading './'.

    Note: lstrip("./") would be wrong here — it strips leading '.' characters
    as a set, turning ".agents/mcp_config.json" into "agents/mcp_config.json"
    and silently exempting every dotfile from NEVER_LOAD.
    """
    normalised = relative_path.replace("\\", "/")
    while normalised.startswith("./"):
        normalised = normalised[2:]
    return normalised.lstrip("/")


def is_private(relative_path: str) -> bool:
    """True if the path is private context (load only after public replies)."""
    normalised = _normalise(relative_path)
    if normalised.endswith(".bak"):
        return True
    return any(
        normalised == prefix or normalised.startswith(f"{prefix}/")
        for prefix in private_prefixes()
    )


def is_never_load(relative_path: str) -> bool:
    """True if the path must never enter the agent's context in any phase."""
    return _normalise(relative_path) in NEVER_LOAD


def is_public(relative_path: str) -> bool:
    """True if the path is committed to the public repo and freely releasable."""
    return not is_private(relative_path) and not is_never_load(relative_path)
