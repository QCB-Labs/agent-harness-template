"""Every tool call the agent is shown must be one it can make as written.

The wake protocol and the messages the tools print are instructions. A call
written there with an unnamed argument, a wrong argument name or a missing
required one fails when the agent copies it.
"""
import ast
import asyncio
import re
from pathlib import Path

import pytest

from tools import harness_mcp

ROOT = Path(__file__).resolve().parents[1]
SERVERS = {"harness": harness_mcp}
MESSAGE_SOURCES = ["tools/harness_mcp.py", "tools/cards.py", "tools/goals.py", "tools/threads.py", "tools/commit.py", "tools/casefiles.py", "tools/verdicts.py"]


def _registry() -> dict[str, dict[str, dict[str, set[str]]]]:
    registry: dict[str, dict[str, dict[str, set[str]]]] = {}
    for prefix, module in SERVERS.items():
        registry[prefix] = {
            tool.name: {
                "all": set(tool.inputSchema.get("properties", {})),
                "required": set(tool.inputSchema.get("required", []) or []),
            }
            for tool in asyncio.run(module.mcp.list_tools())
        }
    return registry


REGISTRY = _registry()
# Validate messages against the local server registry.
ANY_TOOL = {
    name: spec
    for prefix in REGISTRY
    for name, spec in REGISTRY[prefix].items()
}


def _arguments(inside: str) -> list[str]:
    """Split a bracketed argument list on commas that are not nested."""
    parts, depth, current = [], 0, ""
    for char in inside:
        if char in "([{<":
            depth += 1
        elif char in ")]}>":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += char
    if current.strip():
        parts.append(current.strip())
    return parts


def _problems(name: str, inside: str, spec: dict[str, set[str]]) -> list[str]:
    problems, named = [], set()
    for argument in _arguments(inside):
        match = re.match(r"^(\w+)\s*=", argument)
        if not match:
            problems.append(f"argument `{argument}` has no name")
        elif match.group(1) not in spec["all"]:
            problems.append(f"`{match.group(1)}` is not a parameter of {name}")
        else:
            named.add(match.group(1))
    missing = spec["required"] - named
    if missing:
        problems.append(f"required {sorted(missing)} not shown")
    return problems


def _protocol_calls() -> list[tuple[int, str, str, str]]:
    calls = []
    for number, line in enumerate((ROOT / "AGENTS.md").read_text(encoding="utf-8").splitlines(), start=1):
        for match in re.finditer(r"\b(harness|market|square)\.([a-z_]+)(?:\(([^()]*)\))?", line):
            # Square and Market are external servers, like the city.
            if match.group(1) in REGISTRY:
                calls.append((number, match.group(1), match.group(2), match.group(3)))
    return calls


def _message_calls() -> list[tuple[str, int, str, str]]:
    pattern = re.compile(r"\b(" + "|".join(sorted(ANY_TOOL, key=len, reverse=True)) + r")\(([^()]*)\)")
    calls = []
    for relative in MESSAGE_SOURCES:
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for match in pattern.finditer(node.value):
                    calls.append((relative, node.lineno, match.group(1), match.group(2)))
    return calls


def test_protocol_names_only_tools_that_exist():
    unknown = [
        f"AGENTS.md:{line} {prefix}.{name}"
        for line, prefix, name, _ in _protocol_calls()
        if name not in REGISTRY[prefix]
    ]
    assert unknown == []


def test_protocol_calls_can_be_made_as_written():
    faults = []
    for line, prefix, name, inside in _protocol_calls():
        if inside is None or name not in REGISTRY[prefix]:
            continue
        for problem in _problems(name, inside, REGISTRY[prefix][name]):
            faults.append(f"AGENTS.md:{line} {prefix}.{name}({inside}): {problem}")
    assert faults == []


def test_tool_messages_show_calls_that_can_be_made_as_written():
    faults = []
    for relative, line, name, inside in _message_calls():
        for problem in _problems(name, inside, ANY_TOOL[name]):
            faults.append(f"{relative}:{line} {name}({inside}): {problem}")
    assert faults == []


@pytest.mark.parametrize(
    "inside, expected",
    [
        ('card_id="<id>"', []),
        ("<id>", ["argument `<id>` has no name", "required ['card_id'] not shown"]),
        ('wrong="x"', ["`wrong` is not a parameter of read_card", "required ['card_id'] not shown"]),
        ("", ["required ['card_id'] not shown"]),
    ],
)
def test_the_checker_itself(inside, expected):
    assert _problems("read_card", inside, REGISTRY["harness"]["read_card"]) == expected
