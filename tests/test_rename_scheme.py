"""The naming-scheme switcher: refuses unsafe renames, round-trips exactly."""

from __future__ import annotations

import filecmp
import json
import shutil
from pathlib import Path

import pytest

from tools import rename_scheme

REPO = Path(rename_scheme.__file__).resolve().parent.parent
IGNORE = shutil.ignore_patterns(".git", ".venv", "venv", "__pycache__", ".pytest_cache")


@pytest.fixture
def copy(tmp_path: Path) -> Path:
    dest = tmp_path / "repo"
    shutil.copytree(REPO, dest, ignore=IGNORE)
    return dest


def switch(repo: Path, scheme: str, answer: bool = True) -> int:
    return rename_scheme.rename(
        repo, repo / "naming" / f"{scheme}.json",
        assume_yes=answer, skip_tests=True, out=lambda *_: None,
    )


def tree_differences(a: Path, b: Path) -> list[str]:
    diffs: list[str] = []

    def walk(cmp: filecmp.dircmp, prefix: str) -> None:
        diffs.extend(prefix + n for n in cmp.left_only + cmp.right_only + cmp.diff_files)
        for name, sub in cmp.subdirs.items():
            walk(sub, f"{prefix}{name}/")

    walk(filecmp.dircmp(a, b, ignore=["__pycache__", ".pytest_cache"]), "")
    return diffs


def current_scheme(repo: Path) -> str:
    return json.loads((repo / "naming.json").read_text(encoding="utf-8"))["scheme"]


def test_every_preset_is_valid() -> None:
    for preset in (REPO / "naming").glob("*.json"):
        rename_scheme.validate(rename_scheme.load_scheme(preset))


def test_presets_rename_only_real_tools() -> None:
    harness = (REPO / "tools" / "harness_mcp.py").read_text(encoding="utf-8")
    for preset in (REPO / "naming").glob("*.json"):
        for function in rename_scheme.load_scheme(preset)["tools"]:
            assert f"def {function}(" in harness, (preset.name, function)


def test_round_trip_through_every_scheme_is_byte_identical(tmp_path: Path, copy: Path) -> None:
    original = tmp_path / "original"
    shutil.copytree(copy, original)
    start = current_scheme(copy)
    others = [p.stem for p in sorted((copy / "naming").glob("*.json")) if p.stem != start]
    for scheme in others + [start]:
        assert switch(copy, scheme) == 0, scheme
    assert tree_differences(original, copy) == []


def test_switch_moves_files_and_records_the_scheme(copy: Path) -> None:
    target = "classic" if current_scheme(copy) != "classic" else "bureau"
    scheme = rename_scheme.load_scheme(copy / "naming" / f"{target}.json")
    assert switch(copy, target) == 0
    assert current_scheme(copy) == target
    for key in ("identity", "governance", "journal", "atlas"):
        assert (copy / rename_scheme.rel(scheme, key)).exists(), key


def test_cancelling_changes_nothing(tmp_path: Path, copy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = tmp_path / "original"
    shutil.copytree(copy, original)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    target = "classic" if current_scheme(copy) != "classic" else "bureau"
    assert switch(copy, target, answer=False) == 1
    assert tree_differences(original, copy) == []


def test_refuses_after_the_first_wake(copy: Path) -> None:
    budget = json.loads((copy / "budget.json").read_text(encoding="utf-8"))
    budget["wake_count"] = 1
    (copy / "budget.json").write_text(json.dumps(budget), encoding="utf-8")
    with pytest.raises(rename_scheme.RenameRefused, match="already woken"):
        switch(copy, "classic" if current_scheme(copy) != "classic" else "bureau")


def test_refuses_when_journal_has_entries(copy: Path) -> None:
    current = rename_scheme.load_scheme(copy / "naming.json")
    entry = copy / rename_scheme.rel(current, "journal") / "2026-09" / "entry.md"
    entry.parent.mkdir(parents=True)
    entry.write_text("a wake", encoding="utf-8")
    with pytest.raises(rename_scheme.RenameRefused, match="already has entries"):
        switch(copy, "classic" if current_scheme(copy) != "classic" else "bureau")


def test_refuses_a_scheme_that_collides(copy: Path) -> None:
    bad = json.loads((copy / "naming" / "classic.json").read_text(encoding="utf-8"))
    bad["paths"]["lessons"] = bad["paths"]["wishlist"]
    with pytest.raises(rename_scheme.RenameRefused, match="would both be"):
        rename_scheme.validate(rename_scheme.load_scheme_from_data(bad))


def test_refuses_paths_outside_the_repository(copy: Path) -> None:
    bad = json.loads((copy / "naming" / "classic.json").read_text(encoding="utf-8"))
    bad["paths"]["identity"] = "../IDENTITY.md"
    with pytest.raises(rename_scheme.RenameRefused, match="inside the repository"):
        rename_scheme.validate(rename_scheme.load_scheme_from_data(bad))


def test_a_failing_test_run_restores_everything(
    tmp_path: Path, copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = tmp_path / "original"
    shutil.copytree(copy, original)
    monkeypatch.setattr(rename_scheme, "run_tests", lambda _repo: False)
    target = "classic" if current_scheme(copy) != "classic" else "bureau"
    result = rename_scheme.rename(
        copy, copy / "naming" / f"{target}.json", assume_yes=True, out=lambda *_: None
    )
    assert result == 2
    assert tree_differences(original, copy) == []
