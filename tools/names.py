"""Every renameable name in the template, read from naming.json.

Code and tests learn file paths, folder paths, exposed MCP tool names, guarded
headings, and the wake ID format only from here. Switching naming schemes
(tools/rename_scheme.py) therefore never edits code: it moves files, rewrites
docs, and updates naming.json.

A key missing from naming.json falls back to the plain scheme below, so an
older or partial file still works.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
NAMING_PATH = REPO_ROOT / "naming.json"

# The plain scheme: the names the harness code was written with. Tool keys are
# the Python function names; values are the names exposed over MCP.
PLAIN: dict[str, dict[str, str]] = {
    "dirs": {
        "memory": "memory",
        "drafts": "drafts",
    },
    "paths": {
        "identity": "IDENTITY.md",
        "governance": "GOVERNANCE.md",
        "operator": "OPERATOR.md",
        "setup": "SETUP.md",
        "goals": "goals.md",
        "goals_archive": "goals-archive.md",
        "journal": "{memory}/journal",
        "atlas": "{memory}/atlas",
        "residents": "{memory}/residents",
        "works": "{memory}/works",
        "lessons": "{memory}/lessons.md",
        "wishlist": "{memory}/wishlist.md",
        "wishlist_archive": "{memory}/wishlist-archive.md",
        "open_threads": "{memory}/open-threads.md",
        "city_rules": "{memory}/city-rules.md",
        "goal_files": "{memory}/goals",
        "projects": "{memory}/projects",
        "threads": "{memory}/threads",
        "threads_archive": "{memory}/open-threads-archive.md",
        "threads_before": "{memory}/open-threads-before-split.md",
    },
    "headings": {
        "identity": "# IDENTITY",
        "governance": "# GOVERNANCE",
        "operator": "# OPERATOR",
        "open_threads": "# Open Threads",
        "works_index": "# Works index",
    },
    "tools": {},
    "wake_id": {"prefix": "W", "width": "3"},
}


@lru_cache(maxsize=1)
def load() -> dict[str, dict[str, str]]:
    """Return the active scheme, plain names filling any gaps."""
    try:
        data = json.loads(NAMING_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    merged: dict[str, dict[str, str]] = {}
    for section, defaults in PLAIN.items():
        merged[section] = {**defaults, **(data.get(section) or {})}
    merged["scheme"] = {"name": str(data.get("scheme", "plain"))}
    return merged


def scheme() -> str:
    return load()["scheme"]["name"]


def dir_name(key: str) -> str:
    """A top-level folder: memory or drafts."""
    return load()["dirs"][key]


def rel(key: str) -> str:
    """A repository-relative path, POSIX style, e.g. ``memory/journal``."""
    return load()["paths"][key].format(**load()["dirs"])


def memory(*parts: str) -> str:
    """A fixed-name path inside the memory folder, e.g. ``memory/map.json``."""
    return "/".join((dir_name("memory"), *parts))


def heading(key: str) -> str:
    return load()["headings"][key]


def tool(function_name: str) -> str:
    """The MCP-exposed name for a tool, given its Python function name."""
    return load()["tools"].get(function_name, function_name)


def wake_id(number: int) -> str:
    fmt = load()["wake_id"]
    return f"{fmt['prefix']}{number:0{int(fmt['width'])}d}"


def render_tools(text: str) -> str:
    """Render tool call names in guidance through the active naming scheme."""
    import re
    for function, exposed in load()["tools"].items():
        text = re.sub(r"\b" + re.escape(function) + r"\b", lambda match: exposed, text)
    return text
