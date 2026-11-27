#!/usr/bin/env python3
"""Write one list of every tool and its argument names.

The client loads tools lazily: a tool's argument names reach the agent only
when it opens that tool's description file. Reading dozens of those files in a
wake costs more than reading one list. This builds the list from the client's
own description files, so it cannot drift from the real tools. Run it again
whenever tools are added, removed or renamed. Add other connected servers with
`--servers harness city <name>`. Only clients that keep description files
(Antigravity does) have anything for it to read.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

SERVERS = ("harness", "city")
DEFAULT_SOURCE = Path.home() / ".gemini" / "antigravity" / "mcp"
DEFAULT_OUT = Path("config") / "tool-sheet.md"
MAX_NOTE = 90


def _first_sentence(text: str) -> str:
    line = " ".join((text or "").split())
    for stop in (". ", ".\n"):
        if stop in line:
            line = line.split(stop, 1)[0]
    line = line.rstrip(".")
    return line if len(line) <= MAX_NOTE else line[: MAX_NOTE - 1].rstrip() + "…"


def read_tool(path: Path) -> tuple[str, str] | None:
    """One description file as (signature, note), or None if it is not a tool."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    name = data.get("name") if isinstance(data, dict) else None
    params = data.get("parameters") if isinstance(data, dict) else None
    if not isinstance(name, str) or not isinstance(params, dict):
        return None
    required = set(params.get("required") or [])
    args = [f"{key}{'' if key in required else '?'}"
            for key in (params.get("properties") or {}) if not key.startswith("_")]
    return f"{name}({', '.join(args)})", _first_sentence(data.get("description", ""))


def build(source: Path, servers=SERVERS) -> str:
    lines = [
        "# Tool sheet",
        "",
        "Every tool with its argument names, built from the client's own description",
        "files by `tools/tool_sheet.py`. An argument ending in `?` is optional.",
        "A tool's own description file has its full text.",
    ]
    for server in servers:
        tools = sorted(t for t in (read_tool(p) for p in (source / server).glob("*.json")) if t)
        if not tools:
            continue
        lines += ["", f"## {server}", ""]
        lines += [f"- `{sig}`" + (f" - {note}" if note else "") for sig, note in tools]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Write the one-file list of tools and their arguments.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--servers", nargs="*", default=list(SERVERS))
    args = parser.parse_args()
    text = build(args.source, args.servers)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {args.out} ({len(text.encode('utf-8'))} bytes)")


if __name__ == "__main__":
    main()
