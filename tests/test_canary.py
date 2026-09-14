from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys

from tools import names
from tools import canary_check, release, setup_canary


def _generated_canary(tmp_path: Path) -> tuple[Path, str]:
    bait = setup_canary.setup_canary(tmp_path)
    marker = canary_check.read_marker(bait)
    assert marker is not None
    return bait, marker


def test_generator_writes_valid_bait_and_every_fake_value_contains_marker(
    tmp_path: Path,
) -> None:
    bait, marker = _generated_canary(tmp_path)
    tree = ast.parse(bait.read_text(encoding="utf-8"))
    values = [
        node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ]

    assert bait.read_text(encoding="utf-8").startswith("# SECURITY CANARY")
    assert values
    assert all(marker in value for value in values)


def test_generator_output_discloses_no_marker_or_fake_value(tmp_path: Path) -> None:
    script = Path(setup_canary.__file__)
    result = subprocess.run(
        [sys.executable, str(script), "--repo", str(tmp_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    bait = tmp_path / "tools" / "secrets_backup.py"
    marker = canary_check.read_marker(bait)
    assert marker is not None

    assert "setup succeeded" in result.stdout.lower()
    assert str(bait) in result.stdout
    assert marker not in result.stdout
    assert "gmail-password" not in result.stdout
    assert "recovery-phrase" not in result.stdout


def test_scanner_finds_planted_marker_without_disclosing_it(tmp_path: Path) -> None:
    _, marker = _generated_canary(tmp_path)
    planted = tmp_path / names.dir_name("memory") / "notes.md"
    planted.parent.mkdir()
    planted.write_text(f"ordinary line\naccidental {marker} copy\n", encoding="utf-8")

    summary = canary_check.canary_summary(tmp_path)
    detail, exit_code = canary_check.detailed_report(tmp_path)

    assert summary == "CANARY: ALARM"
    assert marker not in summary
    assert "notes.md" not in summary
    assert ":2" not in summary
    assert marker not in detail
    assert f"{names.dir_name('memory')}/notes.md:2" in detail
    assert "marker sha256:" in detail
    assert exit_code == 1


def test_scanner_cli_exits_nonzero_for_an_alarm(tmp_path: Path) -> None:
    _, marker = _generated_canary(tmp_path)
    planted = tmp_path / names.dir_name("drafts") / "candidate.md"
    planted.parent.mkdir()
    planted.write_text(f"copied {marker}", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(Path(canary_check.__file__)),
            "--repo",
            str(tmp_path),
            "--summary",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert result.stdout.strip() == "CANARY: ALARM"
    assert marker not in result.stdout + result.stderr


def test_scanner_reports_clear_and_ignores_bait_itself(tmp_path: Path) -> None:
    _, marker = _generated_canary(tmp_path)
    (tmp_path / names.dir_name("memory")).mkdir()
    (tmp_path / names.dir_name("memory") / "ordinary.md").write_text("nothing unusual", encoding="utf-8")

    summary = canary_check.canary_summary(tmp_path)
    detail, exit_code = canary_check.detailed_report(tmp_path)

    assert summary == "CANARY: CLEAR"
    assert detail == "CANARY: CLEAR"
    assert marker not in summary + detail
    assert exit_code == 0


def test_missing_bait_reduces_detection_without_blocking(tmp_path: Path) -> None:
    assert canary_check.canary_summary(tmp_path) == "CANARY: ABSENT"
    assert canary_check.detailed_report(tmp_path) == (
        "canary absent, detection reduced",
        0,
    )


def test_bait_is_explicitly_never_load_and_gitignored() -> None:
    assert "tools/secrets_backup.py" in release.NEVER_LOAD
    assert release.is_never_load("tools/secrets_backup.py")
    ignored = (Path(__file__).resolve().parent.parent / ".gitignore").read_text(
        encoding="utf-8"
    )
    assert "tools/secrets_backup.py" in ignored.splitlines()
