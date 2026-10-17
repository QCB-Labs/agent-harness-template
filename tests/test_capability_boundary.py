"""The harness must never gain a general-purpose file or shell capability.

The agent receives email from strangers. That mail lands in its context as
untrusted input. The defence against it being talked into leaking a private
file rests on the SHAPE of its tool surface: every reader is fixed-target
(read_identity, read_lessons, read_map), so a prompt injection has no parameter
to point at a file it should not see.

These tests fail loudly if that shape changes. Two classes:

  T1  — no tool TAKES a path, and no shell/eval capability exists.
  T1b — no string parameter BECOMES a path. A slug interpolated into a glob
        is a path parameter in disguise; `.strip().lower()` is not validation.

The house convention for anything that names a file is: re.fullmatch to
validate, then resolve() + is_relative_to() to contain. See read_resident.
"""

from __future__ import annotations

import ast
import inspect
import types
from pathlib import Path

import pytest

from tools import names
from tools import harness_mcp

REPO = Path(__file__).resolve().parent.parent

SERVERS = [
    REPO / "tools" / "harness_mcp.py",
    REPO / "tools" / "square_mcp.py",
    REPO / "tools" / "market_mcp.py",
]

FORBIDDEN_TOOL_NAMES = {
    "read_file", "write_file", "cat", "glob", "list_dir", "listdir",
    "exec", "execute", "run", "run_command", "shell", "bash", "powershell",
    "open_file", "get_file", "fetch_file",
}

FORBIDDEN_IMPORTS = {"subprocess", "pty", "commands"}

# commit_scope.py reads staged paths and blobs for the operator commit hook.
# backfill_casefiles.py reads git history; operator-run only, never a tool.
SUBPROCESS_MODULES = {"rename_scheme.py", "backfill_casefiles.py", "commit_scope.py", "wallet.py", "release_scan.py", "commit.py"}  # Payments, read-only release scan, named-file commits.


def test_subprocess_is_confined_to_approved_modules():
    for path in (REPO / "tools").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        imports = {alias.name.split(".")[0] for node in ast.walk(tree)
                   if isinstance(node, ast.Import) for alias in node.names}
        imports.update((node.module or "").split(".")[0] for node in ast.walk(tree)
                       if isinstance(node, ast.ImportFrom))
        if "subprocess" in imports:
            assert path.name in SUBPROCESS_MODULES


# Parameter names that would let a caller aim a tool at an arbitrary file.
PATH_PARAM_NAMES = {"path", "filepath", "file_path", "fullpath", "abspath", "dirpath"}

# Tools that take a NAME (not a path) and resolve it inside a fixed directory.
# Anything added here needs a comment explaining how it is contained.
NAME_PARAM_ALLOWLIST = {
    "file_unrecorded",            # resolves inside memory/journal, name only
}

# A name may be interpolated into a glob pattern only if it is a module-level
# CONSTANT, an int-annotated parameter, or a clean_* local in a function that
# also performs an is_relative_to containment check.
CLEAN_PREFIX = "clean_"


def _tools() -> dict:
    return harness_mcp.mcp._tool_manager._tools


def _fn(tool) -> object | None:
    return getattr(tool, "fn", None)


# --------------------------------------------------------------------------
# T1 — no tool TAKES a path
# --------------------------------------------------------------------------

def test_no_general_purpose_file_or_shell_tool() -> None:
    for name in _tools():
        assert name not in FORBIDDEN_TOOL_NAMES, (
            f"Tool '{name}' is a general-purpose capability. It collapses the "
            "information-release gate: an injected model could point it at "
            f"{names.dir_name('drafts')}/ or secrets.py."
        )


def test_no_tool_accepts_an_arbitrary_filesystem_path() -> None:
    for name, tool in _tools().items():
        fn = _fn(tool)
        if fn is None:
            continue
        for param in inspect.signature(fn).parameters:
            if param in PATH_PARAM_NAMES:
                assert name in NAME_PARAM_ALLOWLIST, (
                    f"Tool '{name}' takes path-like parameter '{param}'. "
                    "Fixed-target readers only."
                )


@pytest.mark.parametrize("path", SERVERS, ids=lambda p: p.name)
def test_server_imports_no_shell_module(path: Path) -> None:
    if not path.exists():
        pytest.skip(f"{path.name} not present")
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            names = {(node.module or "").split(".")[0]}
        else:
            continue
        bad = names & FORBIDDEN_IMPORTS
        assert not bad, f"{path.name} imports {bad} — shell access breaks the gate."


@pytest.mark.parametrize("path", SERVERS, ids=lambda p: p.name)
def test_server_calls_no_dynamic_execution(path: Path) -> None:
    if not path.exists():
        pytest.skip(f"{path.name} not present")
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    banned = {"eval", "exec", "compile", "__import__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in banned, (
                f"{path.name} calls {node.func.id}() — arbitrary execution."
            )


@pytest.mark.parametrize("path", SERVERS, ids=lambda p: p.name)
def test_server_does_not_use_os_system(path: Path) -> None:
    if not path.exists():
        pytest.skip(f"{path.name} not present")
    assert "os.system" not in path.read_text(encoding="utf-8"), (
        f"os.system in {path.name} breaks the gate."
    )


# --------------------------------------------------------------------------
# T1b — no string parameter BECOMES a path
# --------------------------------------------------------------------------

def _interpolated_names(node: ast.AST) -> set[str]:
    """Names substituted into an f-string."""
    names: set[str] = set()
    if isinstance(node, ast.JoinedStr):
        for part in node.values:
            if isinstance(part, ast.FormattedValue):
                for sub in ast.walk(part.value):
                    if isinstance(sub, ast.Name):
                        names.add(sub.id)
    return names


def _int_params(fn: ast.FunctionDef) -> set[str]:
    """Parameters annotated int — they cannot carry a path separator."""
    out: set[str] = set()
    for arg in list(fn.args.args) + list(fn.args.kwonlyargs):
        annotation = ast.unparse(arg.annotation) if arg.annotation else ""
        if "int" in annotation and "str" not in annotation:
            out.add(arg.arg)
    return out


@pytest.mark.parametrize("path", SERVERS, ids=lambda p: p.name)
def test_glob_patterns_never_interpolate_unvalidated_input(path: Path) -> None:
    """A slug reaching a glob unvalidated is a path parameter in disguise.

    Verified to catch the real thing: run against the commit before
    4ae6390 this reports read_place interpolating 'slug' unvalidated.
    """
    if not path.exists():
        pytest.skip(f"{path.name} not present")
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    constants = {
        target.id
        for node in tree.body if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id.isupper()
    }

    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        contained = "is_relative_to" in ast.unparse(fn)
        safe_ints = _int_params(fn)

        for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
            if not (
                isinstance(call.func, ast.Attribute)
                and call.func.attr in ("glob", "rglob")
            ):
                continue
            for arg in call.args:
                for name in _interpolated_names(arg):
                    if name in constants or name in safe_ints:
                        continue
                    assert name.startswith(CLEAN_PREFIX), (
                        f"{path.name}:{fn.name} interpolates '{name}' into a glob "
                        "without validation. Validate with re.fullmatch and name "
                        f"the result {CLEAN_PREFIX}*."
                    )
                    assert contained, (
                        f"{path.name}:{fn.name} globs on '{name}' but has no "
                        "is_relative_to containment check on the results."
                    )


TRAVERSAL_PAYLOADS = [
    "sub/../../../../private",
    "x/../../../identity",
    "../../..",
    "*",
    "*/../../../*",
    "/etc/passwd",
    "C:/Windows/System32/drivers/etc/hosts",
    "..\\..\\..\\secrets",
    "%2e%2e/%2e%2e/secrets",
]

SECRET_MARKER = "OPERATOR-SECRET-CANARY"


def _is_str_param(annotation: object) -> bool:
    """True for `str` and `str | None`, false for int and everything else."""
    if annotation is str:
        return True
    if isinstance(annotation, types.UnionType):
        return str in annotation.__args__
    return isinstance(annotation, str) and annotation.startswith("str")


def _callable_with_one_kwarg(fn) -> list[str]:
    """String parameters that can be supplied alone (all others have defaults)."""
    signature = inspect.signature(fn)
    result = []
    for name, param in signature.parameters.items():
        if not _is_str_param(param.annotation):
            continue
        others_optional = all(
            other.default is not inspect.Parameter.empty
            for other_name, other in signature.parameters.items()
            if other_name != name
        )
        if others_optional:
            result.append(name)
    return result


def test_string_taking_read_tools_refuse_traversal_payloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Behavioural complement to the static check.

    Plants a canary at the repo root and a subdirectory in every directory a
    read tool globs, then feeds traversal payloads to every string parameter.
    The subdirectory matters: traversal needs the first pattern component to
    match a real directory entry, so without one a latent bug looks safe.
    """
    monkeypatch.setattr(harness_mcp, "REPO", tmp_path)
    (tmp_path / "PRIVATE.md").write_text(SECRET_MARKER, encoding="utf-8")

    for relative in (f"{names.rel('atlas')}/continents", f"{names.rel('atlas')}/places",
                     f"{names.rel('residents')}", "drafts", f"{names.rel('journal')}"):
        directory = tmp_path / relative
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "sub").mkdir(exist_ok=True)

    checked = 0
    for name, tool in _tools().items():
        fn = _fn(tool)
        if fn is None or not name.startswith("read_"):
            continue
        for param in _callable_with_one_kwarg(fn):
            for payload in TRAVERSAL_PAYLOADS:
                try:
                    result = str(fn(**{param: payload}))
                except Exception:
                    continue  # refusing by raising is acceptable
                checked += 1
                assert SECRET_MARKER not in result, (
                    f"{name}({param}={payload!r}) returned a file from outside "
                    "its own directory."
                )

    assert checked, "No string-taking read tool was exercised — test is inert."
