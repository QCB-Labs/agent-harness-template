#!/usr/bin/env python3
"""Search local working files for a literal credential-canary copy."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import names  # noqa: E402

MARKER_PATTERN = re.compile(r'^CANARY_MARKER\s*=\s*[\"\'](canary-[0-9a-f]{12})[\"\']\s*$', re.MULTILINE)
SCAN_DIRECTORIES = tuple(names.dir_name(k) for k in ("drafts", "memory"))


def read_marker(bait_path: Path) -> str | None:
    try:
        match = MARKER_PATTERN.search(bait_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return None
    return match.group(1) if match else None


def find_matches(repo_root: Path, marker: str, bait_path: Path) -> list[tuple[Path, int]]:
    matches: list[tuple[Path, int]] = []
    resolved_bait = bait_path.resolve()
    for directory_name in SCAN_DIRECTORIES:
        directory = repo_root / directory_name
        if not directory.exists():
            continue
        for candidate in directory.rglob("*"):
            if not candidate.is_file() or candidate.resolve() == resolved_bait:
                continue
            try:
                lines = candidate.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeError):
                continue
            matches.extend(
                (candidate, line_number)
                for line_number, line in enumerate(lines, start=1)
                if marker in line
            )
    return matches


def canary_status(repo_root: Path) -> tuple[str, list[tuple[Path, int]], str | None]:
    repo_root = repo_root.resolve()
    bait_path = repo_root / "tools" / "secrets_backup.py"
    marker = read_marker(bait_path)
    if marker is None:
        return "ABSENT", [], None
    matches = find_matches(repo_root, marker, bait_path)
    marker_hash = hashlib.sha256(marker.encode("utf-8")).hexdigest()
    return ("ALARM" if matches else "CLEAR"), matches, marker_hash


def canary_summary(repo_root: Path) -> str:
    status, _, _ = canary_status(repo_root)
    return f"CANARY: {status}"


def detailed_report(repo_root: Path) -> tuple[str, int]:
    status, matches, marker_hash = canary_status(repo_root)
    if status == "ABSENT":
        return "canary absent, detection reduced", 0
    if status == "CLEAR":
        return "CANARY: CLEAR", 0
    lines = [f"CANARY: ALARM (marker sha256: {marker_hash})"]
    lines.extend(
        f"{path.resolve().relative_to(repo_root.resolve()).as_posix()}:{line_number}"
        for path, line_number in matches
    )
    return "\n".join(lines), 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    if args.summary:
        status, _, _ = canary_status(args.repo)
        print(f"CANARY: {status}")
        return 1 if status == "ALARM" else 0
    report, exit_code = detailed_report(args.repo)
    print(report)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
