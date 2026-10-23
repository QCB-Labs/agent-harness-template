"""Commit only the files named for a recorded wake, with the repository hooks."""
from __future__ import annotations

import json
import os
from pathlib import Path, PureWindowsPath
import re
import secrets
import subprocess
import threading
import time
from datetime import datetime, timezone

from tools import paths, release, names as naming


RESULT_PATH = Path(naming.memory("private", "commit-result.json"))
# Files the harness writes for him while he works. He never has to name
# them: when they changed, a wake commit takes them along.
HARNESS_KEPT = (
    "budget.json",
    naming.memory("cards", "cards.jsonl"),
    naming.memory("cards", "index.json"),
    naming.memory("registry.json"),
    naming.rel("residents") + "/index.md",
    naming.memory("seen.json"),
)
COMMITTABLE = (" M", "M ", "MM", "A ", "AM", "??", " D", "D ", "MD", "AD")
# His own files: a changed one he did not name is reported after the commit,
# never swept in, because an operator move can be waiting in the same tree.
HIS_PREFIX = naming.dir_name("memory") + "/"
HIS_FILES = tuple(naming.rel(k) for k in ("goals", "goals_archive", "identity"))
LOG_FOLDER = naming.rel("journal") + "/"
LIST_LIMIT = 30
_states: dict[str, dict] = {}
_guard = threading.Lock()


def _state(root: Path) -> dict:
    key = str(root.resolve())
    with _guard:
        if key not in _states:
            try:
                saved = json.loads((root / RESULT_PATH).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                saved = {}
            _states[key] = {"result": saved.get("result", {"status": "NONE"}),
                            "wake": saved.get("wake", 0), "running": False,
                            "notice": saved.get("notice", False),
                            # The conversation that opened the current wake, and the
                            # wake count as it stood then.
                            "open_conversation": saved.get("open_conversation"),
                            "open_count": saved.get("open_count", 0),
                            "committed_files": saved.get("committed_files", [])}
        return _states[key]


def _save(root: Path, state: dict) -> None:
    path = root / RESULT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({key: state[key] for key in ("result", "wake", "notice", "open_conversation", "open_count", "committed_files")}) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)


def status(root: Path) -> dict:
    with _guard:
        # Loading is separate: _state also takes _guard.
        state = _states.get(str(root.resolve()))
    if state is None:
        state = _state(root)
    with _guard:
        return dict(state["result"])


def notice(root: Path, conversation: str | None = None, count: int | None = None) -> str:
    state = _state(root)
    with _guard:
        # wake_status calls this. A status call from a new conversation opens
        # a wake; until that wake writes its log, the count still names the
        # wake before it. A conversation that cannot be named counts as new.
        changed = False
        if conversation is None or conversation != state["open_conversation"]:
            state["open_conversation"] = conversation
            if isinstance(count, int) and not isinstance(count, bool):
                state["open_count"] = count
            changed = True
        sentence = ""
        if state["notice"]:
            result = state["result"]
            if result["status"] == "HOOK_REFUSED":
                sentence = "The previous wake commit was refused by its hook."
            elif result["status"] == "STILL_RUNNING":
                sentence = "The previous wake commit has no recorded completion."
            if sentence:
                state["notice"] = False
                changed = True
        if changed:
            _save(root, state)
        return sentence


def _environment(for_commit: bool = False) -> dict:
    environment = os.environ.copy()
    if for_commit:
        # The repository's hooks inherit this environment and run the test
        # suite in it. Literal pathspecs break git commands the suite runs
        # (check-ignore), and a repository override would point the suite's
        # writes at the real files. The commit takes no paths, so it needs
        # neither.
        environment.pop("AGENT_HARNESS_REPO", None)
        environment.pop("GIT_LITERAL_PATHSPECS", None)
        return environment
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    environment["GIT_LITERAL_PATHSPECS"] = "1"
    # Report individual untracked files without adding a sixth git command.
    count = int(environment.get("GIT_CONFIG_COUNT", "0"))
    environment[f"GIT_CONFIG_KEY_{count}"] = "status.showUntrackedFiles"
    environment[f"GIT_CONFIG_VALUE_{count}"] = "all"
    environment["GIT_CONFIG_COUNT"] = str(count + 1)
    return environment


def _run(root: Path, arguments: list[str], extra: dict | None = None) -> subprocess.CompletedProcess:
    environment = _environment(arguments[1] == "commit")
    environment.update(extra or {})
    # Git never reads input here. Without this it inherits the MCP server's
    # stdin, the pipe the host talks to the harness on.
    return subprocess.run(arguments, cwd=root, env=environment, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE,
                          # Porcelain output is a binary record stream: Git warnings
                          # must not become part of its first path.
                          stderr=subprocess.PIPE if arguments[1] == "status" else subprocess.STDOUT)


TRACE_KEEP = 20


def _trace_target(root: Path, call_id: str) -> Path | None:
    """A git trace file for one status call, in the operator's log folder,
    never inside the repository. The oldest are removed past TRACE_KEEP.
    None when no safe place exists. Never raises."""
    try:
        folder = paths.operator_log_dir() / "git-trace"
        if paths.inside_repo(folder, also=root) or folder.is_symlink():
            return None
        folder.mkdir(parents=True, exist_ok=True)
        old = sorted(folder.glob("status-*.txt"), key=lambda item: item.stat().st_mtime)
        for item in old[:max(0, len(old) - TRACE_KEEP + 1)]:
            item.unlink(missing_ok=True)
        target = folder / f"status-{call_id}.txt"
        if target.exists() or target.is_symlink():
            return None
        return target
    except Exception:
        return None


def _git_names() -> str:
    """Names only, never values: which git and home variables the host gave
    the harness."""
    names = sorted(name for name in os.environ
                   if name.upper().startswith("GIT_") or name.upper() in {"HOME", "XDG_CONFIG_HOME"})
    return ",".join(names) or "none"


def _changes(output: bytes) -> tuple[dict[str, str], set[str]]:
    entries = output.decode("utf-8", errors="surrogateescape").split("\0")
    changed, staged = {}, set()
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        code, name = entry[:2], entry[3:]
        changed[name] = code
        if code[0] not in (" ", "?"):
            staged.add(name)
        if "R" in code or "C" in code:
            original = entries[index]
            index += 1
            changed[original] = code
            if code[0] not in (" ", "?"):
                staged.add(original)
    return changed, staged


def _committable(name: str, changes: dict[str, str]) -> bool:
    return (name in changes and changes[name] in COMMITTABLE
            and not release.is_private(name) and not release.is_never_load(name))


def _changed_list(changes: dict[str, str]) -> str:
    """The files he could name: changed, committable, not private, not kept
    by the harness. Sorted; at most LIST_LIMIT, then a count."""
    names = sorted(name for name in changes
                   if _committable(name, changes) and name not in HARNESS_KEPT)
    if not names:
        return " No other files have changed."
    shown = ", ".join(names[:LIST_LIMIT])
    more = f", and {len(names) - LIST_LIMIT} more" if len(names) > LIST_LIMIT else ""
    return f" Changed files: {shown}{more}."


def _write_timing(root: Path, line: str) -> None:
    try:
        folder = paths.operator_log_dir()
        target = folder / "commit-timing.log"
        if paths.inside_repo(folder, also=root) or target.is_symlink() or paths.inside_repo(target, also=root):
            return
        folder.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except Exception:
        pass


def _log_timing(root: Path, fields: dict) -> None:
    """One line for the operator: a call id, status and seconds per step,
    never file names, the summary or hook output. Written by a background
    thread, so a slow disk never holds up the call. Never raises."""
    try:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        parts = " ".join(f"{key}={value:.2f}" if isinstance(value, float) else f"{key}={value}"
                         for key, value in fields.items())
        threading.Thread(target=_write_timing, args=(root, f"{stamp} {parts}\n"),
                         name="commit-timing", daemon=True).start()
    except Exception:
        pass


def _safe_to_add(root: Path, name: str, changes: dict[str, str]) -> bool:
    """A harness-kept file goes along only as itself: a plain file inside
    the repository (or a plain deletion), never a folder or link standing
    in its place, and never anything below it."""
    if not _committable(name, changes):
        return False
    if any(other.startswith(name + "/") for other in changes):
        return False
    path = root / name
    try:
        if path.is_symlink() or path.is_dir():
            return False
        if path.exists() and not path.resolve().is_relative_to(root.resolve()):
            return False
    except (OSError, ValueError):
        return False
    return True


def _this_wakes_log(name: str, wake: int) -> bool:
    return (name.startswith(LOG_FOLDER) and name.endswith(".md")
            and Path(name).name.startswith(f"{naming.wake_id(wake)}-"))


def _left_uncommitted(changes: dict[str, str], committed: list[str]) -> list[str]:
    return sorted(name for name in changes
                  if name not in committed and _committable(name, changes)
                  and (name.startswith(HIS_PREFIX) or name in HIS_FILES))


def _left_note(left: list[str]) -> str:
    shown = ", ".join(left[:LIST_LIMIT])
    more = f", and {len(left) - LIST_LIMIT} more" if len(left) > LIST_LIMIT else ""
    return (f"These changed files of yours were not committed: {shown}{more}. "
            "If this wake changed them, commit them too: "
            f'harness.{naming.tool("commit_wake")}(files=["<file>", "<file>"], summary="<one line>").')


def wait_seconds(root: Path) -> float:
    try:
        value = json.loads((root / "config/harness.json").read_text(encoding="utf-8"))["commit_wait_seconds"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 86400:
            return 360
        return value
    except (OSError, ValueError, KeyError, TypeError):
        return 360


def commit_wake(root: Path, files: list[str], summary: str, load_budget, lock: threading.Lock) -> dict:
    started = time.monotonic()
    timing: dict = {"call": "commit_wake", "id": secrets.token_hex(4)}
    result = _commit_wake(root, files, summary, load_budget, lock, started, timing)
    timing["status"] = result.get("status", "?")
    timing.setdefault("lock", 0.0)
    timing.setdefault("checks", 0.0)
    timing["returned_after"] = time.monotonic() - started
    _log_timing(root, timing)
    return result


def _commit_wake(root: Path, files: list[str], summary: str, load_budget, lock: threading.Lock,
                 started: float, timing: dict) -> dict:
    state = _state(root)

    def refuse(reason: str) -> dict:
        result = {"status": "REFUSED", "reason": reason}
        with _guard:
            if not state["running"]:
                state["result"] = result
        return result

    mark = time.monotonic()
    acquired = lock.acquire(blocking=False)
    timing["lock"] = time.monotonic() - mark
    if not acquired:
        return refuse("commit_wake is already running.")
    transferred = False
    try:
        if not isinstance(files, list) or not files or any(not isinstance(item, str) for item in files):
            return refuse("files must be a non-empty list of strings.")
        if not isinstance(summary, str) or not summary.strip():
            return refuse("summary must not be blank.")
        names = []
        for item in files:
            name = item.strip().replace("\\", "/")
            path = Path(name)
            if not name or "\0" in name or path.is_absolute() or PureWindowsPath(name).drive or name.startswith("/"):
                return refuse(f"{item!r} must be a relative file path.")
            if name.startswith("-"):
                return refuse(f"{item!r} must not start with a dash.")
            if any(character in name for character in "*?["):
                return refuse(f"{item!r} must not contain file-search characters.")
            if name == "." or ".." in path.parts:
                return refuse(f"{item!r} must not be a dot or contain a parent part.")
            try:
                contained = (root / path).resolve().is_relative_to(root.resolve())
            except (OSError, ValueError):
                contained = False
            if not contained:
                return refuse(f"{item!r} must resolve inside the repository.")
            name = path.as_posix()
            if name not in names:
                names.append(name)
        timing["env"] = _git_names()
        mark = time.monotonic()
        trace = _trace_target(root, timing["id"])
        timing["trace_setup"] = time.monotonic() - mark
        # Never let inherited trace settings copy variable or config values
        # into the trace.
        extra = {"GIT_TRACE2_PERF": str(trace), "GIT_TRACE2_ENV_VARS": "",
                 "GIT_TRACE2_CONFIG_PARAMS": ""} if trace else None
        mark = time.monotonic()
        result = _run(root, ["git", "status", "--porcelain=v1", "-z"], extra)
        timing["checks"] = time.monotonic() - mark
        if result.returncode:
            return refuse("files could not be checked by git status.")
        changes, staged = _changes(result.stdout)
        already_committed = all(name not in changes and name in state["committed_files"] for name in names)
        for name in names:
            if name not in changes and not already_committed:
                return refuse(f"{name!r} is not reported as a changed file by git."
                              + _changed_list(changes))
            if name in changes and changes[name] not in COMMITTABLE:
                return refuse(f"{name!r} must be modified, added, untracked or deleted.")
        for name in names:
            if release.is_private(name) or release.is_never_load(name):
                return refuse(f"{name!r} is private and must not be committed.")
        for name in names:
            if (root / name).is_dir():
                return refuse(f"{name!r} names a folder; name a file.")
        if any(character in summary for character in ("\n", "\r", "\0", "\u2028", "\u2029")):
            return refuse("summary must be one line.")
        summary = summary.strip()
        if re.match(re.escape(naming.load()["wake_id"]["prefix"]) + r"\d", summary):
            return refuse("summary must not begin with a wake number.")
        kept = [name for name in HARNESS_KEPT if name not in names and _safe_to_add(root, name, changes)]
        other = staged - set(names) - set(kept)
        if other:
            return refuse(f"{sorted(other)[0]!r} is already staged and was not named.")
        if already_committed:
            result = {"status": "NOTHING_TO_COMMIT", "files": names}
            with _guard:
                state["result"] = result
            return result
        wake = load_budget().get("wake_count", 0)
        # The count must have moved since this wake opened: its log is
        # written. The same wake may then commit again (a retry, a forgotten
        # file); a new wake may not commit under the last wake's number.
        if (isinstance(wake, bool) or not isinstance(wake, int) or wake < 1 or wake < state["wake"]
                or wake <= state["open_count"]):
            return refuse("The shift report comes first; write it before commit_wake.")
        # This wake's own log always goes with its commit, named or not.
        kept += [name for name in sorted(changes)
                 if _this_wakes_log(name, wake) and name not in names and name not in kept
                 and name not in staged and _safe_to_add(root, name, changes)]
        message = f"{naming.wake_id(wake)}: {summary}"
        finished = threading.Event()

        def worker() -> None:
            result = {"status": "STILL_RUNNING"}
            everything = names + kept
            times: dict = {"call": "commit_worker", "id": timing["id"],
                           "add": 0.0, "commit_and_hooks": 0.0, "rev_parse": 0.0}
            try:
                mark = time.monotonic()
                added = _run(root, ["git", "add", "--", *everything])
                times["add"] = time.monotonic() - mark
                if added.returncode:
                    _run(root, ["git", "reset", "-q", "--", *everything])
                    result = {"status": "REFUSED", "reason": "files could not be staged by git."}
                else:
                    mark = time.monotonic()
                    committed = _run(root, ["git", "commit", "-m", message])
                    times["commit_and_hooks"] = time.monotonic() - mark
                    if committed.returncode:
                        _run(root, ["git", "reset", "-q", "--", *everything])
                        lines = committed.stdout.decode("utf-8", errors="replace").splitlines()
                        last = next((line for line in reversed(lines) if line.strip()), "")[:200]
                        result = {"status": "HOOK_REFUSED", "hook_line": last}
                    else:
                        mark = time.monotonic()
                        head = _run(root, ["git", "rev-parse", "--short", "HEAD"])
                        times["rev_parse"] = time.monotonic() - mark
                        result = {"status": "COMMITTED", "commit": head.stdout.decode().strip(),
                                  "message": message, "files": names}
                        if kept:
                            result["harness_files"] = kept
                        left = _left_uncommitted(changes, everything)
                        if left:
                            result["left_uncommitted"] = left[:LIST_LIMIT]
                            result["note"] = _left_note(left)
            except OSError:
                try:
                    _run(root, ["git", "reset", "-q", "--", *everything])
                except OSError:
                    pass
                result = {"status": "REFUSED", "reason": "commit_wake could not run git."}
            finally:
                times["status"] = result["status"]
                times["total"] = time.monotonic() - started
                with _guard:
                    state["result"] = result
                    state["running"] = False
                    state["notice"] = result["status"] in ("HOOK_REFUSED", "STILL_RUNNING")
                    if result["status"] == "COMMITTED":
                        state["committed_files"] = everything
                    try:
                        _save(root, state)
                    finally:
                        lock.release()
                        finished.set()
                _log_timing(root, times)

        with _guard:
            state.update(result={"status": "STILL_RUNNING"}, wake=wake, running=True, notice=True)
            _save(root, state)
        thread = threading.Thread(target=worker, name="commit-wake", daemon=False)
        thread.start()
        transferred = True
        finished.wait(wait_seconds(root))
        return status(root)
    finally:
        if not transferred:
            lock.release()
