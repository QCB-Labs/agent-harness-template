"""Private casefiles: one per source, a permanent row for every flag.

The memory airlock (tools/verdicts.py) flags a line of an impression that
holds a listed reliability phrase. A flag can vanish from his file when he
rewrites the line; its row here never does, so flags about one source stack
over time.

Two files per source, in a folder outside the repository (paths.cases_dir):

- ``<kind>-<key>.flags.md``: the harness's. One row appended per flag, with
  plain appends; earlier rows are never rewritten.
- ``<kind>-<key>.md``: the operator's dated evidence, written by hand.
  Only rows named by an evidence entry are checked. The harness reads this
  file and never writes it; agent text cannot supply evidence.

The agent never reads casefiles: no tool returns their contents, names or
location. Every function here fails quietly: a failure is logged for the
operator and never changes or refuses the agent's write.
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from tools import paths

FLAGS_HEAD = ("| Date (UTC) | File | Phrase | Line as saved |\n"
              "|---|---|---|---|\n")
_HANDLE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


def resident(handle: str) -> tuple[str, str]:
    """Residents are keyed by their handle, exactly as the harness validated
    it (names in the city are permanent; the city id isn't always known at
    save time)."""
    clean = str(handle).strip().lower()
    if not _HANDLE.fullmatch(clean):
        raise ValueError("invalid resident handle")
    return ("resident", clean)


def place(place_id: int) -> tuple[str, str]:
    return ("place", str(int(place_id)))


def _folder(repo: Path | None) -> Path | None:
    return paths.cases_dir(also_outside=repo)


def flags_path(source: tuple[str, str], repo: Path | None = None) -> Path | None:
    folder = _folder(repo)
    return None if folder is None else folder / f"{source[0]}-{source[1]}.flags.md"


def judgement_path(source: tuple[str, str], repo: Path | None = None) -> Path | None:
    folder = _folder(repo)
    return None if folder is None else folder / f"{source[0]}-{source[1]}.md"


def log_failure(what: str, error: BaseException, repo: Path | None = None) -> None:
    """One line for the operator, outside the repository. Never raises, and
    never writes a log that would land inside a repository."""
    try:
        folder = paths.operator_log_dir()
        if paths.inside_repo(folder, also=repo):
            return
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with (folder / "casefile-errors.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {what}: {type(error).__name__}\n")
    except Exception:
        pass


def covered_rows(source: tuple[str, str], repo: Path | None = None) -> set[str]:
    """Row bodies named by Evidence entries in the operator's file. Fail open."""
    try:
        operator = judgement_path(source, repo)
        if operator is None or not operator.is_file():
            return set()
        cutoff = operator.stat().st_mtime
        evidence = operator.read_text(encoding="utf-8")
        selections: list[str] = []
        active = False
        for line in evidence.splitlines():
            heading = re.match(r"^##(?:[ \t]+|$)(.*)", line)
            if heading:
                active = bool(re.match(r"Evidence(?:\b|$)", heading[1]))
            elif active and line.startswith("Covers:"):
                value = line[len("Covers:"):].strip()
                if value == "all" or re.fullmatch(
                        r"[1-9][0-9]*(?:\s*[-–]\s*[1-9][0-9]*)?(?:\s*,\s*[1-9][0-9]*(?:\s*[-–]\s*[1-9][0-9]*)?)*", value):
                    ranges = [re.split(r"\s*[-–]\s*", item.strip())
                              for item in value.split(",")] if value != "all" else []
                    if any(len(pair) == 2 and int(pair[0]) > int(pair[1]) for pair in ranges):
                        log_failure("covered_rows Covers", ValueError("descending range"), repo)
                    else:
                        selections.append(value)
                else:
                    log_failure("covered_rows Covers", ValueError("invalid row selection"), repo)
        path = flags_path(source, repo)
        if not selections or path is None or not path.is_file():
            return set()
        intervals = []
        for value in selections:
            if value != "all":
                for item in value.split(","):
                    pair = re.split(r"\s*[-–]\s*", item.strip())
                    intervals.append((int(pair[0]), int(pair[-1])))
        covered: set[str] = set()
        number = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            body = _row_body(line)
            if body is None:
                continue
            number += 1
            named = any(lo <= number <= hi for lo, hi in intervals)
            if not named and "all" in selections:
                stamp = line.split(" | ", 1)[0][2:]
                dated = datetime.strptime(stamp, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                named = dated.timestamp() <= cutoff
            if named:
                covered.add(body)
        return covered
    except Exception as e:  # noqa: BLE001 - evidence never breaks a save
        log_failure("covered_rows", e, repo)
        return set()


def is_checked(source: tuple[str, str], line_text: str, phrases: str,
               rel_file: str, repo: Path | None = None) -> bool:
    """True only for this file, phrase list and line's covered row body."""
    try:
        body = f"| {_cell(rel_file)} | {_cell(phrases)} | {_cell(line_text)} |"
        return body in covered_rows(source, repo)
    except Exception as e:
        log_failure("is_checked", e, repo)
        return False


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def _lock(path: Path, timeout: float = 5.0) -> Path:
    lock = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return lock
        except FileExistsError:
            if time.monotonic() > deadline:
                raise TimeoutError("casefile lock")
            time.sleep(0.05)


def _row_body(text: str) -> str | None:
    """A row without its date: '| file | phrase | line |'. None if not a row."""
    if not text.startswith("| ") or text.startswith("| Date (UTC) |"):
        return None
    parts = text.split(" | ", 1)
    return "| " + parts[1] if len(parts) == 2 else None


def append_flags(source: tuple[str, str], title: str, rel_file: str,
                 rows: list[tuple[str, str]], when: datetime | None = None,
                 repo: Path | None = None) -> int | None:
    """Append one row per (line text, phrases) not already recorded for this
    file and line. Returns rows added (0 if all were there), or None when
    casefiles are off or the append failed."""
    try:
        path = flags_path(source, repo)
        if path is None:
            return None
        if not rows:
            return 0
        stamp = (when or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M")
        lock = _lock(path)
        try:
            seen: set[str] = set()
            if path.is_file():
                for line in path.read_text(encoding="utf-8").splitlines():
                    body = _row_body(line)
                    if body:
                        seen.add(body)
            added: list[str] = []
            for line_text, phrases in rows:
                body = f"| {_cell(rel_file)} | {_cell(phrases)} | {_cell(line_text)} |"
                if body in seen:
                    continue
                seen.add(body)
                added.append(f"| {stamp} {body}")
            if not added:
                return 0
            new_file = not path.is_file()
            with path.open("a", encoding="utf-8", newline="\n") as fh:
                if new_file:
                    fh.write(f"# {source[0]} {source[1]}: {_cell(title) or source[1]}\n\n"
                             "*Written by the harness: one row per flag, appended, never edited.\n"
                             "The operator's evidence is in the file of the same name without\n"
                             "`.flags`.*\n\n" + FLAGS_HEAD)
                fh.write("\n".join(added) + "\n")
            return len(added)
        finally:
            lock.unlink(missing_ok=True)
    except Exception as e:  # noqa: BLE001 - a casefile never breaks a save
        log_failure("append_flags", e, repo)
        return None
