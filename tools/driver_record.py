"""Read which models made this wake's calls, from the driver's own record.

The harness cannot ask the model what it is: a model often does not know.
The driver (Antigravity) keeps one small database per conversation, with a
row for every model call and the model's name on it. This module reads that
record, read-only, and counts the calls by model.

Everything that names the driver's files or their layout lives in
``config/driver-record.json``. With no such file there is no record to read
and nothing is added to a log. Nothing here raises: an unreadable, locked or
unfamiliar record yields an empty answer.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import time

SETTINGS = "config/driver-record.json"


def settings(repo: Path) -> dict | None:
    path = Path(repo) / SETTINGS
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("enabled") else None


def _workspace_forms(repo: Path) -> list[bytes]:
    # The record holds the folder as a file URI, with the drive's colon both
    # plain and percent-encoded. Compared without regard to letter case.
    uri = Path(repo).resolve().as_uri().lower()
    forms = {uri}
    match = re.match(r"(file:///[a-z]):(.*)", uri)
    if match:
        forms.add(match.group(1) + "%3a" + match.group(2))
    return [form.encode("utf-8") for form in forms]


def _names_folder(blob: bytes, forms: list[bytes]) -> bool:
    # A stored string is preceded by its length, so a longer folder whose
    # name merely starts with this one does not match.
    lowered = blob.lower()
    for form in forms:
        if len(form) > 127:
            if form in lowered:
                return True
            continue
        start = lowered.find(form)
        while start >= 0:
            # the length is read from the stored bytes: lowering would change it
            if start > 0 and blob[start - 1] == len(form):
                return True
            start = lowered.find(form, start + 1)
    return False


def _open(path: Path) -> sqlite3.Connection:
    return sqlite3.connect("file:" + path.as_posix() + "?mode=ro", uri=True, timeout=1.0)


@contextmanager
def _snapshot(path: Path):
    with tempfile.TemporaryDirectory(prefix="agent-driver-") as temporary:
        copied = Path(temporary) / path.name
        shutil.copy2(path, copied)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(path) + suffix)
            if sidecar.exists():
                shutil.copy2(sidecar, Path(str(copied) + suffix))
        with closing(_open(copied)) as con:
            yield con


def models_used(repo: Path, cfg: dict | None = None, now: float | None = None) -> list[tuple[str, int]]:
    """Models named on the calls of the newest conversation for this folder, in order of first use."""
    cfg = cfg if cfg is not None else settings(repo)
    if not cfg:
        return []
    try:
        folder = Path(os.path.expanduser(cfg["folder"]))
        pattern = re.compile(cfg["model_pattern"].encode("latin-1"), re.DOTALL)
        newest_first = sorted(folder.glob(cfg.get("glob", "*.db")), key=lambda p: p.stat().st_mtime, reverse=True)
        limit = (now if now is not None else time.time()) - 60 * float(cfg.get("newer_than_minutes", 180))
        forms = _workspace_forms(repo)
    except (OSError, KeyError, ValueError, re.error, TypeError):
        return []
    for path in newest_first:
        try:
            if path.stat().st_mtime < limit:
                break
            with _snapshot(path) as con:
                blobs = [row[0] for row in con.execute(
                    f"select [{cfg['workspace_column']}] from [{cfg['workspace_table']}]")]
                if not any(isinstance(blob, bytes) and _names_folder(blob, forms) for blob in blobs):
                    continue
                counts: dict[str, int] = {}
                for (data,) in con.execute(
                        f"select [{cfg['column']}] from [{cfg['table']}] order by [{cfg.get('order', 'rowid')}]"):
                    if not isinstance(data, bytes):
                        continue
                    name = None
                    for match in pattern.finditer(data):
                        if match.group(1)[0] == len(match.group(2)):
                            name = match.group(2).decode("ascii", "replace")
                    if name:
                        counts[name] = counts.get(name, 0) + 1
                return list(counts.items())
        except OSError:
            return []
        except (sqlite3.Error, KeyError, IndexError, ValueError):
            continue
    return []


def footer(repo: Path) -> str:
    """The line the harness adds to a log, or an empty string when there is nothing measured."""
    cfg = settings(repo)
    if not cfg:
        return ""
    used = models_used(repo, cfg)
    if not used:
        return ""
    try:
        # The driver's own name for a setting is shown in the operator's
        # words where she has given them; an unlisted name is shown as it is.
        labels = cfg.get("labels") or {}
        models = cfg.get("joiner", ", ").join(
            cfg["model_item"].format(name=name, label=labels.get(name, name), calls=calls) for name, calls in used)
        return cfg["footer"].format(models=models, total=sum(calls for _, calls in used))
    except (KeyError, IndexError, ValueError):
        return ""


def strip_footers(repo: Path, content: str) -> str:
    """Remove footer lines the agent copied into a log, and the rule above each.

    The wake card shows the last log with its footer, and the agent has
    copied it into a new log with an old count, number and all.
    Only the footer the harness adds is measured, so a copy goes before the
    real one is added. A line counts as a footer when it carries both fixed
    ends of the configured template, so the two can't drift apart.
    """
    cfg = settings(repo)
    template = (cfg or {}).get("footer", "")
    if "{models}" not in template:
        return content
    before, after = template.split("{models}", 1)
    head = before.strip().splitlines()[-1].strip()
    tail = after.strip()
    if not head or not tail:
        return content
    # The template's footer also measures the total; match its rendered count.
    tail_pattern = re.compile(re.escape(tail).replace(r"\{total\}", r"\d+") + r"$")
    lines = content.splitlines(keepends=True)
    kept: list[str] = []
    for line in lines:
        text = line.strip()
        if text.startswith(head) and tail_pattern.search(text):
            while kept and not kept[-1].strip():
                kept.pop()
            if kept and kept[-1].strip() == "---":
                kept.pop()
            continue
        kept.append(line)
    return "".join(kept)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Show the models named in the driver's record for this folder.")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    cfg = settings(args.repo_root)
    if not cfg:
        print("No driver record is configured.")
        return
    used = models_used(args.repo_root, cfg)
    if not used:
        print("No model calls found for this folder in the driver's record.")
        return
    for name, calls in used:
        print(f"{name}: {calls} calls")


if __name__ == "__main__":
    main()
