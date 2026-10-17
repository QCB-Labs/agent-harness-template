"""Choose the operator hook's staged privacy check or full test suite."""
from __future__ import annotations

import subprocess

from tools import outgoing_check, names


MEMORY_FILES = frozenset({names.rel("goals"), names.rel("goals_archive"), "budget.json"})
MEMORY_PREFIXES = (names.dir_name("memory") + "/",)


def _git(*arguments: str) -> bytes:
    return subprocess.run(
        ["git", *arguments], check=True, capture_output=True,
    ).stdout


def _paths(data: bytes) -> list[str]:
    if not data:
        return []
    if not data.endswith(b"\0"):
        raise ValueError("incomplete git output")
    return data[:-1].decode("utf-8").split("\0")


def _check() -> int:
    # Disable rename detection so both the removed source and added target
    # participate, irrespective of repository diff configuration.
    arguments = ("diff", "--cached", "--name-only", "-z", "--no-renames")
    paths = _paths(_git(*arguments))
    if not paths:
        print("full suite: nothing staged")
        return 2
    for path in paths:
        if path not in MEMORY_FILES and not path.startswith(MEMORY_PREFIXES):
            print(f"full suite: {path}")
            return 2

    deleted = set(_paths(_git(*arguments, "--diff-filter=D")))
    patterns = outgoing_check.load_private_patterns()  # once per run
    failed = patterns is None
    hits = []
    for path in paths:
        if path in deleted:
            continue
        content = _git("show", f":{path}")
        try:
            if b"\x00" in content:
                # UTF-16 without a byte-order mark decodes as UTF-8 with NULs
                # between the letters, and hides every word from the checks.
                raise UnicodeError("NUL byte")
            text = content.decode("utf-8")
        except UnicodeError:
            # Text that can't be read can't be checked: refuse, never pass.
            hits.append(f"{path}: not UTF-8 text, cannot be checked")
            continue
        secret_hits = outgoing_check.live_secret_hits(path, text)
        secret_locations = {hit.rsplit(" matches secret pattern ", 1)[0]
                            for hit in secret_hits}
        for number, line in enumerate(text.splitlines(), start=1):
            private = outgoing_check.check_loaded_patterns(line, patterns)
            location = f"{path}:{number}"
            if (outgoing_check._check_local_paths(line)
                    or any(f.kind == "private-pattern" for f in private)
                    or location in secret_locations):
                hits.append(location)
    if failed or hits:
        print("memory-only commit: privacy check failed")
        for hit in hits:
            print(hit)
        return 1
    print(f"memory-only commit: privacy checks passed on {len(paths)} files")
    return 0


def main() -> int:
    try:
        return _check()
    except Exception:
        # Neither git stderr nor exception text is safe to disclose here.
        print("full suite: check unavailable")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
