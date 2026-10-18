"""Backfill the operator's private casefiles from git history.

Operator-run, from the repository root:

    python -m tools.backfill_casefiles --dry-run
    python -m tools.backfill_casefiles

Walks committed resident case files and site inspections, finds each line of the impression
section that holds a listed reliability phrase (the current word list), and
records it once, dated by the first commit it appeared in. Safe to run twice:
rows already in a casefile are not added again. --dry-run prints counts per
source only: no text, and no path outside the repository.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from tools import casefiles, paths, verdicts, names

FOLDERS = {
    names.rel("residents").rstrip("/") + "/": "## Impression",
    names.rel("atlas").rstrip("/") + "/places/": "## Field Impression",
}


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True, encoding="utf-8").stdout


def _source(rel: str) -> tuple[str, str] | None:
    name = Path(rel).stem
    if rel.startswith(names.rel("residents").rstrip("/") + "/"):
        return None if name == "index" else casefiles.resident(name)
    head = name.split("-", 1)[0]
    return casefiles.place(int(head)) if head.isdigit() else None


def _impression_lines(text: str, heading: str) -> list[str]:
    """Lines under every impression heading (a flight log can hold several
    records), up to the next heading."""
    out, inside = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line.strip() == heading
            continue
        if inside and line.strip():
            out.append(line)
    return out


def collect(root: Path) -> dict[tuple[str, str], list[tuple[datetime, str, str, str]]]:
    """{source: [(first date, file, line, phrases)]}, oldest commit first."""
    claims, ignore = verdicts.load_words(root)
    found: dict[tuple[str, str], dict[tuple[str, str], tuple[datetime, str]]] = {}
    for folder, heading in FOLDERS.items():
        log = _git(root, "log", "--reverse", "--format=%H %cI", "--", folder)
        for entry in log.splitlines():
            sha, date = entry.split(" ", 1)
            when = datetime.fromisoformat(date)
            names = _git(root, "ls-tree", "-r", "--name-only", sha, "--", folder)
            for rel in names.splitlines():
                source = _source(rel)
                if source is None or not rel.endswith(".md"):
                    continue
                try:
                    text = _git(root, "show", f"{sha}:{rel}")
                except subprocess.CalledProcessError:
                    continue
                lines = _impression_lines(verdicts.strip_marks(text), heading)
                by_line = verdicts.find_by_line("\n".join(lines), claims, ignore)
                for number, phrases in by_line.items():
                    key = (rel, lines[number].strip())
                    seen = found.setdefault(source, {})
                    if key not in seen:
                        seen[key] = (when, ", ".join(sorted(phrases)))
    return {source: [(when, rel, line, phrases)
                     for (rel, line), (when, phrases) in sorted(rows.items(), key=lambda kv: kv[1][0])]
            for source, rows in found.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    root = Path.cwd()
    if not args.dry_run and paths.cases_dir(also_outside=root) is None:
        print("Casefiles are off: no cases folder is set.")
        return 1
    sources = collect(root)
    total = 0
    failed = 0
    for source in sorted(sources):
        rows = sources[source]
        total += len(rows)
        if args.dry_run:
            print(f"{source[0]} {source[1]}: {len(rows)} flagged line(s)")
            continue
        added = 0
        for when, rel, line, phrases in rows:
            result = casefiles.append_flags(source, source[1], rel, [(line, phrases)],
                                            when=when, repo=root)
            if result is None:
                failed += 1
            else:
                added += result
        print(f"{source[0]} {source[1]}: {added} row(s) added")
    print(f"{len(sources)} source(s), {total} flagged line(s).")
    if failed:
        print(f"{failed} row(s) could not be written; see casefile-errors.log.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
