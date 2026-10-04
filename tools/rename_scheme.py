#!/usr/bin/env python3
"""Switch this repository to another naming scheme, before the first wake.

    python tools/rename_scheme.py bureau          # a preset from naming/
    python tools/rename_scheme.py my-names.json   # your own scheme file

What it does, in order:

1. Refuses if the agent has already woken, or if Git has uncommitted changes.
2. Shows a dry run: every move and how many names change in each file.
3. Asks before applying (skip with --yes).
4. Backs up the repository, moves files and folders, rewrites names in docs and
   config (whole names only), and writes naming.json.
5. Runs the test suite. If anything fails, restores the backup.

Code and tests never change: they read every name from naming.json through
tools/names.py. See naming/ for the presets.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools.names import PLAIN  # noqa: E402

# Never rewritten: code reads names through tools/names.py, and these are
# either generated, binary, legal text, or the scheme definitions themselves.
SKIP_DIRS = {
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache",
    "tools", "tests", "naming", "fixtures",
}
SKIP_FILES = {"naming.json", "LICENSE"}
TEXT_SUFFIXES = {".md", ".json", ".txt", ".yml", ".yaml", ".toml", ".ini", ".example"}
TEXT_NAMES = {".gitignore"}


class RenameRefused(Exception):
    pass


def read(path: Path) -> str:
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def write(path: Path, text: str) -> None:
    # newline="" keeps each file's own line endings exactly as they were
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def load_scheme_from_data(data: dict, default_name: str = "custom") -> dict:
    merged = {section: {**defaults, **(data.get(section) or {})} for section, defaults in PLAIN.items()}
    merged["scheme"] = str(data.get("scheme", default_name))
    return merged


def load_scheme(path: Path) -> dict:
    return load_scheme_from_data(json.loads(path.read_text(encoding="utf-8")), path.stem)


def resolve_target(arg: str, repo: Path) -> Path:
    candidate = Path(arg)
    if candidate.suffix != ".json":
        candidate = repo / "naming" / f"{arg}.json"
    if not candidate.is_file():
        raise RenameRefused(f"no scheme file at {candidate}")
    return candidate


def rel(scheme: dict, key: str, dirs: dict | None = None) -> str:
    return scheme["paths"][key].format(**(dirs or scheme["dirs"]))


def validate(scheme: dict) -> None:
    seen: dict[str, str] = {}
    for key in scheme["paths"]:
        path = rel(scheme, key)
        if path in seen:
            raise RenameRefused(f"'{key}' and '{seen[path]}' would both be {path}")
        if path.startswith("/") or ".." in Path(path).parts:
            raise RenameRefused(f"'{key}' must be a relative path inside the repository: {path}")
        seen[path] = key
    tools = list(scheme["tools"].values())
    if len(tools) != len(set(tools)):
        raise RenameRefused("two tools would share one exposed name")
    for name in tools:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name):
            raise RenameRefused(f"tool name {name!r} must be lowercase letters, digits and _")


def check_not_woken(repo: Path, current: dict) -> None:
    budget = repo / "budget.json"
    if budget.exists():
        try:
            if int(json.loads(budget.read_text(encoding="utf-8")).get("wake_count", 0)) > 0:
                raise RenameRefused("the agent has already woken (budget.json wake_count > 0)")
        except (ValueError, TypeError, json.JSONDecodeError):
            raise RenameRefused("budget.json is unreadable; fix it before renaming")
    journal = repo / rel(current, "journal")
    if journal.exists() and any(p.name != "README.md" for p in journal.rglob("*.md")):
        raise RenameRefused(f"{rel(current, 'journal')} already has entries")


def check_git_clean(repo: Path) -> str:
    if not (repo / ".git").exists():
        return "no Git repository here: relying on the backup only"
    result = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True)
    if result.returncode != 0:
        raise RenameRefused("could not run git status")
    if result.stdout.strip():
        raise RenameRefused("Git has uncommitted changes; commit or stash them first")
    return "Git tree is clean"


def plan_moves(current: dict, target: dict) -> list[tuple[str, str]]:
    moves: list[tuple[str, str]] = []
    for key, old_dir in current["dirs"].items():
        if old_dir != target["dirs"][key]:
            moves.append((old_dir, target["dirs"][key]))
    for key in current["paths"]:
        # after the top-level folders move, the old leaf sits under the new folder
        old = rel(current, key, target["dirs"])
        new = rel(target, key)
        if old != new:
            moves.append((old, new))
    return moves


def replacement_map(current: dict, target: dict) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for key in current["paths"]:
        old, new = rel(current, key), rel(target, key)
        if old != new:
            pairs[old] = new
            old_leaf, new_leaf = Path(old).name, Path(new).name
            if old_leaf != new_leaf:
                if "." in old_leaf:
                    pairs.setdefault(old_leaf, new_leaf)
                else:
                    # Only exact mentions of the folder itself: a bare leaf like
                    # "places/" can also be an unrelated subfolder name.
                    pairs.setdefault(f"`{old_leaf}/`", f"`{new_leaf}/`")
                    pairs.setdefault(f"### {old_leaf}/", f"### {new_leaf}/")
    for key, old in current["dirs"].items():
        new = target["dirs"][key]
        if old != new:
            pairs[old + "/"] = new + "/"
            pairs[f"`{old}`"] = f"`{new}`"
    for function in set(current["tools"]) | set(target["tools"]):
        old = current["tools"].get(function, function)
        new = target["tools"].get(function, function)
        if old != new:
            pairs[old] = new
    return pairs


def build_rewriter(pairs: dict[str, str], current: dict, target: dict):
    keys = sorted(pairs, key=len, reverse=True)
    pattern = re.compile(
        r"(?<![\w-])("
        + "|".join(re.escape(k) + ("" if k.endswith("/") else r"(?![\w-])") for k in keys)
        + r")"
    ) if keys else None
    old_wake, new_wake = current["wake_id"], target["wake_id"]
    wake_pattern = None
    if old_wake != new_wake:
        wake_pattern = re.compile(
            r"(?<![\w-])" + re.escape(old_wake["prefix"]) + r"(\d{" + old_wake["width"] + r"})(?![\d])"
        )

    old_placeholder = old_wake["prefix"] + "N" * int(old_wake["width"])
    new_placeholder = new_wake["prefix"] + "N" * int(new_wake["width"])

    def rewrite(text: str) -> tuple[str, int]:
        count = 0
        if old_placeholder != new_placeholder and old_placeholder in text:
            count += text.count(old_placeholder)
            text = text.replace(old_placeholder, new_placeholder)
        if pattern:
            text, n = pattern.subn(lambda m: pairs[m.group(1)], text)
            count += n
        if wake_pattern:
            text, n = wake_pattern.subn(
                lambda m: f"{new_wake['prefix']}{int(m.group(1)):0{int(new_wake['width'])}d}", text
            )
            count += n
        return text, count

    return rewrite


def text_files(repo: Path):
    for path in sorted(repo.rglob("*")):
        if not path.is_file():
            continue
        parts = set(path.relative_to(repo).parts[:-1])
        if parts & SKIP_DIRS or path.name in SKIP_FILES:
            continue
        if path.suffix in TEXT_SUFFIXES or path.name in TEXT_NAMES:
            yield path


def rewrite_headings(repo: Path, current: dict, target: dict) -> list[str]:
    changed = []
    owners = {"identity": "identity", "governance": "governance", "operator": "operator",
              "open_threads": "open_threads"}
    for heading_key, path_key in owners.items():
        old, new = current["headings"][heading_key], target["headings"][heading_key]
        path = repo / rel(target, path_key)
        if old != new and path.exists():
            text = read(path)
            if text.startswith(old):
                write(path, new + text[len(old):])
                changed.append(rel(target, path_key))
    old, new = current["headings"]["works_index"], target["headings"]["works_index"]
    index = repo / rel(target, "works") / "index.md"
    if old != new and index.exists():
        text = read(index)
        if text.startswith(old):
            write(index, new + text[len(old):])
            changed.append(f"{rel(target, 'works')}/index.md")
    return changed


def backup(repo: Path) -> Path:
    dest = Path(tempfile.mkdtemp(prefix="rename-scheme-backup-"))
    ignore = shutil.ignore_patterns(".git", ".venv", "venv", "__pycache__", ".pytest_cache")
    shutil.copytree(repo, dest / "repo", ignore=ignore)
    return dest / "repo"


def restore(repo: Path, saved: Path) -> None:
    for entry in repo.iterdir():
        if entry.name in {".git", ".venv", "venv"}:
            continue
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    for entry in saved.iterdir():
        if entry.is_dir():
            shutil.copytree(entry, repo / entry.name)
        else:
            shutil.copy2(entry, repo / entry.name)


def run_tests(repo: Path) -> bool:
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    if not (repo / ".git").exists():
        cmd += ["--deselect", "tests/test_secret_hygiene.py"]
    return subprocess.run(cmd, cwd=repo).returncode == 0


def rename(repo: Path, target_path: Path, assume_yes: bool = False, skip_tests: bool = False,
           out=print) -> int:
    current = load_scheme(repo / "naming.json")
    target = load_scheme(target_path)
    validate(target)
    if current == target:
        out(f"Already on the '{target['scheme']}' scheme. Nothing to do.")
        return 0
    check_not_woken(repo, current)
    out(check_git_clean(repo))

    moves = [(old, new) for old, new in plan_moves(current, target)]
    rewrite = build_rewriter(replacement_map(current, target), current, target)

    out(f"\nRename '{current['scheme']}' -> '{target['scheme']}'\n")
    out("Moves:")
    for old, new in moves:
        out(f"  {old}  ->  {new}")
    out("\nName changes in docs and config:")
    for path in text_files(repo):
        _, count = rewrite(read(path))
        if count:
            out(f"  {path.relative_to(repo).as_posix()}: {count}")

    if not assume_yes:
        try:
            answer = input("\nApply? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() != "y":
            out("Cancelled. Nothing changed.")
            return 1

    saved = backup(repo)
    try:
        for old, new in moves:
            source, dest = repo / old, repo / new
            if not source.exists():
                continue
            if dest.exists() and not source.samefile(dest):
                raise RenameRefused(f"{new} already exists")
            dest.parent.mkdir(parents=True, exist_ok=True)
            # A case-only rename (GOALS.md -> goals.md) is the same file on
            # Windows and macOS, so go through a temporary name.
            step = source.with_name(source.name + ".renaming")
            shutil.move(str(source), str(step))
            shutil.move(str(step), str(dest))
        for path in text_files(repo):
            new_text, count = rewrite(read(path))
            if count:
                write(path, new_text)
        rewrite_headings(repo, current, target)
        scheme_out = {"scheme": target["scheme"], **{k: target[k] for k in PLAIN}}
        write(repo / "naming.json", json.dumps(scheme_out, indent=2) + "\n")
        if not skip_tests and not run_tests(repo):
            raise RenameRefused("the tests failed after renaming")
    except Exception as error:
        restore(repo, saved)
        out(f"\nRestored the repository from backup: {error}")
        return 2
    shutil.rmtree(saved.parent, ignore_errors=True)
    out(f"\nDone. The repository now uses the '{target['scheme']}' scheme.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scheme", help="a preset name from naming/ (bureau, classic, plain) or a .json path")
    parser.add_argument("--yes", action="store_true", help="apply without asking")
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    parser.add_argument("--skip-tests", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        return rename(args.repo.resolve(), resolve_target(args.scheme, args.repo.resolve()),
                      assume_yes=args.yes, skip_tests=args.skip_tests)
    except RenameRefused as error:
        print(f"REFUSED: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
