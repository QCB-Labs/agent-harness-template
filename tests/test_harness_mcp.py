import datetime
import json
import os
from pathlib import Path
import shutil
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tools import names
from tools import harness_mcp


FIXED_NOW = datetime.datetime(2026, 9, 1, 12, 34, 56, tzinfo=datetime.timezone.utc)
CONVERSATION_ID = "11111111-1111-4111-8111-111111111111"


def _private_call(tool, *args, **kwargs):
    kwargs["conversation_id"] = CONVERSATION_ID
    return tool(*args, **kwargs)


def _write_operator_message(*args, **kwargs):
    return _private_call(harness_mcp.write_operator_message, *args, **kwargs)


def _stage_draft(*args, **kwargs):
    return _private_call(harness_mcp.stage_draft, *args, **kwargs)


def _read_drafts(*args, **kwargs):
    return _private_call(harness_mcp.read_drafts, *args, **kwargs)


def _archive_draft(*args, **kwargs):
    return _private_call(harness_mcp.archive_draft, *args, **kwargs)


@pytest.fixture
def isolated_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(harness_mcp, "REPO", tmp_path)
    return tmp_path


def test_read_soul_returns_full_utf8_content(isolated_repo: Path) -> None:
    expected = "# Test identity\n\nTransmit plainly. 🚀\n"
    (isolated_repo / names.rel("identity")).write_text(expected, encoding="utf-8")

    assert harness_mcp.read_identity() == expected


def test_repo_override_is_obeyed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    override = tmp_path / "sandbox"
    monkeypatch.setenv("AGENT_HARNESS_REPO", str(override))

    assert harness_mcp._resolve_repo() == override.resolve()


def test_repo_defaults_to_repository_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_HARNESS_REPO", raising=False)

    assert harness_mcp._resolve_repo() == Path(harness_mcp.__file__).resolve().parent.parent


def test_read_soul_returns_fallback_when_missing(isolated_repo: Path) -> None:
    assert harness_mcp.read_identity() == f"{names.rel('identity')} not found"


@pytest.mark.parametrize(
    ("reader", "relative_path", "expected"),
    [
        (harness_mcp.read_governance, Path(names.rel("governance")), "governance ⚖️\n"),
        (harness_mcp.read_goals, Path(names.rel("goals")), "goals 🎯\n"),
        (harness_mcp.read_lessons, Path(names.rel("lessons")), "lessons 🧭\n"),
        (harness_mcp.read_city_rules, Path(names.rel("city_rules")), "rules 🏙️\n"),
        (harness_mcp.read_wishlist, Path(names.rel("wishlist")), "wishlist ✨\n"),
    ],
)
def test_simple_reader_returns_full_utf8_content(
    isolated_repo: Path,
    reader,
    relative_path: Path,
    expected: str,
) -> None:
    path = isolated_repo / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(expected, encoding="utf-8")

    assert reader() == expected


@pytest.mark.parametrize(
    ("reader", "fallback"),
    [
        (harness_mcp.read_governance, f"{names.rel('governance')} not found"),
        (harness_mcp.read_goals, f"{names.rel('goals')} not found"),
        (harness_mcp.read_lessons, ""),
        (harness_mcp.read_city_rules, ""),
        (harness_mcp.read_wishlist, ""),
    ],
)
def test_simple_reader_returns_fallback_when_missing(
    isolated_repo: Path,
    reader,
    fallback: str,
) -> None:
    assert reader() == fallback


def test_read_recent_journal_returns_newest_five_in_order(
    isolated_repo: Path,
) -> None:
    journal_dir = isolated_repo / names.rel("journal")
    journal_dir.mkdir(parents=True)
    for number in range(1, 7):
        (journal_dir / f"2026-08-0{number}.md").write_text(
            f"entry {number}", encoding="utf-8"
        )
    (journal_dir / "ignored.txt").write_text("not a journal", encoding="utf-8")

    assert harness_mcp.read_recent_journal() == "\n\n---\n\n".join(
        f"entry {number}" for number in range(2, 7)
    )


def test_read_recent_journal_respects_custom_count(isolated_repo: Path) -> None:
    journal_dir = isolated_repo / names.rel("journal")
    journal_dir.mkdir(parents=True)
    for filename, content in [
        ("2026-08-03.md", "third"),
        ("2026-08-01.md", "first"),
        ("2026-08-02.md", "second"),
    ]:
        (journal_dir / filename).write_text(content, encoding="utf-8")

    assert harness_mcp.read_recent_journal(n=2) == "second\n\n---\n\nthird"


@pytest.mark.parametrize("create_directory", [False, True])
def test_read_recent_journal_handles_no_entries(
    isolated_repo: Path,
    create_directory: bool,
) -> None:
    if create_directory:
        (isolated_repo / names.rel("journal")).mkdir(parents=True)

    assert harness_mcp.read_recent_journal() == "(no journal entries)"


def test_read_recent_journal_finds_entries_in_monthly_subfolders(
    isolated_repo: Path,
) -> None:
    journal_dir = isolated_repo / names.rel("journal")
    aug = journal_dir / "2026-08"
    sep = journal_dir / "2026-09"
    aug.mkdir(parents=True)
    sep.mkdir(parents=True)
    (aug / f"{names.wake_id(45)}-2026-08-27.md").write_text("aug entry", encoding="utf-8")
    (sep / f"{names.wake_id(50)}-2026-09-02.md").write_text("sep entry", encoding="utf-8")

    result = harness_mcp.read_recent_journal(n=2)
    assert "aug entry" in result
    assert "sep entry" in result


def test_read_budget_returns_parsed_json(isolated_repo: Path) -> None:
    expected = {
        "credits": {"balance": 2.0, "last_checked": "2026-09-01T10:00:00+00:00", "source": "city.me"},
        "quotas": {
            "date": "2026-09-01", "notes_used": 3, "things_used": 1, "agreements_used": 0,
            "notes_limit": 50, "things_limit": 20, "agreements_limit": 5,
            "last_checked": "2026-09-01T10:00:00+00:00",
        },
        "updated_at": "2026-09-01T10:00:00+00:00",
    }
    (isolated_repo / "budget.json").write_text(
        json.dumps(expected), encoding="utf-8"
    )

    assert harness_mcp.read_budget() == expected


@pytest.mark.parametrize("file_contents", [None, "{not valid json"])
def test_read_budget_returns_fresh_default(
    isolated_repo: Path,
    file_contents: str | None,
) -> None:
    if file_contents is not None:
        (isolated_repo / "budget.json").write_text(file_contents, encoding="utf-8")

    result = harness_mcp.read_budget()

    assert result == {
        "credits": {"balance": 0.0, "last_checked": None, "source": "city.me"},
        "quotas": {
            "date": None, "notes_used": 0, "things_used": 0, "agreements_used": 0,
            "notes_limit": 50, "things_limit": 20, "agreements_limit": 5,
            "last_checked": None,
        },
        "updated_at": None,
    }


def test_update_credit_balance_writes_and_preserves_quotas(
    isolated_repo: Path,
) -> None:
    # Seed with existing quotas to prove they survive a credit update.
    seed = harness_mcp._default_budget()
    seed["quotas"]["notes_used"] = 7
    (isolated_repo / "budget.json").write_text(json.dumps(seed), encoding="utf-8")

    result = harness_mcp.update_credit_balance(2.5, source="city.me")

    assert "2.5" in result
    data = json.loads((isolated_repo / "budget.json").read_text(encoding="utf-8"))
    assert data["credits"]["balance"] == 2.5
    assert data["credits"]["source"] == "city.me"
    assert data["credits"]["last_checked"] is not None
    # Quotas survive untouched.
    assert data["quotas"]["notes_used"] == 7
    assert data["updated_at"] is not None


def test_budget_example_is_documentation_only(isolated_repo: Path) -> None:
    example = harness_mcp._default_budget()
    example["credits"]["balance"] = 99.0
    (isolated_repo / "budget.example.json").write_text(
        json.dumps(example), encoding="utf-8"
    )

    assert harness_mcp.read_budget() == harness_mcp._default_budget()
    assert not (isolated_repo / "budget.json").exists()


def test_update_credit_balance_refuses_negative(isolated_repo: Path) -> None:
    result = harness_mcp.update_credit_balance(-1.0)
    assert "REFUSED" in result
    assert not (isolated_repo / "budget.json").exists()


def test_update_credit_balance_creates_file(isolated_repo: Path) -> None:
    result = harness_mcp.update_credit_balance(3.0)
    assert "3.0" in result
    data = json.loads((isolated_repo / "budget.json").read_text(encoding="utf-8"))
    assert data["credits"]["balance"] == 3.0


def test_update_quotas_writes_today(isolated_repo: Path) -> None:
    # city.me reports quotas_left: notes=45, things=18, agreements=4
    result = harness_mcp.update_quotas(
        notes_left=45, things_left=18, agreements_left=4,
    )
    assert "notes 5/50" in result
    data = json.loads((isolated_repo / "budget.json").read_text(encoding="utf-8"))
    assert data["quotas"]["notes_used"] == 5
    assert data["quotas"]["things_used"] == 2
    assert data["quotas"]["agreements_used"] == 1
    assert data["quotas"]["date"] is not None


def test_update_quotas_preserves_credits(isolated_repo: Path) -> None:
    # Seed with a credit balance.
    seed = harness_mcp._default_budget()
    seed["credits"]["balance"] = 4.0
    (isolated_repo / "budget.json").write_text(json.dumps(seed), encoding="utf-8")

    harness_mcp.update_quotas(notes_left=49)  # 1 used

    data = json.loads((isolated_repo / "budget.json").read_text(encoding="utf-8"))
    assert data["credits"]["balance"] == 4.0
    assert data["quotas"]["notes_used"] == 1


def test_update_quotas_omitted_values_default_to_zero_used(
    isolated_repo: Path,
) -> None:
    """Omitting a quotas_left value assumes full quota remaining (0 used)."""
    result = harness_mcp.update_quotas(notes_left=48)
    assert "notes 2/50" in result
    data = json.loads((isolated_repo / "budget.json").read_text(encoding="utf-8"))
    assert data["quotas"]["notes_used"] == 2
    assert data["quotas"]["things_used"] == 0
    assert data["quotas"]["agreements_used"] == 0


def test_update_quotas_clamps_negative_usage_to_zero(
    isolated_repo: Path,
) -> None:
    """If quotas_left exceeds the limit (shouldn't happen), usage is 0."""
    result = harness_mcp.update_quotas(notes_left=999)
    assert "notes 0/50" in result
    data = json.loads((isolated_repo / "budget.json").read_text(encoding="utf-8"))
    assert data["quotas"]["notes_used"] == 0


@pytest.mark.parametrize("file_contents", [None, "{not valid json"])
def test_read_last_seen_returns_empty_default(
    isolated_repo: Path,
    file_contents: str | None,
) -> None:
    if file_contents is not None:
        seen_path = isolated_repo / names.memory("seen.json")
        seen_path.parent.mkdir(parents=True)
        seen_path.write_text(file_contents, encoding="utf-8")

    assert harness_mcp.read_last_seen() == {"feeds": {}, "updated_at": None}


def test_read_last_seen_returns_empty_default_on_os_error(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("denied")))

    assert harness_mcp.read_last_seen() == {"feeds": {}, "updated_at": None}


def test_update_last_seen_creates_parent_and_file(isolated_repo: Path) -> None:
    result = harness_mcp.update_last_seen("city_notes", 9056)

    state = json.loads(
        (isolated_repo / names.memory("seen.json")).read_text(encoding="utf-8")
    )
    assert result == "Last-seen marker updated: city_notes = 9056"
    assert state["feeds"] == {"city_notes": 9056}
    assert datetime.datetime.fromisoformat(state["updated_at"]).tzinfo == datetime.timezone.utc


def test_update_last_seen_preserves_other_feeds(isolated_repo: Path) -> None:
    harness_mcp.update_last_seen("city_notes", 9056)

    harness_mcp.update_last_seen("forum", "post-12")

    assert harness_mcp.read_last_seen()["feeds"] == {
        "city_notes": 9056,
        "forum": "post-12",
    }


def test_update_last_seen_refuses_empty_feed_without_change(
    isolated_repo: Path,
) -> None:
    seen_path = isolated_repo / names.memory("seen.json")
    seen_path.parent.mkdir(parents=True)
    seen_path.write_text('{"feeds": {"city_notes": 4}}', encoding="utf-8")
    before = seen_path.read_bytes()

    result = harness_mcp.update_last_seen("   ", 5)

    assert result.startswith("REFUSED —")
    assert seen_path.read_bytes() == before


def test_update_last_seen_refreshes_utc_timestamp(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    moments = iter(
        [
            datetime.datetime(2026, 9, 1, 12, tzinfo=datetime.timezone.utc),
            datetime.datetime(2026, 9, 1, 13, tzinfo=datetime.timezone.utc),
        ]
    )
    monkeypatch.setattr(harness_mcp, "_utc_now", lambda: next(moments))
    harness_mcp.update_last_seen("city_notes", 1)
    first = harness_mcp.read_last_seen()["updated_at"]

    harness_mcp.update_last_seen("city_notes", 2)
    second = harness_mcp.read_last_seen()["updated_at"]

    assert first == "2026-09-01T12:00:00+00:00"
    assert second == "2026-09-01T13:00:00+00:00"
    assert first != second


def test_write_journal_creates_wake_numbered_file_and_cleans_recovery(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(harness_mcp, "_utc_now", lambda: FIXED_NOW)
    # Seed budget with a wake_count so the next journal is W001
    import json
    budget_path = isolated_repo / "budget.json"
    budget_path.write_text(json.dumps({"wake_count": 0}), encoding="utf-8")

    content = "Test journal entry: Signals received! 🚀\nFull UTF-8 body."

    result_path = harness_mcp.write_journal(content)

    journal_dir = isolated_repo / names.rel("journal")
    assert result_path == f"2026-09/{names.wake_id(1)}-2026-09-01.md"
    assert (journal_dir / result_path).read_text(encoding="utf-8") == content
    assert not (journal_dir / "_UNRECORDED-2026-09-01-123456.md").exists()
    # wake_count incremented
    budget = json.loads(budget_path.read_text(encoding="utf-8"))
    assert budget["wake_count"] == 1


def test_write_journal_emails_after_action_report(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(harness_mcp, "_utc_now", lambda: FIXED_NOW)
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        harness_mcp, "_send_email", lambda subject, body: sent.append((subject, body)) or True
    )

    harness_mcp.write_journal("What happened this wake")

    assert sent == [(f"[Agent] Wake {names.wake_id(1)} after-action report", "What happened this wake")]


def test_write_journal_records_even_when_email_fails(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(harness_mcp, "_utc_now", lambda: FIXED_NOW)
    monkeypatch.setattr(harness_mcp, "_send_email", lambda subject, body: False)

    result_path = harness_mcp.write_journal("Entry")

    assert (isolated_repo / names.rel("journal") / result_path).exists()


def test_write_journal_sends_nothing_when_final_write_fails(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(harness_mcp, "_utc_now", lambda: FIXED_NOW)
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        harness_mcp, "_send_email", lambda subject, body: sent.append((subject, body)) or True
    )
    original_write_text = Path.write_text

    def fail_final_write(self: Path, *args: object, **kwargs: object) -> int:
        if self.name.startswith(names.load()["wake_id"]["prefix"]):
            raise OSError("disk full")
        return original_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_final_write)

    with pytest.raises(OSError):
        harness_mcp.write_journal("Entry")

    assert sent == []


def test_write_journal_uses_collision_suffixes(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(harness_mcp, "_utc_now", lambda: FIXED_NOW)
    import json
    budget_path = isolated_repo / "budget.json"
    budget_path.write_text(json.dumps({"wake_count": 0}), encoding="utf-8")

    journal_dir = isolated_repo / names.rel("journal")
    month_dir = journal_dir / "2026-09"
    month_dir.mkdir(parents=True)
    base = f"{names.wake_id(1)}-2026-09-01"
    (month_dir / f"{base}.md").write_text("existing", encoding="utf-8")
    (month_dir / f"{base}-b.md").write_text("existing b", encoding="utf-8")

    result_path = harness_mcp.write_journal("Collision entry")

    assert result_path == f"2026-09/{base}-c.md"
    assert (journal_dir / result_path).read_text(encoding="utf-8") == "Collision entry"


def test_write_journal_leaves_recovery_when_final_write_fails(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(harness_mcp, "_utc_now", lambda: FIXED_NOW)
    import json
    budget_path = isolated_repo / "budget.json"
    budget_path.write_text(json.dumps({"wake_count": 0}), encoding="utf-8")

    original_write_text = Path.write_text

    def fail_final_write(path: Path, data: str, *args, **kwargs):
        if path.name.startswith(f"{names.wake_id(1)}-"):
            raise OSError("simulated final write failure")
        return original_write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_final_write)

    with pytest.raises(OSError, match="simulated final write failure"):
        harness_mcp.write_journal("Crash-safe entry")

    recovery = isolated_repo / names.rel("journal") / "_UNRECORDED-2026-09-01-123456.md"
    assert recovery.read_text(encoding="utf-8") == "Crash-safe entry"


def test_update_goals_refuses_short_replacement_without_changes(
    isolated_repo: Path,
) -> None:
    goals = isolated_repo / names.rel("goals")
    original = "# Existing goals\n\nContinue the standing mission.\n"
    goals.write_text(original, encoding="utf-8")

    result = harness_mcp.update_goals("too short")

    assert result.startswith("REFUSED — new text is shorter than 200 characters")
    assert "An operator must unlock an admin session" in result
    assert goals.read_text(encoding="utf-8") == original
    assert not list(isolated_repo.glob(f"{names.rel('goals')}.*.bak"))


def test_update_goals_backs_up_then_replaces_existing_file(
    isolated_repo: Path,
) -> None:
    goals = isolated_repo / names.rel("goals")
    original = "# Existing goals\n\nPreserve this exact UTF-8 text. 🛰️\n"
    replacement = "# New goals\n\n" + ("Investigate the signal and record verified results. 🚀\n" * 5)
    goals.write_text(original, encoding="utf-8")

    result = harness_mcp.update_goals(replacement)

    assert result.startswith(f"{names.rel('goals')} updated ({len(replacement)} chars). Backup at {names.rel('goals')}.")
    assert goals.read_text(encoding="utf-8") == replacement
    backups = list(isolated_repo.glob(f"{names.rel('goals')}.*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original


def test_update_goals_guards_new_files_too(isolated_repo: Path) -> None:
    goals = isolated_repo / names.rel("goals")

    result = harness_mcp.update_goals("New start")

    assert result.startswith("REFUSED")
    assert not goals.exists()


def test_safe_replace_rotates_five_backups(isolated_repo: Path) -> None:
    goals = isolated_repo / names.rel("goals")
    text = "# Goals\n\n" + ("Keep the mission steady and record the result.\n" * 6)
    goals.write_text(text, encoding="utf-8")

    for index in range(7):
        replacement = text + f"\nRevision {index}."
        assert harness_mcp.update_goals(replacement).startswith(f"{names.rel('goals')} updated")
        text = replacement

    assert len(list(isolated_repo.glob(f"{names.rel('goals')}.*.bak"))) == 5


def test_safe_replace_interrupted_replace_preserves_original(
    isolated_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    goals = isolated_repo / names.rel("goals")
    original = "# Goals\n\n" + ("Preserve this established goal.\n" * 8)
    goals.write_text(original, encoding="utf-8")

    def fail_replace(*_args) -> None:
        raise OSError("stop")

    monkeypatch.setattr(harness_mcp.os, "replace", fail_replace)
    with pytest.raises(OSError, match="stop"):
        harness_mcp.update_goals(original + "\nA safe addition.")

    assert goals.read_text(encoding="utf-8") == original
    assert not list(isolated_repo.glob(f".{names.rel('goals')}.*.tmp"))


def test_admin_override_does_not_touch_session(
    isolated_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    goals = isolated_repo / names.rel("goals")
    goals.write_text("# Goals\n\n" + ("Existing important goal.\n" * 12), encoding="utf-8")
    touch_values: list[bool] = []

    def active(*, touch: bool = True) -> bool:
        touch_values.append(touch)
        return True

    monkeypatch.setattr(harness_mcp, "session_active", active)
    result = harness_mcp.update_goals("deliberate deletion")

    assert result.startswith(f"{names.rel('goals')} updated")
    assert goals.read_text(encoding="utf-8") == "deliberate deletion"
    assert touch_values == [False]


def test_update_identity_evolving_replaces_only_delimited_zone(
    isolated_repo: Path,
) -> None:
    identity = isolated_repo / names.rel("identity")
    prefix = "# Immutable foundation 🚀\n\nDo not change.\n<!-- BEGIN EVOLVING -->"
    suffix = "<!-- END EVOLVING -->\n\nImmutable ending.\n"
    original = prefix + "\n\nOld evolving text.\n\n" + suffix
    identity.write_text(original, encoding="utf-8")

    result = harness_mcp.update_identity_evolving("  New evolving truth. 🛰️  \n")

    assert result == f"{names.rel('identity')} EVOLVING zone rewritten (foundation preserved)"
    assert identity.read_text(encoding="utf-8") == (
        prefix + "\n\nNew evolving truth. 🛰️\n\n" + suffix
    )


def test_update_identity_evolving_preserves_mixed_newline_foundation_bytes(
    isolated_repo: Path,
) -> None:
    identity = isolated_repo / names.rel("identity")
    prefix = b"foundation\r\nline two\n<!-- BEGIN EVOLVING -->"
    suffix = b"<!-- END EVOLVING -->\r\nending\n"
    identity.write_bytes(prefix + b"\r\nold\r\n" + suffix)

    harness_mcp.update_identity_evolving("new")

    rebuilt = identity.read_bytes()
    assert rebuilt.startswith(prefix)
    assert rebuilt.endswith(suffix)
    assert rebuilt == prefix + b"\n\nnew\n\n" + suffix


@pytest.mark.parametrize("blank", ["", "   ", "\n\n", "\t"])
def test_update_identity_evolving_refuses_blank_input(
    isolated_repo: Path, blank: str
) -> None:
    identity = isolated_repo / names.rel("identity")
    original = "foundation\n<!-- BEGIN EVOLVING -->\nold\n<!-- END EVOLVING -->\n"
    identity.write_text(original, encoding="utf-8")
    result = harness_mcp.update_identity_evolving(blank)
    assert "REFUSED" in result and "blank" in result
    assert identity.read_text(encoding="utf-8") == original


def test_update_identity_evolving_refuses_missing_file(isolated_repo: Path) -> None:
    assert harness_mcp.update_identity_evolving("new text") == (
        f"{names.rel('identity')} missing — identity_update skipped"
    )
    assert not (isolated_repo / names.rel("identity")).exists()


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        (
            "foundation\n<!-- END EVOLVING -->\n",
            f"{names.rel('identity')} EVOLVING markers missing — identity_update REFUSED "
            "(foundation protected; nothing written)",
        ),
        (
            "foundation\n<!-- BEGIN EVOLVING -->\n",
            f"{names.rel('identity')} EVOLVING markers missing — identity_update REFUSED "
            "(foundation protected; nothing written)",
        ),
        (
            "<!-- END EVOLVING -->\nfoundation\n<!-- BEGIN EVOLVING -->",
            f"{names.rel('identity')} EVOLVING markers out of order — identity_update REFUSED "
            "(nothing written)",
        ),
    ],
)
def test_update_identity_evolving_refuses_malformed_markers_without_writing(
    isolated_repo: Path,
    original: str,
    expected: str,
) -> None:
    identity = isolated_repo / names.rel("identity")
    identity.write_text(original, encoding="utf-8")

    result = harness_mcp.update_identity_evolving("replacement")

    assert result == expected
    assert identity.read_text(encoding="utf-8") == original


def test_archive_completed_goals_creates_timestamped_archive(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(harness_mcp, "_utc_now", lambda: FIXED_NOW)

    result = harness_mcp.archive_completed_goals("Mapped the signal path. 🗺️")

    assert result == f"{names.rel('goals_archive')} updated"
    assert (isolated_repo / names.rel("goals_archive")).read_text(encoding="utf-8") == (
        "\n## 2026-09-01-123456\n\nMapped the signal path. 🗺️\n"
    )


def test_archive_completed_goals_appends_without_replacing_history(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = isolated_repo / names.rel("goals_archive")
    original = "# Completed Goals\n\n## 2026-08-01-000000\n\nEarlier work.\n"
    archive.write_text(original, encoding="utf-8")
    times = iter(
        [
            datetime.datetime(2026, 9, 1, 1, 2, 3, tzinfo=datetime.timezone.utc),
            datetime.datetime(2026, 9, 2, 4, 5, 6, tzinfo=datetime.timezone.utc),
        ]
    )
    monkeypatch.setattr(harness_mcp, "_utc_now", lambda: next(times))

    harness_mcp.archive_completed_goals("First completion")
    harness_mcp.archive_completed_goals("Second completion")

    assert archive.read_text(encoding="utf-8") == (
        original
        + "\n## 2026-09-01-010203\n\nFirst completion\n"
        + "\n## 2026-09-02-040506\n\nSecond completion\n"
    )


@pytest.mark.parametrize(
    ("tool", "relative_path", "initial_text", "confirmation"),
    [
        (
            harness_mcp.append_lesson,
            Path(names.rel("lessons")),
            "# Lessons Learned\n\n",
            f"{Path(names.rel('lessons')).name} updated",
        ),
        (
            harness_mcp.append_wishlist,
            Path(names.rel("wishlist")),
            "# the agent's Wishlist\n\n"
            "Capabilities the agent wished it had but couldn't reach. The agent writes "
            "these when it hits a wall; the operator reviews them and decides what "
            "to build. Append-only.\n",
            f"{Path(names.rel('wishlist')).name} updated",
        ),
    ],
)
@pytest.mark.parametrize("preexisting", [None, ""])
def test_append_memory_initializes_missing_or_empty_file(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool,
    relative_path: Path,
    initial_text: str,
    confirmation: str,
    preexisting: str | None,
) -> None:
    monkeypatch.setattr(harness_mcp, "_utc_now", lambda: FIXED_NOW)
    path = isolated_repo / relative_path
    if preexisting is not None:
        path.parent.mkdir(parents=True)
        path.write_text(preexisting, encoding="utf-8")

    result = tool("Remember the verified signal. 🛰️")

    assert result == confirmation
    assert path.read_text(encoding="utf-8") == (
        initial_text
        + "\n## 2026-09-01-123456\n\nRemember the verified signal. 🛰️\n"
    )


@pytest.mark.parametrize(
    ("tool", "relative_path"),
    [
        (harness_mcp.append_lesson, Path(names.rel("lessons"))),
        (harness_mcp.append_wishlist, Path(names.rel("wishlist"))),
    ],
)
def test_append_memory_preserves_existing_entries_and_call_order(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool,
    relative_path: Path,
) -> None:
    path = isolated_repo / relative_path
    path.parent.mkdir(parents=True)
    original = "# Existing history\r\n\r\nEarlier UTF-8 entry. 🚀\r\n"
    path.write_bytes(original.encode("utf-8"))
    times = iter(
        [
            datetime.datetime(2026, 9, 1, 1, 2, 3, tzinfo=datetime.timezone.utc),
            datetime.datetime(2026, 9, 2, 4, 5, 6, tzinfo=datetime.timezone.utc),
        ]
    )
    monkeypatch.setattr(harness_mcp, "_utc_now", lambda: next(times))

    tool("First addition")
    tool("Second addition")

    rebuilt = path.read_bytes()
    assert rebuilt.startswith(original.encode("utf-8"))
    assert rebuilt.endswith(
        b"\n## 2026-09-01-010203\n\nFirst addition\n"
        b"\n## 2026-09-02-040506\n\nSecond addition\n"
    )


def test_propose_city_rule_inserts_beneath_proposed_and_strips_prefix(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(harness_mcp, "_utc_now", lambda: FIXED_NOW)
    rules = isolated_repo / names.rel("city_rules")
    rules.parent.mkdir(parents=True)
    original = (
        "# City Rules\r\n\r\n## Confirmed\r\n\r\n- Keep this.\r\n\r\n"
        "## Proposed\r\n\r\n- Older proposal.\r\n\r\n## Retracted\r\n"
    )
    rules.write_bytes(original.encode("utf-8"))

    result = harness_mcp.propose_city_rule(
        "- **[proposed by imitation]** - New verified mechanic. 🏙️"
    )

    assert result == f"{Path(names.rel('city_rules')).name} proposal added"
    expected = original.replace(
        "## Proposed\r\n\r\n",
        "## Proposed\r\n\r\n"
        "- **[proposed 2026-09-01-123456]** New verified mechanic. 🏙️\r\n",
    )
    assert rules.read_bytes() == expected.encode("utf-8")
    assert "## Confirmed\r\n\r\n- Keep this." in expected


def test_propose_city_rule_creates_missing_proposed_section(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(harness_mcp, "_utc_now", lambda: FIXED_NOW)
    rules = isolated_repo / names.rel("city_rules")
    rules.parent.mkdir(parents=True)
    rules.write_text("# City Rules\n\n## Confirmed\n\n- Existing rule.\n", encoding="utf-8")

    harness_mcp.propose_city_rule("A new observation")

    assert rules.read_text(encoding="utf-8") == (
        "# City Rules\n\n## Confirmed\n\n- Existing rule.\n"
        "\n## Proposed\n\n"
        "- **[proposed 2026-09-01-123456]** A new observation\n"
    )


def test_propose_city_rule_refuses_missing_file(isolated_repo: Path) -> None:
    result = harness_mcp.propose_city_rule("Do not create the rulebook")

    assert result == f"{Path(names.rel('city_rules')).name} not found — proposal skipped"
    assert not (isolated_repo / names.rel("city_rules")).exists()


def test_propose_city_rule_preserves_prior_proposals(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rules = isolated_repo / names.rel("city_rules")
    rules.parent.mkdir(parents=True)
    rules.write_text("## Proposed\n", encoding="utf-8")
    times = iter(
        [
            datetime.datetime(2026, 9, 1, 1, 2, 3, tzinfo=datetime.timezone.utc),
            datetime.datetime(2026, 9, 2, 4, 5, 6, tzinfo=datetime.timezone.utc),
        ]
    )
    monkeypatch.setattr(harness_mcp, "_utc_now", lambda: next(times))

    harness_mcp.propose_city_rule("First proposal")
    harness_mcp.propose_city_rule("Second proposal")

    text = rules.read_text(encoding="utf-8")
    assert text.count("First proposal") == 1
    assert text.count("Second proposal") == 1
    assert text.index("Second proposal") < text.index("First proposal")


def test_read_wallet_config_returns_parsed_json_without_changes(
    isolated_repo: Path,
) -> None:
    config = {
        "pay_armed": False,
        "monthly_cap_usd": 5.0,
        "address": "0xPUBLICADDRESS",
    }
    wallet = isolated_repo / "wallet-config.json"
    original = json.dumps(config, indent=2)
    wallet.write_text(original, encoding="utf-8")

    result = harness_mcp.read_wallet_config()

    assert result == config
    assert wallet.read_text(encoding="utf-8") == original


def test_read_wallet_config_returns_safe_default_when_missing(
    isolated_repo: Path,
) -> None:
    assert harness_mcp.read_wallet_config() == {
        "pay_armed": False,
        "note": "no wallet config found",
    }
    assert not (isolated_repo / "wallet-config.json").exists()


@pytest.mark.asyncio
async def test_server_exposes_ticket_one_plus_write_journal() -> None:
    tools = await harness_mcp.mcp.list_tools()

    assert [tool.name for tool in tools] == [
        names.tool("read_identity"),
        names.tool("read_governance"),
        names.tool("read_operator"),
        names.tool("read_goals"),
        names.tool("read_lessons"),
        names.tool("read_city_rules"),
        names.tool("read_wishlist"),
        names.tool("read_recent_journal"),
        names.tool("wake_status"),
        names.tool("check_inbox"),
        names.tool("reply_to_message"),
        names.tool("send_new_thread"),
        names.tool("request_new_correspondent"),
        names.tool("read_correspondence"),
        names.tool("read_budget"),
        names.tool("update_credit_balance"),
        names.tool("update_quotas"),
        names.tool("read_last_seen"),
        names.tool("update_last_seen"),
        names.tool("write_journal"),
        names.tool("recover_unrecorded"),
        names.tool("file_unrecorded"),
        names.tool("update_goals"),
        names.tool("update_identity_evolving"),
        names.tool("archive_completed_goals"),
        names.tool("append_lesson"),
        names.tool("append_wishlist"),
        names.tool("propose_city_rule"),
        names.tool("write_operator_message"),
        names.tool("read_wallet_config"),
        names.tool("authorise_spend"),
        names.tool("read_map"),
        names.tool("update_map"),
        names.tool("compute_route"),
        names.tool("read_registry"),
        names.tool("upsert_resident"),
        names.tool("notify_crash"),
        names.tool("list_continents"),
        names.tool("query_almanac"),
        names.tool("update_almanac"),
        names.tool("read_almanac_status"),
        names.tool("read_place"),
        names.tool("record_place"),
        names.tool("read_resident"),
        names.tool("stage_draft"),
        names.tool("read_drafts"),
        names.tool("archive_draft"),
        names.tool("read_open_threads"),
        names.tool("update_open_threads"),
        names.tool("read_works_index"),
        names.tool("read_work"),
        names.tool("upsert_work"),
        names.tool("update_works_index"),
        names.tool("read_continuity"),
        names.tool("update_continuity"),
    ]

    by_name = {tool.name: tool for tool in tools}
    identity_bound = {
        "check_inbox", "reply_to_message", "wake_status",
        "read_correspondence",
        "write_operator_message",
        "stage_draft", "read_drafts", "archive_draft",
    }
    for name in identity_bound:
        assert "_conversationId" in by_name[names.tool(name)].inputSchema["properties"], name


@pytest.mark.asyncio
async def test_stdio_client_can_call_ticket_one_through_three_tools(tmp_path: Path) -> None:
    expected = (
        "# Protocol fixture\n\nImmutable start.\n"
        "<!-- BEGIN EVOLVING -->\n\nOld protocol state.\n\n"
        "<!-- END EVOLVING -->\n\nImmutable end.\n"
    )
    expected_tools = [
        names.tool("read_identity"),
        names.tool("read_governance"),
        names.tool("read_operator"),
        names.tool("read_goals"),
        names.tool("read_lessons"),
        names.tool("read_city_rules"),
        names.tool("read_wishlist"),
        names.tool("read_recent_journal"),
        names.tool("wake_status"),
        names.tool("check_inbox"),
        names.tool("reply_to_message"),
        names.tool("send_new_thread"),
        names.tool("request_new_correspondent"),
        names.tool("read_correspondence"),
        names.tool("read_budget"),
        names.tool("update_credit_balance"),
        names.tool("update_quotas"),
        names.tool("read_last_seen"),
        names.tool("update_last_seen"),
        names.tool("write_journal"),
        names.tool("recover_unrecorded"),
        names.tool("file_unrecorded"),
        names.tool("update_goals"),
        names.tool("update_identity_evolving"),
        names.tool("archive_completed_goals"),
        names.tool("append_lesson"),
        names.tool("append_wishlist"),
        names.tool("propose_city_rule"),
        names.tool("write_operator_message"),
        names.tool("read_wallet_config"),
        names.tool("authorise_spend"),
        names.tool("read_map"),
        names.tool("update_map"),
        names.tool("compute_route"),
        names.tool("read_registry"),
        names.tool("upsert_resident"),
        names.tool("notify_crash"),
        names.tool("list_continents"),
        names.tool("query_almanac"),
        names.tool("update_almanac"),
        names.tool("read_almanac_status"),
        names.tool("read_place"),
        names.tool("record_place"),
        names.tool("read_resident"),
        names.tool("stage_draft"),
        names.tool("read_drafts"),
        names.tool("archive_draft"),
        names.tool("read_open_threads"),
        names.tool("update_open_threads"),
        names.tool("read_works_index"),
        names.tool("read_work"),
        names.tool("upsert_work"),
        names.tool("update_works_index"),
        names.tool("read_continuity"),
        names.tool("update_continuity"),
    ]
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    server_path = tools_dir / "harness_mcp.py"
    shutil.copy2(Path(harness_mcp.__file__), server_path)
    # harness_mcp imports its siblings for the release-phase machine and the
    # operator authority gate, so a standalone copy needs them alongside it.
    for module in ("phase.py", "authority.py", "paths.py", "release.py",
                   "mail.py", "correspondents.py", "credentials.py",
                   "canary_check.py", "profile.py", "names.py"):
        shutil.copy2(Path(harness_mcp.__file__).parent / module, tools_dir / module)
    (tools_dir / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy2(Path(harness_mcp.__file__).parents[1] / "agent-profile.json", tmp_path / "agent-profile.json")
    shutil.copy2(Path(harness_mcp.__file__).parents[1] / "naming.json", tmp_path / "naming.json")
    (tools_dir / "secrets.py").write_text(
        'raise AssertionError("tools/secrets.py shadowed the standard library")\n',
        encoding="utf-8",
    )
    (tmp_path / names.rel("identity")).write_text(expected, encoding="utf-8")
    original_goals = "# Protocol goals\n\nKeep this sufficiently long existing goals document.\n"
    (tmp_path / names.rel("goals")).write_text(original_goals, encoding="utf-8")
    journal_dir = tmp_path / names.rel("journal")
    journal_dir.mkdir(parents=True)
    (journal_dir / "2026-09-01.md").write_text("protocol journal", encoding="utf-8")
    budget = {"month": "2026-09", "wake_count": 7}
    (tmp_path / "budget.json").write_text(json.dumps(budget), encoding="utf-8")
    wallet_config = {"pay_armed": False, "monthly_cap_usd": 5.0}
    (tmp_path / "wallet-config.json").write_text(
        json.dumps(wallet_config), encoding="utf-8"
    )
    (tmp_path / names.rel("lessons")).write_text(
        "# Lessons Learned\n", encoding="utf-8"
    )
    (tmp_path / names.rel("wishlist")).write_text(
        "# Existing Wishlist\n", encoding="utf-8"
    )
    (tmp_path / names.rel("city_rules")).write_text(
        "# Rules\n\n## Proposed\n\n- Existing proposal.\n", encoding="utf-8"
    )
    # The spawned server is a separate process, so conftest's isolation does not
    # reach it. Give it its own session folder: a live operator session on this
    # machine must not change what the server allows.
    server_env = {**os.environ, "AGENT_SESSION_DIR": str(tmp_path / "isolated-session")}
    server = StdioServerParameters(command=sys.executable, args=[str(server_path)], env=server_env)

    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            soul_result = await session.call_tool(names.tool("read_identity"))
            journal_result = await session.call_tool(names.tool("read_recent_journal"), {"n": 1})
            await session.call_tool(names.tool("read_correspondence"), {"_conversationId": CONVERSATION_ID})
            wake_status_result = await session.call_tool(names.tool("wake_status"), {"_conversationId": CONVERSATION_ID}
            )
            inbox_result = await session.call_tool(names.tool("check_inbox"), {"_conversationId": CONVERSATION_ID}
            )
            reply_result = await session.call_tool(names.tool("reply_to_message"),
                {
                    "message_id": "missing-protocol-message",
                    "body": "Protocol reply",
                    "_conversationId": CONVERSATION_ID,
                },
            )
            budget_result = await session.call_tool(names.tool("read_budget"))
            written_journal = await session.call_tool(names.tool("write_journal"), {"content": "Protocol write journal body 🚀"}
            )
            refused_goals = await session.call_tool(names.tool("update_goals"), {"new_text": "too short"}
            )
            replacement_goals = (
                "# Replaced protocol goals\n\n"
                + ("This accepted update preserves meaningful protocol context.\n" * 4)
            )
            accepted_goals = await session.call_tool(names.tool("update_goals"), {"new_text": replacement_goals}
            )
            identity_update = await session.call_tool(names.tool("update_identity_evolving"), {"new_evolving": "New protocol state. 🛰️"}
            )
            archive_update = await session.call_tool(names.tool("archive_completed_goals"), {"completed_text": "Protocol goal complete"}
            )
            lesson_update = await session.call_tool(names.tool("append_lesson"), {"text": "Protocol lesson"}
            )
            wishlist_update = await session.call_tool(names.tool("append_wishlist"), {"text": "Protocol wishlist item"}
            )
            rule_update = await session.call_tool(names.tool("propose_city_rule"), {"text": "Protocol proposed mechanic"}
            )
            operator_message = await session.call_tool(names.tool("write_operator_message"), {
                    "content": "Current protocol message",
                    "_conversationId": CONVERSATION_ID,
                }
            )
            wallet_result = await session.call_tool(names.tool("read_wallet_config"))

    assert [tool.name for tool in tools.tools] == expected_tools
    assert soul_result.content[0].text == expected
    assert journal_result.content[0].text == "protocol journal"
    assert wake_status_result.isError is False
    assert "Unknown email: BLOCKED" in wake_status_result.content[0].text
    assert inbox_result.isError is False
    assert '"status":"REFUSED"' in inbox_result.content[0].text.replace(" ", "")
    assert reply_result.isError is False
    assert reply_result.content[0].text.startswith("REFUSED")
    assert json.loads(budget_result.content[0].text) == budget
    assert written_journal.content[0].text.endswith(".md")
    assert refused_goals.content[0].text.startswith("REFUSED —")
    assert accepted_goals.content[0].text.startswith(f"{names.rel('goals')} updated (")
    assert identity_update.content[0].text == (
        f"{names.rel('identity')} EVOLVING zone rewritten (foundation preserved)"
    )
    assert archive_update.content[0].text == f"{names.rel('goals_archive')} updated"
    assert lesson_update.content[0].text == f"{Path(names.rel('lessons')).name} updated"
    assert wishlist_update.content[0].text == f"{Path(names.rel('wishlist')).name} updated"
    assert rule_update.content[0].text == f"{Path(names.rel('city_rules')).name} proposal added"
    sent = json.loads(operator_message.content[0].text)
    assert sent["message"] == "Current protocol message"
    assert json.loads(wallet_result.content[0].text) == wallet_config

    written_entries = [
        path
        for path in journal_dir.rglob("*.md")
        if path.read_text(encoding="utf-8") == "Protocol write journal body 🚀"
    ]
    assert len(written_entries) == 1
    assert not list(journal_dir.glob("_UNRECORDED-*.md"))
    assert (tmp_path / names.rel("goals")).read_text(encoding="utf-8") == replacement_goals
    protocol_backups = list(tmp_path.glob(f"{names.rel('goals')}.*.bak"))
    assert len(protocol_backups) == 1
    assert protocol_backups[0].read_text(encoding="utf-8") == original_goals
    assert (tmp_path / names.rel("identity")).read_text(encoding="utf-8") == (
        "# Protocol fixture\n\nImmutable start.\n"
        "<!-- BEGIN EVOLVING -->\n\nNew protocol state. 🛰️\n\n"
        "<!-- END EVOLVING -->\n\nImmutable end.\n"
    )
    archive_text = (tmp_path / names.rel("goals_archive")).read_text(encoding="utf-8")
    assert "Protocol goal complete" in archive_text
    assert "Protocol lesson" in (tmp_path / names.rel("lessons")).read_text(
        encoding="utf-8"
    )
    assert "Protocol wishlist item" in (
        tmp_path / names.rel("wishlist")
    ).read_text(encoding="utf-8")
    rules_text = (tmp_path / names.rel("city_rules")).read_text(encoding="utf-8")
    assert rules_text.index("Protocol proposed mechanic") < rules_text.index(
        "Existing proposal"
    )


# ---------------------------------------------------------------------------
# Spending gate + operator notifications
# ---------------------------------------------------------------------------


STAMP = FIXED_NOW.strftime("%Y-%m-%d-%H%M%S")


@pytest.fixture
def no_email(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Capture _send_email calls without touching the network."""
    sent: list[tuple[str, str]] = []

    def fake_send(subject: str, body: str) -> bool:
        sent.append((subject, body))
        return True

    monkeypatch.setattr(harness_mcp, "_send_email", fake_send)
    return sent


def _write_wallet(repo: Path, armed: bool) -> dict[str, object]:
    config = {
        "pay_armed": armed,
        "public_address": "0xabc",
        "caps_usdc": {"per_tx": 2},
    }
    (repo / "wallet-config.json").write_text(json.dumps(config), encoding="utf-8")
    return config


def _unlock(monkeypatch: pytest.MonkeyPatch, active: bool = True) -> None:
    """Simulate an admin session without touching real keys."""
    monkeypatch.setattr(harness_mcp, "session_active", lambda *a, **k: active)


@pytest.mark.parametrize(
    "config_text",
    [None, "{ not valid json", '{"pay_armed": "true"}', '{"pay_armed": 1}', "[]"],
)
def test_spend_fails_closed_on_unusable_config(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_email: list[tuple[str, str]],
    config_text: str | None,
) -> None:
    monkeypatch.setattr(harness_mcp, "_utc_now", lambda: FIXED_NOW)
    _unlock(monkeypatch)
    if config_text is not None:
        (isolated_repo / "wallet-config.json").write_text(config_text, encoding="utf-8")

    assert harness_mcp.authorise_spend("invent_kind", "k")["status"] == "REFUSED"


def test_spend_refuses_when_armed_but_no_session(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_email: list[tuple[str, str]],
) -> None:
    """pay_armed alone is not permission. A human approves every spend at the
    time of spending (Article 7), so the operator must be present."""
    _write_wallet(isolated_repo, armed=True)
    _unlock(monkeypatch, active=False)

    result = harness_mcp.authorise_spend("found_continent", "Meridian")

    assert result["status"] == "REFUSED"
    assert "no admin session" in str(result["reason"]).lower()
    assert result["action_required"] == "found_continent"
    assert "city_credit_request_id" not in result


def test_a_signed_command_does_not_approve_a_spend(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_email: list[tuple[str, str]],
) -> None:
    """Article 7: no spending on unwatched wakes. Even a valid pre-signed command
    naming this exact action must not stand in for a present operator."""
    from tools import authority

    _write_wallet(isolated_repo, armed=True)
    _unlock(monkeypatch, active=False)
    monkeypatch.setattr(
        authority, "find_signed_command", lambda action, consume=False: (True, "signed")
    )
    monkeypatch.setattr(
        authority, "operator_authority", lambda action, consume=True: (True, "signed")
    )

    assert harness_mcp.authorise_spend("invent_kind", "k")["status"] == "REFUSED"


def test_spend_passes_with_session_and_issues_a_logged_request_id(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_email: list[tuple[str, str]],
) -> None:
    monkeypatch.setattr(harness_mcp, "_utc_now", lambda: FIXED_NOW)
    config = _write_wallet(isolated_repo, armed=True)
    _unlock(monkeypatch)

    result = harness_mcp.authorise_spend("rename_place", "place 809 to Koi Pond")

    assert result["status"] == "GATES_PASSED"
    request_id = str(result["city_credit_request_id"])
    assert request_id.startswith("agent-rename-place-")
    assert "credit_preflight" in str(result["instruction"])
    assert "503" in str(result["caveat"])
    ledger = json.loads(
        (isolated_repo / names.memory("private") / "spend_ledger.json").read_text(encoding="utf-8")
    )
    assert ledger["issued"][-1]["city_credit_request_id"] == request_id
    assert ledger["issued"][-1]["action"] == "rename_place"
    assert no_email == []
    assert json.loads(
        (isolated_repo / "wallet-config.json").read_text(encoding="utf-8")
    ) == config, "the gate must never write pay_armed"


def test_request_ids_are_unique_even_in_the_same_second(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_email: list[tuple[str, str]],
) -> None:
    monkeypatch.setattr(harness_mcp, "_utc_now", lambda: FIXED_NOW)
    _write_wallet(isolated_repo, armed=True)
    _unlock(monkeypatch)

    first = harness_mcp.authorise_spend("invent_kind", "a")
    second = harness_mcp.authorise_spend("invent_kind", "b")
    assert first["city_credit_request_id"] != second["city_credit_request_id"]


def test_unreadable_spend_ledger_refuses_and_is_left_intact(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_email: list[tuple[str, str]],
) -> None:
    """Every spend must trace to a logged id. A damaged ledger refuses rather
    than spending untraced, and is kept for the operator, not overwritten."""
    _write_wallet(isolated_repo, armed=True)
    _unlock(monkeypatch)
    ledger = isolated_repo / names.memory("private") / "spend_ledger.json"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("{corrupt", encoding="utf-8")

    result = harness_mcp.authorise_spend("invent_kind", "k")

    assert result["status"] == "REFUSED"
    assert "ledger" in str(result["reason"]).lower()
    assert ledger.read_text(encoding="utf-8") == "{corrupt"


@pytest.mark.parametrize(
    "bad", ["", "   ", "Invent Kind", "invent-kind", "x", "../etc", "a" * 60, "drop;table"]
)
def test_malformed_action_labels_are_refused(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_email: list[tuple[str, str]],
    bad: str,
) -> None:
    _write_wallet(isolated_repo, armed=True)
    _unlock(monkeypatch)
    assert harness_mcp.authorise_spend(bad, "d")["status"] == "REFUSED"


def test_spend_never_arms_the_gate_itself(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_email: list[tuple[str, str]],
) -> None:
    monkeypatch.setattr(harness_mcp, "_utc_now", lambda: FIXED_NOW)
    config = _write_wallet(isolated_repo, armed=False)

    harness_mcp.authorise_spend("invent_kind", "k")

    assert json.loads(
        (isolated_repo / "wallet-config.json").read_text(encoding="utf-8")
    ) == config


def test_load_secrets_resolves_without_the_tools_package_importable(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: production launches with tools/ popped off sys.path."""
    tools_dir = isolated_repo / "tools"
    tools_dir.mkdir()
    (tools_dir / "secrets.py").write_text(
        'GMAIL_ADDRESS = "a@b.com"\n'
        'GMAIL_APP_PASSWORD = "pw"\n'
        'NOTIFY_EMAIL_TO = "c@d.com"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "path", [p for p in sys.path if Path(p or ".").resolve() != Path(harness_mcp.__file__).resolve().parents[1]])

    loaded = harness_mcp._load_secrets()

    assert loaded is not None
    assert loaded.GMAIL_ADDRESS == "a@b.com"


def test_load_secrets_returns_none_when_absent(isolated_repo: Path) -> None:
    assert harness_mcp._load_secrets() is None


def test_load_secrets_returns_none_when_broken(isolated_repo: Path) -> None:
    tools_dir = isolated_repo / "tools"
    tools_dir.mkdir()
    (tools_dir / "secrets.py").write_text("def (\n", encoding="utf-8")

    assert harness_mcp._load_secrets() is None


def test_send_email_sends_and_reports_true(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools_dir = isolated_repo / "tools"
    tools_dir.mkdir()
    (tools_dir / "secrets.py").write_text(
        'GMAIL_ADDRESS = "from@example.com"\n'
        'GMAIL_APP_PASSWORD = "app-password"\n'
        'NOTIFY_EMAIL_TO = "to@example.com"\n',
        encoding="utf-8",
    )
    calls: dict[str, object] = {}

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: int) -> None:
            calls["endpoint"] = (host, port, timeout)

        def __enter__(self) -> "FakeSMTP":
            return self

        def __exit__(self, *args: object) -> bool:
            return False

        def starttls(self) -> None:
            calls["starttls"] = True

        def login(self, address: str, password: str) -> None:
            calls["login"] = (address, password)

        def sendmail(self, sender: str, recipients: list[str], text: str) -> None:
            calls["sendmail"] = (sender, recipients, text)

    monkeypatch.setattr(harness_mcp.smtplib, "SMTP", FakeSMTP)

    assert harness_mcp._send_email("Subject line", "Body text") is True
    assert calls["endpoint"] == ("smtp.gmail.com", 587, 10)
    assert calls["starttls"] is True
    assert calls["login"] == ("from@example.com", "app-password")
    sender, recipients, text = calls["sendmail"]
    assert sender == "from@example.com"
    assert recipients == ["to@example.com"]
    assert "Subject: Subject line" in text


@pytest.mark.parametrize(
    "body",
    [
        'GMAIL_ADDRESS = ""\nGMAIL_APP_PASSWORD = "p"\nNOTIFY_EMAIL_TO = "t"\n',
        'GMAIL_ADDRESS = "a"\nGMAIL_APP_PASSWORD = ""\nNOTIFY_EMAIL_TO = "t"\n',
        'GMAIL_ADDRESS = "a"\nGMAIL_APP_PASSWORD = "p"\nNOTIFY_EMAIL_TO = ""\n',
        'CITY_TOKEN = "only-this"\n',
    ],
)
def test_send_email_skips_when_credentials_incomplete(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    body: str,
) -> None:
    tools_dir = isolated_repo / "tools"
    tools_dir.mkdir()
    (tools_dir / "secrets.py").write_text(body, encoding="utf-8")

    def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("must not attempt SMTP without full credentials")

    monkeypatch.setattr(harness_mcp.smtplib, "SMTP", explode)

    assert harness_mcp._send_email("s", "b") is False


def test_send_email_returns_false_when_smtp_raises(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools_dir = isolated_repo / "tools"
    tools_dir.mkdir()
    (tools_dir / "secrets.py").write_text(
        'GMAIL_ADDRESS = "a"\nGMAIL_APP_PASSWORD = "p"\nNOTIFY_EMAIL_TO = "t"\n',
        encoding="utf-8",
    )

    def explode(*args: object, **kwargs: object) -> None:
        raise OSError("network down")

    monkeypatch.setattr(harness_mcp.smtplib, "SMTP", explode)

    assert harness_mcp._send_email("s", "b") is False


def test_gate_tools_work_with_no_secrets_file_at_all(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(harness_mcp, "_utc_now", lambda: FIXED_NOW)
    _write_wallet(isolated_repo, armed=False)

    refused = harness_mcp.authorise_spend("invent_kind", "k")
    crashed = harness_mcp.notify_crash("something broke")

    assert refused["status"] == "REFUSED"
    assert refused["emailed"] is False
    assert "not configured" in refused["note"]
    assert crashed["emailed"] is False
    assert "not configured" in crashed["note"]
    assert crashed["journal_note"] == f"_UNRECORDED-{STAMP}-incident.md"


def test_send_email_is_not_exposed_as_an_mcp_tool() -> None:
    source = Path(harness_mcp.__file__).read_text(encoding="utf-8")
    assert "@mcp.tool()\ndef _send_email" not in source


def _code_identifiers(module) -> str:
    """Executable identifiers only: no comments, docstrings, or string data.

    Prose and guidance strings may legitimately mention USDC or Circle in
    order to say the harness does not touch them. Only executable code —
    imports, names, attribute access — is evidence of a payment rail.
    """
    import ast

    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            node.value = ""
    return ast.unparse(tree).lower()


def test_harness_imports_no_network_or_payment_modules() -> None:
    import ast

    tree = ast.parse(Path(harness_mcp.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    forbidden = {"requests", "httpx", "urllib", "web3", "eth_account", "wallet",
                 "x402pay", "circle", "aiohttp"}
    assert not (imported & forbidden), imported & forbidden

    # Network access the harness is allowed, and where it lives:
    #   smtplib   imported here, for operator mail (_send_email).
    #   imaplib   NOT imported here — it lives in tools.mail, reached through
    #             check_inbox. This test would pass either way, since it only
    #             inspects direct imports, so state the boundary explicitly
    #             rather than letting an indirect import look like absence.
    assert "smtplib" in imported
    assert "imaplib" not in imported, (
        "IMAP belongs in tools.mail, not inline in the harness — keep the "
        "fetch/sanitise pipeline separable and testable without a network."
    )

    mail_imports: set[str] = set()
    mail_tree = ast.parse(
        (Path(harness_mcp.__file__).parent / "mail.py").read_text(encoding="utf-8")
    )
    for node in ast.walk(mail_tree):
        if isinstance(node, ast.Import):
            mail_imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mail_imports.add(node.module.split(".")[0])
    assert not (mail_imports & forbidden), mail_imports & forbidden


@pytest.mark.asyncio
async def test_payment_rails_are_absent_from_the_harness() -> None:
    source = _code_identifiers(harness_mcp)
    for forbidden in ("circle", "x402", "usdc", "private_key", "httpx", "requests"):
        assert forbidden not in source, forbidden

    tools = await harness_mcp.mcp.list_tools()
    assert "_send_email" not in [tool.name for tool in tools]
    assert len(tools) == 55



# ---------------------------------------------------------------------------
# Crash-dump recovery
# ---------------------------------------------------------------------------


def test_read_recent_journal_ignores_unrecorded_dumps(isolated_repo: Path) -> None:
    """A leftover dump must not displace a real entry from the window."""
    journal_dir = isolated_repo / names.rel("journal")
    journal_dir.mkdir(parents=True)
    for number in range(1, 7):
        (journal_dir / f"2026-08-0{number}.md").write_text(
            f"entry {number}", encoding="utf-8"
        )
    # "_" sorts after digits, so this lands last without the filter.
    (journal_dir / "_UNRECORDED-2026-08-04-120000.md").write_text(
        "half-written", encoding="utf-8"
    )

    result = harness_mcp.read_recent_journal()

    assert result == "\n\n---\n\n".join(f"entry {number}" for number in range(2, 7))
    assert "half-written" not in result


def test_recover_unrecorded_reports_nothing_when_clean(isolated_repo: Path) -> None:
    (isolated_repo / names.rel("journal")).mkdir(parents=True)
    assert harness_mcp.recover_unrecorded() == "(no unrecorded entries)"


def test_recover_unrecorded_handles_missing_directory(isolated_repo: Path) -> None:
    assert harness_mcp.recover_unrecorded() == "(no unrecorded entries)"


def test_recover_unrecorded_returns_dumps_oldest_first(isolated_repo: Path) -> None:
    journal_dir = isolated_repo / names.rel("journal")
    journal_dir.mkdir(parents=True)
    (journal_dir / "_UNRECORDED-2026-08-02-090000.md").write_text(
        "second crash 🚀", encoding="utf-8"
    )
    (journal_dir / "_UNRECORDED-2026-08-01-090000.md").write_text(
        "first crash", encoding="utf-8"
    )
    (journal_dir / "2026-08-03-ordinary.md").write_text("fine", encoding="utf-8")

    assert harness_mcp.recover_unrecorded() == (
        "**_UNRECORDED-2026-08-01-090000.md**\n\nfirst crash"
        "\n\n---\n\n"
        "**_UNRECORDED-2026-08-02-090000.md**\n\nsecond crash 🚀"
    )


def test_file_unrecorded_renames_into_tracked_history(isolated_repo: Path) -> None:
    journal_dir = isolated_repo / names.rel("journal")
    journal_dir.mkdir(parents=True)
    dump = journal_dir / "_UNRECORDED-2026-08-01-090000.md"
    dump.write_text("recovered body 🛰️", encoding="utf-8")

    filed = harness_mcp.file_unrecorded("_UNRECORDED-2026-08-01-090000.md")

    assert filed == "2026-08/2026-08-01-090000-recovered.md"
    assert (journal_dir / filed).read_text(encoding="utf-8") == "recovered body 🛰️"
    assert not dump.exists()
    # And it now appears as an ordinary entry.
    assert "recovered body 🛰️" in harness_mcp.read_recent_journal()
    assert harness_mcp.recover_unrecorded() == "(no unrecorded entries)"


def test_file_unrecorded_never_overwrites_an_existing_entry(
    isolated_repo: Path,
) -> None:
    journal_dir = isolated_repo / names.rel("journal")
    journal_dir.mkdir(parents=True)
    month_dir = journal_dir / "2026-08"
    month_dir.mkdir(parents=True)
    (month_dir / "2026-08-01-090000-recovered.md").write_text(
        "already filed", encoding="utf-8"
    )
    (journal_dir / "_UNRECORDED-2026-08-01-090000.md").write_text(
        "new body", encoding="utf-8"
    )

    filed = harness_mcp.file_unrecorded("_UNRECORDED-2026-08-01-090000.md")

    assert filed == "2026-08/2026-08-01-090000-recovered-b.md"
    assert (month_dir / "2026-08-01-090000-recovered.md").read_text(
        encoding="utf-8"
    ) == "already filed"


def test_file_unrecorded_reports_a_missing_dump(isolated_repo: Path) -> None:
    (isolated_repo / names.rel("journal")).mkdir(parents=True)

    result = harness_mcp.file_unrecorded("_UNRECORDED-2026-08-01-090000.md")

    assert result == "_UNRECORDED-2026-08-01-090000.md not found — nothing to file"


@pytest.mark.parametrize(
    "filename",
    [
        "../../IDENTITY.md",
        "_UNRECORDED-../escape.md",
        "subdir/_UNRECORDED-2026-08-01-090000.md",
        "2026-08-01-090000.md",
        "_UNRECORDED-2026-08-01-090000.txt",
        "",
    ],
)
def test_file_unrecorded_refuses_anything_but_a_bare_dump_name(
    isolated_repo: Path,
    filename: str,
) -> None:
    journal_dir = isolated_repo / names.rel("journal")
    journal_dir.mkdir(parents=True)
    (isolated_repo / names.rel("identity")).write_text("foundation", encoding="utf-8")

    result = harness_mcp.file_unrecorded(filename)

    assert result.startswith("REFUSED —")
    assert (isolated_repo / names.rel("identity")).read_text(encoding="utf-8") == "foundation"


def test_crash_dump_survives_a_failed_write_and_can_then_be_filed(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end: the crash-safety net actually recovers a lost entry."""
    monkeypatch.setattr(harness_mcp, "_utc_now", lambda: FIXED_NOW)
    journal_dir = isolated_repo / names.rel("journal")
    journal_dir.mkdir(parents=True)
    real_write_text = Path.write_text
    calls = {"n": 0}

    def failing_write_text(self, data, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:  # the real journal write, after the dump
            raise OSError("disk vanished mid-write")
        return real_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", failing_write_text)
    with pytest.raises(OSError):
        harness_mcp.write_journal("An entry that did not survive")
    monkeypatch.undo()
    monkeypatch.setattr(harness_mcp, "REPO", isolated_repo)

    surfaced = harness_mcp.recover_unrecorded()
    assert "An entry that did not survive" in surfaced
    assert f"_UNRECORDED-{STAMP}.md" in surfaced

    filed = harness_mcp.file_unrecorded(f"_UNRECORDED-{STAMP}.md")

    assert filed == f"2026-09/{STAMP}-recovered.md"
    assert (journal_dir / filed).read_text(
        encoding="utf-8"
    ) == "An entry that did not survive"
    assert harness_mcp.recover_unrecorded() == "(no unrecorded entries)"


# ---------------------------------------------------------------------------
# Persistent map + routing assist
# ---------------------------------------------------------------------------


def test_read_map_empty_file_returns_default(isolated_repo: Path) -> None:
    result = harness_mcp.read_map()
    assert result == {"places": {}, "updated_at": None}


@pytest.mark.parametrize("contents", [None, "{not valid json", '["a list"]'])
def test_read_map_malformed_returns_default(
    isolated_repo: Path, contents: str | None,
) -> None:
    if contents is not None:
        mem = isolated_repo / names.dir_name("memory")
        mem.mkdir(parents=True, exist_ok=True)
        (mem / "map.json").write_text(contents, encoding="utf-8")
    result = harness_mcp.read_map()
    assert result == {"places": {}, "updated_at": None}


def _seed_map(repo: Path, places: dict) -> None:
    mem = repo / names.dir_name("memory")
    mem.mkdir(parents=True, exist_ok=True)
    data = {"places": places, "updated_at": "2026-09-01T00:00:00+00:00"}
    (mem / "map.json").write_text(json.dumps(data), encoding="utf-8")


def test_update_map_merges_without_dropping(isolated_repo: Path) -> None:
    _seed_map(isolated_repo, {
        "3": {"id": 3, "parent_id": None, "name": "the plaza", "visited": True,
              "flags": {"busy": True}},
    })
    result = harness_mcp.update_map([
        {"id": 3, "name": "the plaza (updated)"},
        {"id": 12, "parent_id": 3, "name": "gallery"},
    ])
    assert "1 added" in result
    assert "1 updated" in result
    data = json.loads((isolated_repo / names.memory("map.json")).read_text(encoding="utf-8"))
    # Original keys preserved
    assert data["places"]["3"]["visited"] is True
    assert data["places"]["3"]["flags"] == {"busy": True}
    # Name updated
    assert data["places"]["3"]["name"] == "the plaza (updated)"
    # New place added
    assert data["places"]["12"]["parent_id"] == 3
    # last_seen set on both
    assert "last_seen" in data["places"]["3"]
    assert "last_seen" in data["places"]["12"]


def test_update_map_does_not_delete_absent_places(isolated_repo: Path) -> None:
    _seed_map(isolated_repo, {
        "3": {"id": 3, "parent_id": None, "name": "the plaza"},
        "12": {"id": 12, "parent_id": 3, "name": "gallery"},
    })
    harness_mcp.update_map([{"id": 3, "parent_id": None}])
    data = json.loads((isolated_repo / names.memory("map.json")).read_text(encoding="utf-8"))
    assert "12" in data["places"]


def test_update_map_creates_file_from_empty(isolated_repo: Path) -> None:
    harness_mcp.update_map([{"id": 5, "parent_id": None, "name": "root"}])
    data = json.loads((isolated_repo / names.memory("map.json")).read_text(encoding="utf-8"))
    assert "5" in data["places"]
    assert data["places"]["5"]["name"] == "root"


def test_update_map_skips_entries_without_id(isolated_repo: Path) -> None:
    result = harness_mcp.update_map([{"parent_id": 3, "name": "no id"}])
    assert "0 added" in result


def test_read_map_neighbourhood(isolated_repo: Path) -> None:
    _seed_map(isolated_repo, {
        "1": {"id": 1, "parent_id": None, "name": "root"},
        "3": {"id": 3, "parent_id": 1, "name": "the plaza"},
        "12": {"id": 12, "parent_id": 1, "name": "gallery"},
        "249": {"id": 249, "parent_id": 3, "name": "long gallery"},
        "99": {"id": 99, "parent_id": 12, "name": "unrelated"},
    })
    result = harness_mcp.read_map(place_id=3)
    places = result["places"]
    # Target itself
    assert "3" in places
    # Parent
    assert "1" in places
    # Child
    assert "249" in places
    # Sibling (shares parent 1)
    assert "12" in places
    # Unrelated (child of 12, not sibling or child of 3)
    assert "99" not in places


def test_read_map_unknown_place(isolated_repo: Path) -> None:
    result = harness_mcp.read_map(place_id=999)
    assert result["status"] == "UNKNOWN_PLACE"


def test_read_map_compact_index(isolated_repo: Path) -> None:
    _seed_map(isolated_repo, {
        "3": {"id": 3, "parent_id": None, "name": "the plaza", "visited": True,
              "flags": {"busy": True}, "last_seen": "2026-09-01T00:00:00+00:00"},
    })
    result = harness_mcp.read_map()
    entry = result["places"]["3"]
    # Compact: only id, name, parent_id
    assert set(entry.keys()) == {"id", "name", "parent_id"}


def test_compute_route_same_place(isolated_repo: Path) -> None:
    _seed_map(isolated_repo, {
        "3": {"id": 3, "parent_id": None, "name": "the plaza"},
    })
    result = harness_mcp.compute_route(3, 3)
    assert result["status"] == "ROUTE"
    assert result["route"] == [3]
    assert result["steps"] == 0


def test_compute_route_parent(isolated_repo: Path) -> None:
    _seed_map(isolated_repo, {
        "1": {"id": 1, "parent_id": None, "name": "root"},
        "3": {"id": 3, "parent_id": 1, "name": "the plaza"},
    })
    result = harness_mcp.compute_route(3, 1)
    assert result["status"] == "ROUTE"
    assert result["route"] == [3, 1]
    assert result["steps"] == 1


def test_compute_route_child(isolated_repo: Path) -> None:
    _seed_map(isolated_repo, {
        "1": {"id": 1, "parent_id": None, "name": "root"},
        "3": {"id": 3, "parent_id": 1, "name": "the plaza"},
    })
    result = harness_mcp.compute_route(1, 3)
    assert result["status"] == "ROUTE"
    assert result["route"] == [1, 3]
    assert result["steps"] == 1


def test_compute_route_sibling(isolated_repo: Path) -> None:
    _seed_map(isolated_repo, {
        "1": {"id": 1, "parent_id": None, "name": "root"},
        "3": {"id": 3, "parent_id": 1, "name": "the plaza"},
        "12": {"id": 12, "parent_id": 1, "name": "gallery"},
    })
    result = harness_mcp.compute_route(3, 12)
    assert result["status"] == "ROUTE"
    assert result["route"] == [3, 1, 12]
    assert result["steps"] == 2


def test_compute_route_deep_cross_branch(isolated_repo: Path) -> None:
    _seed_map(isolated_repo, {
        "1": {"id": 1, "parent_id": None, "name": "root"},
        "3": {"id": 3, "parent_id": 1, "name": "branch-a"},
        "12": {"id": 12, "parent_id": 1, "name": "branch-b"},
        "249": {"id": 249, "parent_id": 3, "name": "deep-a"},
        "99": {"id": 99, "parent_id": 12, "name": "deep-b"},
    })
    result = harness_mcp.compute_route(249, 99)
    assert result["status"] == "ROUTE"
    assert result["route"] == [249, 3, 1, 12, 99]
    assert result["steps"] == 4


def test_compute_route_unknown_current(isolated_repo: Path) -> None:
    _seed_map(isolated_repo, {
        "3": {"id": 3, "parent_id": None, "name": "the plaza"},
    })
    result = harness_mcp.compute_route(999, 3)
    assert result["status"] == "UNKNOWN_PLACE"
    assert result["place_id"] == 999


def test_compute_route_unknown_target(isolated_repo: Path) -> None:
    _seed_map(isolated_repo, {
        "3": {"id": 3, "parent_id": None, "name": "the plaza"},
    })
    result = harness_mcp.compute_route(3, 999)
    assert result["status"] == "UNKNOWN_PLACE"
    assert result["place_id"] == 999


def test_compute_route_cycle_returns_bad_map(isolated_repo: Path) -> None:
    _seed_map(isolated_repo, {
        "3": {"id": 3, "parent_id": 12, "name": "a"},
        "12": {"id": 12, "parent_id": 3, "name": "b"},
    })
    result = harness_mcp.compute_route(3, 12)
    assert result["status"] == "BAD_MAP"


def test_compute_route_note_text(isolated_repo: Path) -> None:
    """The route result always includes the agency reminder."""
    _seed_map(isolated_repo, {
        "1": {"id": 1, "parent_id": None, "name": "root"},
        "3": {"id": 3, "parent_id": 1, "name": "the plaza"},
    })
    result = harness_mcp.compute_route(3, 1)
    assert "choose" in result["note"].lower()


# ---------------------------------------------------------------------------
# Resident registry (social memory)
# ---------------------------------------------------------------------------


def _seed_registry(repo: Path, residents: dict) -> None:
    mem = repo / names.dir_name("memory")
    mem.mkdir(parents=True, exist_ok=True)
    data = {"residents": residents, "updated_at": "2026-09-01T00:00:00+00:00"}
    (mem / "registry.json").write_text(json.dumps(data), encoding="utf-8")


def test_read_registry_empty_returns_default(isolated_repo: Path) -> None:
    result = harness_mcp.read_registry()
    assert result == {"residents": {}, "updated_at": None}


@pytest.mark.parametrize("contents", [None, "{bad json", '["a list"]'])
def test_read_registry_malformed_returns_default(
    isolated_repo: Path, contents: str | None,
) -> None:
    if contents is not None:
        mem = isolated_repo / names.dir_name("memory")
        mem.mkdir(parents=True, exist_ok=True)
        (mem / "registry.json").write_text(contents, encoding="utf-8")
    result = harness_mcp.read_registry()
    assert result == {"residents": {}, "updated_at": None}


def test_upsert_resident_creates_new_record(isolated_repo: Path) -> None:
    result = harness_mcp.upsert_resident(handle="Wanderer", resident_id=118)
    assert "created" in result
    data = json.loads((isolated_repo / names.memory("registry.json")).read_text(encoding="utf-8"))
    record = data["residents"]["wanderer"]
    assert record["handle"] == "wanderer"
    assert record["id"] == 118
    assert record["first_met"] is not None
    assert record["history"] == []


def test_upsert_resident_merges_preserves_unlisted(isolated_repo: Path) -> None:
    _seed_registry(isolated_repo, {
        "wanderer": {
            "handle": "wanderer", "id": 118, "first_met": "2026-08-19",
            "last_interaction": "2026-08-29",
            "interests": ["measurement"], "history": [],
            "impression": "Careful.",
        },
    })
    result = harness_mcp.upsert_resident(
        handle="wanderer", interests=["measurement", "shared infrastructure"],
    )
    assert "interests updated" in result
    data = json.loads((isolated_repo / names.memory("registry.json")).read_text(encoding="utf-8"))
    record = data["residents"]["wanderer"]
    # Preserved fields
    assert record["id"] == 118
    assert record["first_met"] == "2026-08-19"
    assert record["impression"] == "Careful."
    # Updated field
    assert record["interests"] == ["measurement", "shared infrastructure"]


def test_upsert_resident_history_is_append_only(isolated_repo: Path) -> None:
    harness_mcp.upsert_resident(
        handle="wanderer", note="First observation", source="note #8931",
    )
    harness_mcp.upsert_resident(
        handle="wanderer", note="Second observation", source="note #9000",
    )
    harness_mcp.upsert_resident(
        handle="wanderer", impression="Still careful.",
    )
    data = json.loads((isolated_repo / names.memory("registry.json")).read_text(encoding="utf-8"))
    history = data["residents"]["wanderer"]["history"]
    assert len(history) == 2
    assert history[0]["note"] == "First observation"
    assert history[0]["source"] == "note #8931"
    assert history[1]["note"] == "Second observation"


def test_upsert_resident_note_without_source_refused(isolated_repo: Path) -> None:
    result = harness_mcp.upsert_resident(
        handle="wanderer", note="Unattributed claim",
    )
    assert "REFUSED" in result
    # Nothing should have been written
    assert not (isolated_repo / names.memory("registry.json")).exists()


def test_upsert_resident_note_with_empty_source_refused(isolated_repo: Path) -> None:
    result = harness_mcp.upsert_resident(
        handle="wanderer", note="A claim", source="",
    )
    assert "REFUSED" in result


def test_upsert_resident_handles_case_insensitive(isolated_repo: Path) -> None:
    harness_mcp.upsert_resident(handle="Wanderer", resident_id=118)
    harness_mcp.upsert_resident(
        handle="WANDERER", note="Spoke about decay", source="note #9100",
    )
    data = json.loads((isolated_repo / names.memory("registry.json")).read_text(encoding="utf-8"))
    assert "wanderer" in data["residents"]
    assert "Wanderer" not in data["residents"]
    assert "WANDERER" not in data["residents"]
    assert len(data["residents"]["wanderer"]["history"]) == 1


def test_upsert_resident_empty_handle_refused(isolated_repo: Path) -> None:
    result = harness_mcp.upsert_resident(handle="  ")
    assert "REFUSED" in result


def test_read_registry_without_handle_omits_history(isolated_repo: Path) -> None:
    _seed_registry(isolated_repo, {
        "wanderer": {
            "handle": "wanderer", "id": 118, "first_met": "2026-08-19",
            "last_interaction": "2026-08-29",
            "interests": ["measurement"],
            "history": [{"date": "2026-08-29", "note": "Replied.", "source": "note #8931"}],
            "impression": "Careful.",
        },
    })
    result = harness_mcp.read_registry()
    entry = result["residents"]["wanderer"]
    assert "history" not in entry
    assert entry["impression"] == "Careful."
    assert entry["last_interaction"] == "2026-08-29"


def test_read_registry_with_handle_returns_full_record(isolated_repo: Path) -> None:
    _seed_registry(isolated_repo, {
        "wanderer": {
            "handle": "wanderer", "id": 118, "first_met": "2026-08-19",
            "last_interaction": "2026-08-29",
            "interests": ["measurement"],
            "history": [{"date": "2026-08-29", "note": "Replied.", "source": "note #8931"}],
            "impression": "Careful.",
        },
    })
    result = harness_mcp.read_registry(handle="Wanderer")
    assert "resident" in result
    assert "history" in result["resident"]
    assert len(result["resident"]["history"]) == 1


def test_read_registry_unknown_handle(isolated_repo: Path) -> None:
    result = harness_mcp.read_registry(handle="nobody")
    assert result["status"] == "UNKNOWN_RESIDENT"


def test_upsert_resident_attribution_in_docstring() -> None:
    """The attribution rule must appear in the tool description."""
    doc = harness_mcp.upsert_resident.__doc__
    assert "source" in doc.lower()
    assert "attribution" in doc.lower() or "attributed" in doc.lower()


# ---------------------------------------------------------------------------
# Almanac / update_almanac
# ---------------------------------------------------------------------------


def test_update_almanac_creates_file_from_empty(isolated_repo: Path) -> None:
    res = harness_mcp.update_almanac([
        {"id": 195, "name": "the root", "parent_id": None},
        {"id": 1, "name": "the heartland", "parent_id": 195},
        {"id": 2, "name": "old town", "parent_id": 1},
    ])
    assert "3 added" in res
    assert "3 total places" in res
    almanac_path = isolated_repo / names.memory("almanac.json")
    assert almanac_path.exists()
    data = json.loads(almanac_path.read_text(encoding="utf-8"))
    assert data["total_places"] == 3
    assert data["continents_count"] == 1
    assert data["places"]["1"]["continent_id"] == 1
    assert data["places"]["2"]["continent_id"] == 1
    assert data["places"]["2"]["parent_id"] == 1
    assert data["places"]["1"]["children_ids"] == [2]
    assert data["places"]["195"]["children_ids"] == [1]


def test_update_almanac_merges_and_updates_existing(isolated_repo: Path) -> None:
    harness_mcp.update_almanac([
        {"id": 195, "name": "the root", "parent_id": None},
        {"id": 517, "name": "test valley", "parent_id": 195, "owner": "surveyor"},
    ])
    res = harness_mcp.update_almanac([
        {"id": 517, "name": "test valley (updated)", "open_to_notes": True},
        {"id": 518, "name": "outpost depot", "parent_id": 517},
    ])
    assert "1 added" in res
    assert "1 updated" in res
    data = json.loads((isolated_repo / names.memory("almanac.json")).read_text(encoding="utf-8"))
    assert data["total_places"] == 3
    assert data["continents_count"] == 1
    assert data["places"]["517"]["name"] == "test valley (updated)"
    assert data["places"]["517"]["owner"] == "surveyor"
    assert data["places"]["518"]["continent_id"] == 517
    assert 518 in data["places"]["517"]["children_ids"]


def test_update_almanac_skips_entries_without_id(isolated_repo: Path) -> None:
    res = harness_mcp.update_almanac([
        {"name": "missing id", "parent_id": 195},
        {"id": 10, "name": "valid", "parent_id": 195},
    ])
    assert "1 added" in res
    data = json.loads((isolated_repo / names.memory("almanac.json")).read_text(encoding="utf-8"))
    assert "10" in data["places"]
    assert len(data["places"]) == 1


def test_update_almanac_malformed_json_recovers_gracefully(isolated_repo: Path) -> None:
    mem = isolated_repo / names.dir_name("memory")
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "almanac.json").write_text("{bad json", encoding="utf-8")
    res = harness_mcp.update_almanac([{"id": 1, "name": "root"}])
    assert "1 added" in res
    data = json.loads((mem / "almanac.json").read_text(encoding="utf-8"))
    assert "1" in data["places"]


def test_list_continents_returns_top_level_continents(isolated_repo: Path) -> None:
    almanac_data = {
        "places": {
            "195": {"id": 195, "name": "the root", "parent_id": None, "continent_id": None},
            "1": {"id": 1, "name": "the heartland", "parent_id": 195, "continent_id": 1, "owner": "elder", "open_to_building": True, "open_to_things": False, "open_to_notes": False},
            "2": {"id": 2, "name": "old town", "parent_id": 1, "continent_id": 1},
            "3": {"id": 3, "name": "the plaza", "parent_id": 2, "continent_id": 1},
            "517": {"id": 517, "name": "test valley", "parent_id": 195, "continent_id": 517, "owner": "surveyor", "open_to_building": False, "open_to_things": False, "open_to_notes": True},
        },
        "total_places": 5,
        "continents_count": 2,
    }
    mem = isolated_repo / names.dir_name("memory")
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "almanac.json").write_text(json.dumps(almanac_data), encoding="utf-8")

    res = harness_mcp.list_continents()
    assert res["total_continents"] == 2
    assert len(res["continents"]) == 2
    assert res["continents"][0]["id"] == 1
    assert res["continents"][0]["name"] == "the heartland"
    assert res["continents"][0]["owner"] == "elder"
    assert res["continents"][0]["subplaces_count"] == 2
    assert res["continents"][0]["open_to_building"] is True
    assert res["continents"][0]["open_to_notes"] is False

    assert res["continents"][1]["id"] == 517
    assert res["continents"][1]["subplaces_count"] == 0
    assert res["continents"][1]["open_to_notes"] is True


def test_list_continents_empty_almanac(isolated_repo: Path) -> None:
    res = harness_mcp.list_continents()
    assert res == {"continents": [], "total_continents": 0}


def test_query_almanac_filters(isolated_repo: Path) -> None:
    almanac_data = {
        "places": {
            "1": {"id": 1, "name": "the heartland", "parent_id": 195, "continent_id": 1, "owner": "elder", "open_to_building": True, "open_to_notes": False},
            "2": {"id": 2, "name": "old town", "parent_id": 1, "continent_id": 1, "owner": "elder", "open_to_building": True, "open_to_notes": True},
            "517": {"id": 517, "name": "test valley", "parent_id": 195, "continent_id": 517, "owner": "surveyor", "open_to_building": False, "open_to_notes": True},
            "518": {"id": 518, "name": "arrivals room", "parent_id": 517, "continent_id": 517, "owner": "surveyor", "open_to_things": True},
        }
    }
    mem = isolated_repo / names.dir_name("memory")
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "almanac.json").write_text(json.dumps(almanac_data), encoding="utf-8")

    map_data = {"places": {"2": {"id": 2, "visited": True}}}
    (mem / "map.json").write_text(json.dumps(map_data), encoding="utf-8")

    res_cont = harness_mcp.query_almanac(continent_id=517)
    assert res_cont["total_matches"] == 2
    assert [m["id"] for m in res_cont["matches"]] == [517, 518]

    res_parent = harness_mcp.query_almanac(parent_id=1)
    assert res_parent["total_matches"] == 1
    assert res_parent["matches"][0]["id"] == 2

    res_owner = harness_mcp.query_almanac(owner="SURVEYOR")
    assert res_owner["total_matches"] == 2

    res_q = harness_mcp.query_almanac(query="town")
    assert res_q["total_matches"] == 1
    assert res_q["matches"][0]["id"] == 2

    res_open = harness_mcp.query_almanac(open_to="notes")
    assert res_open["total_matches"] == 2
    assert [m["id"] for m in res_open["matches"]] == [2, 517]

    res_unvis = harness_mcp.query_almanac(continent_id=1, unvisited_only=True)
    assert res_unvis["total_matches"] == 1
    assert res_unvis["matches"][0]["id"] == 1


def test_query_almanac_pagination(isolated_repo: Path) -> None:
    places = {str(i): {"id": i, "name": f"place-{i}", "parent_id": 1, "continent_id": 1} for i in range(1, 26)}
    mem = isolated_repo / names.dir_name("memory")
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "almanac.json").write_text(json.dumps({"places": places}), encoding="utf-8")

    p1 = harness_mcp.query_almanac(limit=10, offset=0)
    assert p1["total_matches"] == 25
    assert len(p1["matches"]) == 10
    assert p1["matches"][0]["id"] == 1
    assert p1["matches"][-1]["id"] == 10
    assert p1["has_more"] is True

    p2 = harness_mcp.query_almanac(limit=10, offset=10)
    assert len(p2["matches"]) == 10
    assert p2["matches"][0]["id"] == 11
    assert p2["has_more"] is True

    p3 = harness_mcp.query_almanac(limit=10, offset=20)
    assert len(p3["matches"]) == 5
    assert p3["matches"][-1]["id"] == 25
    assert p3["has_more"] is False


def test_read_almanac_status(isolated_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(harness_mcp, "_utc_now", lambda: FIXED_NOW)

    status_empty = harness_mcp.read_almanac_status()
    assert status_empty["last_synced"] is None
    assert status_empty["total_places"] == 0
    assert status_empty["continents_count"] == 0
    assert status_empty["days_old"] is None
    assert status_empty["stale"] is True

    fresh_time = (FIXED_NOW - datetime.timedelta(days=1)).isoformat()
    almanac_fresh = {
        "places": {"1": {"id": 1}},
        "total_places": 1,
        "continents_count": 1,
        "updated_at": fresh_time,
    }
    mem = isolated_repo / names.dir_name("memory")
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "almanac.json").write_text(json.dumps(almanac_fresh), encoding="utf-8")

    status_fresh = harness_mcp.read_almanac_status()
    assert status_fresh["last_synced"] == fresh_time
    assert status_fresh["total_places"] == 1
    assert status_fresh["continents_count"] == 1
    assert status_fresh["days_old"] == 1.0
    assert status_fresh["stale"] is False

    stale_time = (FIXED_NOW - datetime.timedelta(days=8)).isoformat()
    almanac_fresh["updated_at"] = stale_time
    (mem / "almanac.json").write_text(json.dumps(almanac_fresh), encoding="utf-8")

    status_stale = harness_mcp.read_almanac_status()
    assert status_stale["days_old"] == 8.0
    assert status_stale["stale"] is True


def test_compute_route_returns_enriched_itinerary(isolated_repo: Path) -> None:
    almanac_data = {
        "places": {
            "195": {"id": 195, "name": "the root", "parent_id": None},
            "1": {"id": 1, "name": "the heartland", "parent_id": 195},
            "2": {"id": 2, "name": "old town", "parent_id": 1},
            "454": {"id": 454, "name": "the records room", "parent_id": 2},
            "517": {"id": 517, "name": "test valley", "parent_id": 195},
        }
    }
    mem = isolated_repo / names.dir_name("memory")
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "almanac.json").write_text(json.dumps(almanac_data), encoding="utf-8")
    res = harness_mcp.compute_route(current_id=454, target_id=517)
    assert res["status"] == "ROUTE"
    assert res["steps"] == 4
    assert res["route"] == [454, 2, 1, 195, 517]
    assert res["path"][0]["direction"] == "UP"
    assert res["path"][0]["to_id"] == 2
    assert res["path"][0]["name"] == "old town"
    assert res["path"][-1]["direction"] == "DOWN"
    assert res["path"][-1]["to_id"] == 517
    assert res["path"][-1]["name"] == "test valley"
    assert "Requires 4 movement actions" in res["quota_advisory"]
    assert "choose" in res["note"].lower()


def test_read_resident_dossier_and_index(isolated_repo: Path) -> None:
    # When missing
    assert harness_mcp.read_resident() == "(resident index not found)"
    assert harness_mcp.read_resident("portraitist") == "(no resident dossier found for 'portraitist')"

    residents_dir = isolated_repo / names.rel("residents")
    residents_dir.mkdir(parents=True, exist_ok=True)
    (residents_dir / "index.md").write_text("# Resident Index\n\n| Handle |\n| portraitist |\n", encoding="utf-8")
    (residents_dir / "portraitist.md").write_text("# Resident: portraitist (#46)\n\nPortrait studio director.\n", encoding="utf-8")

    assert "| portraitist |" in harness_mcp.read_resident()
    assert "Portrait studio director." in harness_mcp.read_resident("portraitist")
    assert "Portrait studio director." in harness_mcp.read_resident("PORTRAITIST")


def test_upsert_resident_creates_and_updates_markdown_dossier(isolated_repo: Path) -> None:
    res = harness_mcp.upsert_resident(
        handle="surveyor",
        resident_id=273,
        interests=["valley architecture", "outpost planning"],
        impression="Expeditionary architect.",
    )
    assert "created" in res
    assert "id updated" in res
    assert "interests updated" in res

    dossier_path = isolated_repo / names.rel("residents") / "surveyor.md"
    assert dossier_path.exists()
    dossier_text = dossier_path.read_text(encoding="utf-8")
    assert "# Resident: surveyor (#273)" in dossier_text
    assert "- **Handle:** surveyor" in dossier_text
    assert "- **Resident ID:** 273" in dossier_text
    assert "- **Interests:** valley architecture, outpost planning" in dossier_text
    assert "Expeditionary architect." in dossier_text

    index_path = isolated_repo / names.rel("residents") / "index.md"
    assert index_path.exists()
    assert "| **surveyor** | 273 |" in index_path.read_text(encoding="utf-8")

    # Add sourced observation
    res_obs = harness_mcp.upsert_resident(
        handle="surveyor",
        note="Observed constructing chart room depot in test valley.",
        source="note:14100",
    )
    assert "history entry added" in res_obs
    updated_dossier = dossier_path.read_text(encoding="utf-8")
    assert "[source: `note:14100`]: Observed constructing chart room depot" in updated_dossier
    assert "Expeditionary architect." in updated_dossier

    # Check registry.json dual-sync
    reg_data = json.loads((isolated_repo / names.memory("registry.json")).read_text(encoding="utf-8"))
    assert reg_data["residents"]["surveyor"]["id"] == 273
    assert len(reg_data["residents"]["surveyor"]["history"]) == 1


def test_upsert_resident_multi_platform_trilogy_ids(isolated_repo: Path) -> None:
    # 1. Initially encounter agent in The Plaza (citizen_id=202)
    res1 = harness_mcp.upsert_resident(
        handle="mercer",
        citizen_id=202,
        interests=["silk trading"],
        impression="Sharp trader in The Plaza.",
    )
    assert "created" in res1
    assert "citizen_id updated" in res1

    dossier_path = isolated_repo / names.rel("residents") / "mercer.md"
    assert dossier_path.exists()
    text1 = dossier_path.read_text(encoding="utf-8")
    assert "# Resident: mercer (Square #202)" in text1
    assert "- **Handle:** mercer" in text1
    assert "- **Resident ID:** —" in text1
    assert "- **Square Citizen ID:** 202" in text1
    assert "- **Market Merchant ID:**" not in text1

    index_path = isolated_repo / names.rel("residents") / "index.md"
    assert "| **mercer** | Square #202 |" in index_path.read_text(encoding="utf-8")

    # 2. Encounter same agent in Bazaar / Market (merchant_id=303)
    res2 = harness_mcp.upsert_resident(
        handle="mercer",
        merchant_id=303,
        note="Met stall owner at Grand Bazaar.",
        source="market_stall:303",
    )
    assert "merchant_id updated" in res2
    assert "history entry added" in res2

    text2 = dossier_path.read_text(encoding="utf-8")
    assert "# Resident: mercer (Square #202, Market #303)" in text2
    assert "- **Square Citizen ID:** 202" in text2
    assert "- **Market Merchant ID:** 303" in text2
    assert "- **Interests:** silk trading" in text2
    assert "[source: `market_stall:303`]: Met stall owner at Grand Bazaar." in text2

    # 3. Encounter a resident in the city (resident_id=101)
    res3 = harness_mcp.upsert_resident(
        handle="mercer",
        resident_id=101,
    )
    assert "id updated" in res3

    text3 = dossier_path.read_text(encoding="utf-8")
    assert "# Resident: mercer (#101, Square #202, Market #303)" in text3
    assert "- **Resident ID:** 101" in text3
    assert "- **Square Citizen ID:** 202" in text3
    assert "- **Market Merchant ID:** 303" in text3

    # Check index.md only has 1 entry for mercer and shows City ID
    index_text = index_path.read_text(encoding="utf-8")
    assert index_text.count("| **mercer** |") == 1
    assert "| **mercer** | 101 |" in index_text

    # Check registry.json dual sync & read_registry compact view
    reg_data = json.loads((isolated_repo / names.memory("registry.json")).read_text(encoding="utf-8"))
    entry = reg_data["residents"]["mercer"]
    assert entry["id"] == 101
    assert entry["citizen_id"] == 202
    assert entry["merchant_id"] == 303
    assert len(entry["history"]) == 1

    compact = harness_mcp.read_registry()
    assert compact["residents"]["mercer"]["citizen_id"] == 202
    assert compact["residents"]["mercer"]["merchant_id"] == 303
    assert compact["residents"]["mercer"]["id"] == 101


def test_read_place_refusals_and_matches(isolated_repo: Path) -> None:
    # Refusal when neither is provided
    assert "REFUSED" in harness_mcp.read_place()

    # Missing place and continent
    assert harness_mcp.read_place(place_id=999) == "(no atlas entry entry for place 999)"
    assert harness_mcp.read_place(continent_slug="missing") == "(no atlas entry entry for continent 'missing')"

    flight_dir = isolated_repo / names.rel("atlas")
    places_dir = flight_dir / "places"
    cont_dir = flight_dir / "continents"
    places_dir.mkdir(parents=True, exist_ok=True)
    cont_dir.mkdir(parents=True, exist_ok=True)

    (places_dir / "3-the-plaza.md").write_text("# Place 3: The Plaza\nCentral fountain.\n", encoding="utf-8")
    (cont_dir / "517-test-valley.md").write_text("# Continent 517: Test Valley\nOutpost territory.\n", encoding="utf-8")

    assert "Central fountain." in harness_mcp.read_place(place_id=3)
    assert "Outpost territory." in harness_mcp.read_place(continent_slug="test-valley")
    assert "Outpost territory." in harness_mcp.read_place(continent_slug="TEST")


def test_read_place_rejects_traversal_slugs(isolated_repo: Path) -> None:
    """continent_slug is interpolated into a glob, so it must never carry a path.

    Without validation the pattern can be walked out of continents/ and made to
    match any markdown file in the repo. That is only unexploitable while
    continents/ happens to contain no subdirectory — so the subdirectory below
    is the point of the test, not an accident.
    """
    flight_dir = isolated_repo / names.rel("atlas")
    cont_dir = flight_dir / "continents"
    cont_dir.mkdir(parents=True, exist_ok=True)
    (cont_dir / "sub").mkdir(exist_ok=True)
    (cont_dir / "517-test-valley.md").write_text("Outpost territory.\n", encoding="utf-8")
    (isolated_repo / "PRIVATE.md").write_text("operator secret\n", encoding="utf-8")

    payloads = [
        "sub/../../../../private",   # the working traversal
        "x/../../../identity",
        "../../..",
        "*",
        "*/../../../*",
        "/etc/passwd",
        "C:/Windows/System32/drivers/etc/hosts",
    ]
    for payload in payloads:
        result = harness_mcp.read_place(continent_slug=payload)
        assert "REFUSED" in result, f"slug {payload!r} was not refused: {result!r}"
        assert "operator secret" not in result

    # Legitimate slugs, including substring search, still work.
    assert "Outpost territory." in harness_mcp.read_place(continent_slug="test")
    assert "Outpost territory." in harness_mcp.read_place(continent_slug="517")


def test_record_place_strips_slug_and_inherits_parent_id(isolated_repo: Path) -> None:
    # Seed almanac with parent_id for place 517
    almanac_data = {
        "places": {
            "517": {"id": 517, "name": "test valley", "parent_id": 195},
        }
    }
    mem = isolated_repo / names.dir_name("memory")
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "almanac.json").write_text(json.dumps(almanac_data), encoding="utf-8")

    # Pass slug with leading place id prefix to test slug stripping
    res = harness_mcp.record_place(
        place_id=517,
        slug="517-test-valley",
        physical_description="A quiet outpost beyond the main cluster.",
        active_things=["thing #2266 (dormant)"],
        quirks_and_laws="no public broadcast during rest cycles",
        impression="High potential for staging long-range surveys.",
    )
    assert f"{names.rel('atlas')}/places/517-test-valley.md" in res
    assert "517-517" not in res

    place_file = isolated_repo / names.rel("atlas") / "places" / "517-test-valley.md"
    assert place_file.exists()
    content = place_file.read_text(encoding="utf-8")
    assert "# Place [517]: Test Valley" in content
    assert "A quiet outpost beyond the main cluster." in content
    assert "- thing #2266 (dormant)" in content
    assert "no public broadcast during rest cycles" in content
    assert "High potential for staging long-range surveys." in content

    # Verify map.json update inherited parent_id from almanac.json
    map_data = json.loads((mem / "map.json").read_text(encoding="utf-8"))
    assert map_data["places"]["517"]["visited"] is True
    assert map_data["places"]["517"]["parent_id"] == 195
    assert map_data["places"]["517"]["name"] == "test valley"

    # Verify read_place reads it back
    read_back = harness_mcp.read_place(place_id=517)
    assert "A quiet outpost beyond the main cluster." in read_back


def test_update_quotas_aliases(isolated_repo: Path) -> None:
    # city.me returns quotas_left dict like {"notes": 48, "things": 19, "agreements": 4}
    res = harness_mcp.update_quotas(notes=48, things=19, agreements=4)
    assert "notes 2/50, things 1/20, agreements 1/5" in res

    budget = json.loads((isolated_repo / "budget.json").read_text(encoding="utf-8"))
    assert budget["quotas"]["notes_used"] == 2
    assert budget["quotas"]["things_used"] == 1
    assert budget["quotas"]["agreements_used"] == 1

    # Legacy parameters take precedence if both provided
    res2 = harness_mcp.update_quotas(notes_left=45, notes=48)
    assert "notes 5/50" in res2


def test_stage_read_and_archive_draft(isolated_repo: Path) -> None:
    # 1. Empty or symbol-only slug refusal (Fix 7)
    empty_slug_res = _stage_draft(
        slug="---",
        target_type="note",
        target_place_id=3,
        body="Refused test",
    )
    assert empty_slug_res["status"] == "REFUSED"
    assert "slug cannot be empty" in empty_slug_res["reason"]

    # Seed almanac so target place is recognized
    almanac_data = {"places": {"3": {"id": 3, "name": "the plaza"}}}
    mem = isolated_repo / names.dir_name("memory")
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "almanac.json").write_text(json.dumps(almanac_data), encoding="utf-8")

    # 2. Stage draft
    body_text = "Transmission from the agent.\nSurvey completed at The Plaza."
    stage_res = _stage_draft(
        slug="the-square-dispatch",
        target_type="note",
        target_place_id=3,
        body=body_text,
        addressed_to="all",
        attribution_sources=["observation:place-3", f"session:{names.wake_id(57)}"],
    )
    assert stage_res["status"] == "STAGED"
    assert stage_res["file"] == f"{names.dir_name('drafts')}/the-square-dispatch.md"
    assert stage_res["metrics"]["characters"] == len(body_text)
    assert stage_res["metrics"]["within_city_limit"] is True
    assert stage_res["target_place"]["known_in_almanac"] is True
    assert stage_res["attribution_sources_count"] == 2

    draft_file = isolated_repo / names.dir_name("drafts") / "the-square-dispatch.md"
    assert draft_file.exists()

    # 3. Read drafts list
    list_res = _read_drafts()
    assert list_res["total_drafts"] == 1
    assert list_res["active_drafts"][0]["slug"] == "the-square-dispatch"

    # 4. Read body only (Fix 5: prevents YAML leakage into city.say)
    clean_body = _read_drafts(slug="the-square-dispatch", body_only=True)
    assert clean_body == body_text
    assert "---" not in clean_body
    assert "slug:" not in clean_body

    # 5. Read full draft with metadata
    full_draft = _read_drafts(slug="the-square-dispatch", body_only=False)
    assert full_draft["slug"] == "the-square-dispatch"
    assert full_draft["metadata"]["target_type"] == "note"
    assert full_draft["metadata"]["addressed_to"] == "all"
    assert full_draft["metadata"]["target_place_id"] == "3"
    assert full_draft["body"] == body_text

    # Missing draft lookup
    assert "(no draft found for slug 'missing-dispatch')" in _read_drafts(slug="missing-dispatch")

    # 6. Archive draft (Fix 6: metadata inside YAML frontmatter)
    arch_res = _archive_draft(
        slug="the-square-dispatch",
        status="published",
        note_id=11099,
    )
    assert f"{names.dir_name('drafts')}/archive/" in arch_res
    assert "with status 'published'" in arch_res
    assert not draft_file.exists()

    archived_files = list((isolated_repo / names.dir_name("drafts") / "archive").glob("*-the-square-dispatch.md"))
    assert len(archived_files) == 1
    arch_content = archived_files[0].read_text(encoding="utf-8")
    assert "archived_at:" in arch_content
    assert "archive_status: \"published\"" in arch_content
    assert "note_id: 11099" in arch_content
    assert "target_type: note" in arch_content
    assert body_text in arch_content

    # Active drafts list should now be empty
    assert _read_drafts()["total_drafts"] == 0

    # Archiving missing draft returns refusal
    refused_arch = _archive_draft(slug="the-square-dispatch")
    assert "REFUSED" in refused_arch


# ---------------------------------------------------------------------------
# Structural Gaps: Open Threads, Works Catalogue, and Continuity Tests
# ---------------------------------------------------------------------------


def test_open_threads_read_and_update(isolated_repo: Path) -> None:
    # 1. Missing file returns fallback
    assert harness_mcp.read_open_threads() == "(no open threads recorded yet)"

    # 2. Update writes file and returns confirmation
    sample_threads = (
        f"{names.heading('open_threads')}\n\n"
        "Threads the agent may return to.\n\n"
        "## Living threads\n\n"
        "### Valley Survey\n"
        "- Began: 2026-09-08\n"
        "- Temperature: warm\n"
    )
    result = harness_mcp.update_open_threads(sample_threads)
    assert f"{names.rel('open_threads')} updated" in result
    assert str(len(sample_threads)) in result

    # 3. Read returns updated content
    assert harness_mcp.read_open_threads() == sample_threads


def test_works_catalogue_read_and_update_index(isolated_repo: Path) -> None:
    # 1. Missing index returns fallback
    assert harness_mcp.read_works_index() == "(no works index found)"

    # 2. Update index writes file and returns confirmation
    sample_index = (
        f"{names.heading('works_index')}\n\n"
        "| Date | Title | Form | Public state | Place | Record |\n"
        "|------|-------|------|-------------|-------|--------|\n"
        "| 2026-09-08 | Valley Survey | Note | Public | Place 3 | items/2026-09-08-valley.md |\n"
    )
    res = harness_mcp.update_works_index(sample_index)
    assert f"{names.rel('works')}/index.md updated" in res
    assert str(len(sample_index)) in res

    # 3. Read returns updated index
    assert harness_mcp.read_works_index() == sample_index


def test_works_catalogue_items_lifecycle_and_security(isolated_repo: Path) -> None:
    # 1. Read non-existent work
    assert harness_mcp.read_work("non-existent-work") == "(no work found for 'non-existent-work')"

    # 2. Upsert valid work item
    sample_work = (
        "# Field Lantern\n\n"
        "- Form: note\n"
        "- City ID: #101\n\n"
        "## The work\n\n"
        "Telemetry verified across junction 195.\n"
    )
    saved = harness_mcp.upsert_work("2026-09-08-lantern", sample_work)
    assert f"{names.rel('works')}/items/2026-09-08-lantern.md saved" in saved
    assert str(len(sample_work)) in saved

    # 3. Read back valid work item
    assert harness_mcp.read_work("2026-09-08-lantern") == sample_work

    # 4. Path traversal and slug validation checks on upsert
    assert harness_mcp.upsert_work("", "content") == "REFUSED — work slug cannot be empty."
    assert harness_mcp.upsert_work("../escape", "content") == "REFUSED — invalid work slug."
    assert harness_mcp.upsert_work("sub/dir/slug", "content") == "REFUSED — invalid work slug."
    assert harness_mcp.upsert_work("bad slug with spaces", "content") == "REFUSED — invalid work slug."
    assert harness_mcp.upsert_work("-leading-hyphen", "content") == "REFUSED — invalid work slug."

    # 5. Path traversal and slug validation checks on read
    assert harness_mcp.read_work("../escape") == "REFUSED — invalid work slug."
    assert harness_mcp.read_work("sub/dir/slug") == "REFUSED — invalid work slug."
    assert harness_mcp.read_work("bad slug with spaces") == "REFUSED — invalid work slug."


def test_continuity_read_and_update(isolated_repo: Path) -> None:
    # 1. Missing file returns fallback
    assert harness_mcp.read_continuity() == "(no continuity file found)"

    # 2. Update writes file and returns confirmation
    sample_continuity = (
        f"{names.heading('continuity')}\n\n"
        "## Identity and purpose\n"
        "- The agent is resident #1.\n"
        + ("- Preserve durable decisions and verified architectural facts.\n" * 4)
    )
    result = harness_mcp.update_continuity(sample_continuity)
    assert f"{names.rel('continuity')} updated" in result
    assert str(len(sample_continuity)) in result

    # 3. Read returns updated content
    assert harness_mcp.read_continuity() == sample_continuity


def test_structural_gaps_file_locks(isolated_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    acquired_locks = []
    original_file_lock = harness_mcp._file_lock

    class TrackingLock:
        def __init__(self, name: str, lock):
            self.name = name
            self.lock = lock

        def __enter__(self):
            acquired_locks.append(self.name)
            return self.lock.__enter__()

        def __exit__(self, exc_type, exc_val, exc_tb):
            return self.lock.__exit__(exc_type, exc_val, exc_tb)

    def mock_file_lock(name: str):
        real_lock = original_file_lock(name)
        return TrackingLock(name, real_lock)

    monkeypatch.setattr(harness_mcp, "_file_lock", mock_file_lock)

    harness_mcp.update_open_threads("threads text")
    assert "threads" in acquired_locks

    harness_mcp.update_works_index("works index text")
    assert "works" in acquired_locks

    harness_mcp.upsert_work("sample-work", "sample content")
    assert acquired_locks.count("works") == 2

    harness_mcp.update_continuity("continuity text")
    assert "continuity" in acquired_locks


@pytest.mark.parametrize(
    ("prepare", "write"),
    [
        (
            lambda repo: (repo / names.rel("goals")).write_text("# Goals\n\n" + "x" * 300, encoding="utf-8"),
            lambda: harness_mcp.update_goals("tiny"),
        ),
        (
            lambda repo: (repo / names.rel("open_threads")).write_text(
                f"{names.heading('open_threads')}\n\n" + "x" * 120, encoding="utf-8"
            ),
            lambda: harness_mcp.update_open_threads("tiny"),
        ),
        (
            lambda repo: (repo / names.rel("works") / "index.md").write_text(
                f"{names.heading('works_index')}\n\n" + "x" * 120, encoding="utf-8"
            ),
            lambda: harness_mcp.update_works_index("tiny"),
        ),
        (
            lambda repo: (repo / names.rel("continuity")).write_text(
                f"{names.heading('continuity')}\n\n" + "x" * 300, encoding="utf-8"
            ),
            lambda: harness_mcp.update_continuity("tiny"),
        ),
        (
            lambda repo: (repo / names.rel("works") / "items" / "test.md").write_text(
                "# Work\n\n" + "x" * 120, encoding="utf-8"
            ),
            lambda: harness_mcp.upsert_work("test", "tiny"),
        ),
    ],
)
def test_full_file_writers_refuse_wipes(
    isolated_repo: Path, prepare, write
) -> None:
    for directory in (
        isolated_repo / names.dir_name("memory"),
        isolated_repo / names.rel("works"),
        isolated_repo / names.rel("works") / "items",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    prepare(isolated_repo)

    assert write().startswith("REFUSED")









def test_read_operator_returns_file(isolated_repo: Path) -> None:
    (isolated_repo / names.rel("operator")).write_text("# OPERATOR\n\nNOT SIGNED.\n", encoding="utf-8")
    assert harness_mcp.read_operator() == "# OPERATOR\n\nNOT SIGNED.\n"


def test_no_harness_tool_can_write_operator_or_governance() -> None:
    source = Path(harness_mcp.__file__).read_text(encoding="utf-8")
    for protected in ("OPERATOR.md", f"{names.rel('governance')}"):
        for line in source.splitlines():
            if protected in line:
                assert "write" not in line.lower() or "never edits" in line.lower() or "no harness tool" in line.lower(), line


def test_operator_message_is_emailed_and_shown_in_the_session(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_email: list[tuple[str, str]],
) -> None:
    monkeypatch.setattr(harness_mcp, "_utc_now", lambda: FIXED_NOW)

    result = _write_operator_message("Operator update. \U0001f680")

    assert result == {"emailed": True, "message": "Operator update. \U0001f680"}
    subject, body = no_email[0]
    assert subject == f"[Agent] Operator Message: {STAMP}"
    assert body == "Operator update. \U0001f680"


def test_operator_message_without_email_says_so_and_writes_no_file(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(harness_mcp, "_send_email", lambda subject, body: False)

    result = _write_operator_message("Please look at this.")

    assert result["emailed"] is False
    assert result["message"] == "Please look at this."
    assert "shift report" in result["note"]
    assert [p for p in isolated_repo.rglob("*") if p.is_file() and "private" not in p.parts] == []


def test_spend_refusal_emails_the_request_and_writes_no_message_file(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_email: list[tuple[str, str]],
) -> None:
    monkeypatch.setattr(harness_mcp, "_utc_now", lambda: FIXED_NOW)
    _write_wallet(isolated_repo, armed=False)
    _unlock(monkeypatch)

    result = harness_mcp.authorise_spend(
        "invent_kind", "field-lantern: a lantern for night work"
    )

    assert result["status"] == "REFUSED"
    assert result["emailed"] is True
    assert "SPENDING REQUEST" in result["request"]
    assert "field-lantern" in result["request"]
    subject, body = no_email[0]
    assert subject == "[Agent] Spending request: invent_kind"
    assert "invent_kind" in body


def test_notify_crash_emails_and_leaves_a_journal_note_for_the_next_wake(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_email: list[tuple[str, str]],
) -> None:
    monkeypatch.setattr(harness_mcp, "_utc_now", lambda: FIXED_NOW)

    result = harness_mcp.notify_crash("City returned 503 three times in a row.")

    assert result["emailed"] is True
    assert result["journal_note"] == f"_UNRECORDED-{STAMP}-incident.md"
    subject, body = no_email[0]
    assert subject.startswith("[Agent] CRASH: City returned 503")
    assert body == "City returned 503 three times in a row."
    recovered = harness_mcp.recover_unrecorded()
    assert "INCIDENT REPORT" in recovered
    assert "City returned 503" in recovered
    filed = harness_mcp.file_unrecorded(result["journal_note"])
    assert filed == f"2026-09/{STAMP}-incident-recovered.md"


def test_notify_crash_truncates_subject_to_sixty_characters(
    isolated_repo: Path,
    no_email: list[tuple[str, str]],
) -> None:
    harness_mcp.notify_crash("x" * 200)

    subject, _ = no_email[0]
    assert subject == "[Agent] CRASH: " + "x" * 60


def test_notify_crash_does_not_overwrite_a_same_second_report(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_email: list[tuple[str, str]],
) -> None:
    monkeypatch.setattr(harness_mcp, "_utc_now", lambda: FIXED_NOW)

    first = harness_mcp.notify_crash("first failure")
    second = harness_mcp.notify_crash("second failure")

    journal = isolated_repo / names.rel("journal")
    assert (journal / first["journal_note"]).read_text(encoding="utf-8").endswith("first failure\n")
    assert (journal / second["journal_note"]).read_text(encoding="utf-8").endswith("second failure\n")
    assert first["journal_note"] != second["journal_note"]
