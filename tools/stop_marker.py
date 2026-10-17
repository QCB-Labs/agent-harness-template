"""Stop-marker storage and guards shared by the three local MCP servers.

Standard library only. This module never launches git or imports a server.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import functools
import json
import io
import os
from pathlib import Path
import stat
import tempfile
import threading

REFUSAL = "REFUSED — STOP.md is standing."
CHECK_FAILURE = "REFUSED — STOP.md could not be checked."
LOG_HEADER = """# Stop log

Append-only record. `honoured` records a refused wake attempt or a session
started under a marker. `removed` records an operator removal and the number
of honoured rows for that marker. Removal records preserve the full marker
and the operator's reason beneath the table.

| Date and time (UTC) | Type | Detail |
| --- | --- | --- |
"""
_thread_lock = threading.RLock()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def stop_lock(root: Path):
    """Serialize marker/log operations across threads and local processes.

    The persistent lock inode is never unlinked; *.tmp is already ignored.
    OS locks are released when a process exits, including an interrupted one.
    """
    with _thread_lock:
        with (root / ".stop-lock.tmp").open("a+b") as handle:
            if os.name == "nt":
                import msvcrt
                if handle.seek(0, os.SEEK_END) == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def marker_present(root: Path) -> bool:
    """lstat distinguishes absence from errors and treats dangling links as stops."""
    try:
        (root / "STOP.md").lstat()
        return True
    except FileNotFoundError:
        # Check the ground too: an inaccessible/missing root is not clearance.
        if not stat.S_ISDIR(root.stat().st_mode):
            raise NotADirectoryError(root)
        return False


def atomic_write(path: Path, data: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".stop-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def marker_date(text: str) -> str:
    for line in text.split("\n\nReason:\n", 1)[0].splitlines():
        if line.startswith("UTC: "):
            return line.removeprefix("UTC: ")
    return "not read"


def marker_reason(text: str) -> str:
    return text.partition("\n\nReason:\n")[2]


def git_commit(root: Path) -> str:
    """Read HEAD/refs only, including worktree gitdir/commondir; never run git."""
    try:
        directory = root / ".git"
        if directory.is_file():
            pointer = directory.read_text(encoding="utf-8").strip()
            if not pointer.startswith("gitdir: "):
                return "not read"
            directory = (root / pointer.removeprefix("gitdir: ")).resolve()
        head = (directory / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            reference = head.removeprefix("ref: ")
            if not reference.startswith("refs/") or ".." in Path(reference).parts:
                return "not read"
            common = directory
            common_file = directory / "commondir"
            if common_file.is_file():
                common = (directory / common_file.read_text(encoding="utf-8").strip()).resolve()
            for base in (directory, common):
                ref = base / reference
                if ref.is_file():
                    head = ref.read_text(encoding="utf-8").strip()
                    break
            else:
                head = "not read"
                packed = common / "packed-refs"
                if packed.is_file():
                    for line in packed.read_text(encoding="utf-8").splitlines():
                        parts = line.split()
                        if len(parts) == 2 and parts[1] == reference:
                            head = parts[0]
                            break
        if len(head) in (40, 64) and all(c in "0123456789abcdefABCDEF" for c in head):
            return head
    except (OSError, ValueError):
        pass
    return "not read"


def _existing_marker(root: Path) -> str:
    try:
        return (root / "STOP.md").read_bytes().decode("utf-8")
    except (OSError, UnicodeError):
        return "UTC: not read"


def write_marker(root: Path, reason: str, utc: str, wake: int) -> tuple[bool, str]:
    if marker_present(root):
        return False, _existing_marker(root)
    with stop_lock(root):
        if marker_present(root):
            return False, _existing_marker(root)
        text = (f"# Stop marker\n\nUTC: {utc}\nWake: {wake}\nCommit: {git_commit(root)}"
                + "\n\nReason:\n" + (reason if reason else "No reason was given."))
        atomic_write(root / "STOP.md", text.encode("utf-8"))
        return True, text


def _cell(value: str) -> str:
    return json.dumps(value, ensure_ascii=False).replace("|", "\\u007c").replace("<", "\\u003c").replace(">", "\\u003e")


def _detail(marker: str, checker: str) -> str:
    return _cell(f"Marker UTC: {marker_date(marker)}; checked by: {checker}")


def _log_text(root: Path) -> str:
    try:
        return (root / "STOP_LOG.md").read_bytes().decode("utf-8")
    except FileNotFoundError:
        return LOG_HEADER


def _append(root: Path, text: str) -> None:
    path = root / "STOP_LOG.md"
    # Fail rather than append a partial row onto a damaged last line.
    previous = _log_text(root)
    if not previous.endswith("\n"):
        raise ValueError("Stop log does not end with a whole line")
    with path.open("ab") as handle:
        if handle.tell() == 0:
            handle.write(LOG_HEADER.encode("utf-8"))
        handle.write(text.encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())


def _rows(root: Path) -> list[str]:
    """Parse rows while skipping verbatim archives by their character lengths.

    Reasons may themselves contain tables or headings; those are never rows.
    """
    stream = io.StringIO(_log_text(root))
    rows = []
    for line in stream:
        if line.startswith("<!-- stop archive lengths: "):
            sizes = json.loads(line.removeprefix("<!-- stop archive lengths: ").removesuffix(" -->\n"))
            stream.read(sizes["marker"])
            if stream.readline() != "\n" or stream.readline() != "\n" or stream.readline() != "Operator reason:\n":
                raise ValueError("Malformed stop archive")
            stream.read(sizes["operator_reason"])
            continue
        if line.startswith("| "):
            rows.append(line.rstrip("\n"))
    return rows


def honour(root: Path, checker: str, *, once: bool = False) -> str:
    with stop_lock(root):
        marker = (root / "STOP.md").read_bytes().decode("utf-8")
        detail = _detail(marker, checker)
        rows = _rows(root)
        if not once or not any(line.endswith(f" | honoured | {detail} |")
                               for line in rows[_last_removed(rows) + 1:]):
            _append(root, f"| {now_utc()} | honoured | {detail} |\n")
        return marker


def _last_removed(rows: list[str]) -> int:
    return max((i for i, row in enumerate(rows) if " | removed | " in row), default=-1)


def honour_count(root: Path, marker: str) -> int:
    # Count only the current marker's rows, following the last removal.
    rows = _rows(root)
    count = 0
    prefix = f"Marker UTC: {marker_date(marker)}; checked by: "
    for row in rows:
        if " | removed | " in row and row.startswith("| "):
            count = 0
        if " | honoured | " in row and row.startswith("| "):
            detail = row.split(" | honoured | ", 1)[1].removesuffix(" |")
            try:
                if json.loads(detail).startswith(prefix):
                    count += 1
            except (ValueError, AttributeError):
                raise ValueError("Malformed honoured row")
    return count


def _memory_dir(root: Path) -> str:
    """The memory folder's name under the loaded naming scheme, or the default."""
    try:
        name = json.loads((root / "naming.json").read_bytes().decode("utf-8"))["dirs"]["memory"]
    except Exception:
        return "memory"
    return name if isinstance(name, str) and name else "memory"


def notice_path(root: Path) -> Path:
    return root / _memory_dir(root) / "private" / "stop-removal.json"


def read_notice(root: Path) -> dict | None:
    try:
        data = json.loads(notice_path(root).read_bytes().decode("utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(data, dict) or not all(isinstance(data.get(key), str)
                                           for key in ("marker", "operator_reason")):
        raise ValueError("Malformed stop removal notice")
    return data


def clear_notice(root: Path) -> None:
    try:
        notice_path(root).unlink(missing_ok=True)
    except OSError:
        pass


def lift(root: Path, operator_reason: str, before=None) -> tuple[str, int]:
    """Operator-only entry point. Never imported by a server as a callable tool."""
    if not operator_reason.strip():
        raise ValueError("An operator reason is required.")
    with stop_lock(root):
        marker = (root / "STOP.md").read_bytes().decode("utf-8")
        count = honour_count(root, marker)
        if before is not None:
            before(marker, count)
        utc = now_utc()
        notice = notice_path(root)
        notice.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(notice, json.dumps({"marker": marker, "operator_reason": operator_reason},
                                       ensure_ascii=False).encode("utf-8"))
        detail = _cell(f"Marker UTC: {marker_date(marker)}; honoured rows: {count}")
        _append(root, f"| {utc} | removed | {detail} |\n\n### Removed {utc}\n\n"
                + "<!-- stop archive lengths: "
                + json.dumps({"marker": len(marker), "operator_reason": len(operator_reason)})
                + " -->\n" + marker + "\n\nOperator reason:\n" + operator_reason + "\n\n")
        (root / "STOP.md").unlink()
        return marker, count


def refuse_when_stopped(root_getter, *, structured: bool = False, call: str | None = None, one_sentence: bool = False):
    """Guard writes before phase, private context, validation or outbound work."""
    def decorate(function):
        @functools.wraps(function)
        def guarded(*args, **kwargs):
            try:
                refusal = REFUSAL if marker_present(root_getter()) else None
            except (OSError, ValueError):
                refusal = CHECK_FAILURE
            if refusal:
                if call is not None:
                    tool = call.split("(", 1)[0]
                    fact = "STOP.md is set." if refusal == REFUSAL else "The stop marker could not be checked."
                    refusal = (f"{tool} REFUSED: nothing was changed because {fact.rstrip('.')}; call {call}."
                               if one_sentence else f"{tool} REFUSED: nothing was changed. {fact} Call {call}.")
                return {"error": "STOP_MARKER", "message": refusal} if structured else refusal
            return function(*args, **kwargs)
        return guarded
    return decorate
