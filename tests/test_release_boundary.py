"""The release boundary must stay derived from .gitignore, never duplicated.

If the harness kept its own copy of the private path list, the two would drift.
The day someone adds a sensitive file and forgets to gitignore it, they would
get both failures at once: published to GitHub, and readable while composing a
reply to a stranger. These tests keep the single source of truth honest.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tools import names
from tools import release

REPO = Path(__file__).resolve().parent.parent


def test_private_set_is_derived_from_gitignore_not_hardcoded() -> None:
    """The decisive test: the derived set must equal what .gitignore says.

    A hardcoded list would pass the individual membership tests below while
    silently ignoring a newly gitignored directory. This one cannot.
    """
    entries = {
        entry.rstrip("/")
        for entry in release._gitignore_entries()
        if not entry.startswith(("!", "*"))
    }
    excluded = {e.rstrip("/") for e in release.IGNORE_NOISE | release.NEVER_LOAD}
    assert release.private_prefixes() == frozenset(entries - excluded)


def test_drafts_are_private() -> None:
    """A draft is unpublished thought, not a dispatch. Operator decision."""
    assert release.is_private(names.dir_name("drafts"))
    assert release.is_private(f"{names.dir_name('drafts')}/archive/2026-09-09-koi-offering.md")


def test_drafts_are_not_tracked_by_git() -> None:
    result = subprocess.run(
        ["git", "ls-files", f"{names.dir_name('drafts')}/"],
        cwd=REPO, capture_output=True, text=True, check=False,
    )
    assert not result.stdout.strip(), f"{names.dir_name('drafts')}/ is tracked — it would be published"


def test_residents_are_public() -> None:
    """Operator decision: resident dossiers stay published.

    The boundary is publication. Carving an exception for a committed file
    would protect nothing — it is on GitHub — while turning a principle into a
    hand-maintained list. If the dossiers should not be readable, the fix is to
    stop publishing them, not to withhold them from replies.
    """
    assert release.is_public(f"{names.rel('residents')}/index.md")
    assert release.is_public(f"{names.rel('residents')}/deepwell.md")
    assert not release.is_private(f"{names.rel('residents')}/deepwell.md")


def test_working_state_directories_are_private() -> None:
    for path in (f"{names.dir_name('memory')}/replay", f"{names.dir_name('memory')}/correspondence", "operator",
                 f"{names.dir_name('memory')}/private",
                 f"{names.dir_name('memory')}/acquired", f"{names.dir_name('memory')}/acquired-index.json"):
        assert release.is_private(path), f"{path} should be private context"


def test_acquired_artifacts_are_private_but_loadable() -> None:
    for path in (f"{names.dir_name('memory')}/acquired/21-guide.md", f"{names.dir_name('memory')}/acquired-index.json"):
        assert release.is_private(path)
        assert not release.is_never_load(path)
        assert not release.is_public(path)
    assert release.is_public(f"{names.dir_name('memory')}/acquired-notes.md")
    assert not release.is_private(f"{names.dir_name('memory')}/acquired-notes.md")


def test_published_files_are_public() -> None:
    """Committed files are world-readable already — withholding them is theatre."""
    for path in (f"{names.rel('identity')}", f"{names.rel('governance')}", f"{names.rel('goals')}", f"{names.rel('lessons')}",
                 f"{names.rel('open_threads')}",
                 f"{names.rel('city_rules')}", f"{names.rel('journal')}/x.md"):
        assert release.is_public(path), f"{path} is committed — it is public"
        assert not release.is_private(path)


def test_credentials_are_never_load() -> None:
    for secret in ("tools/secrets.py", "wallet-config.json", "operator_private.pem",
                   ".agents/mcp_config.json"):
        assert release.is_never_load(secret)
        assert not release.is_public(secret)


def test_never_load_is_not_merely_private() -> None:
    """A credential must not appear in the private set, or it would be treated
    as 'load later' rather than 'never load'."""
    for secret in release.NEVER_LOAD:
        assert secret.rstrip("/") not in release.private_prefixes()


def test_build_noise_is_not_private_context() -> None:
    for noise in ("__pycache__", ".venv", ".pytest_cache"):
        assert noise not in release.private_prefixes()


def test_rotating_backups_are_private_even_though_git_ignores_them_as_noise() -> None:
    for backup in (
        f"{names.rel('goals')}.20260913T183000.000000Z.bak",
        f"{names.rel('open_threads')}.20260913T183000.000000Z.bak",
    ):
        assert release.is_private(backup)
        assert not release.is_public(backup)


def test_nested_paths_resolve_against_prefixes() -> None:
    assert release.is_private(f"{names.dir_name('memory')}/correspondence/operator/2026-09-11-in.md")
    assert release.is_private("operator/commands/x.md")
    assert not release.is_private(f"{names.dir_name('memory')}/correspondence_notes.md")  # prefix, not dir


def test_windows_separators_are_handled() -> None:
    assert release.is_private(names.dir_name("memory") + r"\correspondence\operator\x.md")
    assert release.is_never_load(r"tools\secrets.py")


def test_private_directories_are_actually_gitignored() -> None:
    ignored = (REPO / ".gitignore").read_text(encoding="utf-8")
    for entry in (f"{names.dir_name('memory')}/replay/", f"{names.dir_name('memory')}/correspondence/", "operator/",
                  f"{names.dir_name('memory')}/acquired/", f"{names.dir_name('memory')}/acquired-index.json"):
        assert entry in ignored, f"{entry} must be gitignored — it is private."


def test_no_private_directory_is_tracked_by_git() -> None:
    """Belt and braces: gitignored is not the same as untracked."""
    for entry in (f"{names.dir_name('memory')}/replay", f"{names.dir_name('memory')}/correspondence", "operator",
                  f"{names.dir_name('memory')}/acquired", f"{names.dir_name('memory')}/acquired-index.json"):
        result = subprocess.run(
            ["git", "ls-files", entry],
            cwd=REPO, capture_output=True, text=True, check=False,
        )
        assert not result.stdout.strip(), f"{entry} is tracked but should be private"
