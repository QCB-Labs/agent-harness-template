#!/usr/bin/env python3
"""Install the committed Git hooks into .git/hooks.

Run once after creating the repository (and again after pulling hook changes):

    python hooks/install.py

Git does not copy hooks when a repository is cloned, so each copy of the
repository needs this step.
"""

from __future__ import annotations

import shutil
import stat
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOKS = ("pre-commit", "commit-msg")


def main() -> int:
    git_hooks = REPO / ".git" / "hooks"
    if not git_hooks.parent.is_dir():
        print("No .git folder here. Run `git init` first.")
        return 1
    git_hooks.mkdir(exist_ok=True)
    for name in HOOKS:
        target = git_hooks / name
        shutil.copyfile(REPO / "hooks" / name, target)
        target.chmod(target.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        print(f"Installed {name}")
    patterns = REPO / "hooks" / "private-patterns.txt"
    if not patterns.exists():
        print("\nNext: copy hooks/private-patterns.example.txt to hooks/private-patterns.txt")
        print("and list your private details in it. That file is gitignored.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
