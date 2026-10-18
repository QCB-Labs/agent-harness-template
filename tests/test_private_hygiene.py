"""Whole-repository privacy check.

Commit hooks inspect new changes as they are committed. This test also reads
every tracked file, so older leaks are caught by the next test run.

Two checks reuse ``tools/outgoing_check.py``:
1. Local user paths. This check always runs, including when the private pattern
   file is unavailable.
2. Private patterns loaded at runtime from the global pattern file used by the
   Git hooks. A fresh clone or CI environment may not have that file, so only
   this check is skipped when it is absent.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools import outgoing_check

REPO_ROOT = Path(__file__).resolve().parent.parent

# Files that deliberately contain synthetic examples for checker tests.
FIXTURE_FILES = {
    "tools/outgoing_check.py",
    "tools/release_scan.py",
    "tests/test_outgoing_check.py",
    "tests/test_release_scan.py",
    "tests/test_private_hygiene.py",
}


def _tracked_text_files() -> list[tuple[str, str]]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"git ls-files failed: {result.stderr}"

    files = []
    for rel in result.stdout.splitlines():
        rel = rel.strip()
        if not rel or rel in FIXTURE_FILES:
            continue
        path = REPO_ROOT / rel
        if not path.is_file():
            continue
        try:
            files.append((rel, path.read_text(encoding="utf-8")))
        except UnicodeDecodeError:
            continue
    return files


def _lines_with(files, finder) -> list[str]:
    hits = []
    for rel, contents in files:
        for line_number, line in enumerate(contents.splitlines(), start=1):
            if finder(line):
                hits.append(f"{rel}:{line_number}")
    return hits


def test_no_local_user_paths_in_tracked_files() -> None:
    hits = _lines_with(_tracked_text_files(), outgoing_check._check_local_paths)
    assert not hits, (
        "Tracked files contain local user paths (use a repo-relative path):\n"
        + "\n".join(hits)
    )


def test_no_private_patterns_in_tracked_files(monkeypatch) -> None:
    # Always use the global hook file; other tests may point the override at a
    # synthetic fixture.
    monkeypatch.delenv("AGENT_PRIVATE_PATTERNS", raising=False)
    pattern_file = outgoing_check._get_pattern_file_path()
    if not pattern_file.is_file():
        pytest.skip(
            "global private pattern file is not installed; "
            "private-pattern scan unavailable"
        )

    patterns = outgoing_check.load_private_patterns()  # once per scan
    if patterns is None:
        # The file exists but could not be read: never pass unchecked.
        pytest.fail("private pattern file could not be read")

    def private(line: str) -> bool:
        return any(f.kind == "private-pattern"
                   for f in outgoing_check.check_loaded_patterns(line, patterns))

    hits = _lines_with(_tracked_text_files(), private)
    # Report file and line only; never the matched text.
    assert not hits, "Tracked files contain private patterns:\n" + "\n".join(hits)
