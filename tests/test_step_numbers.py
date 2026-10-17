"""Every step reference names a step that exists.

GEMINI.md's wake runway is a numbered sequence ("Steps 1–13 are one
continuous, mandatory sequence"), and prose elsewhere points back at it by
number ("Remember the result for Step 11"). A renumber that misses one of
those references sends the agent to the wrong step with nothing failing.
These tests read the real protocol and the tool messages the agent can be shown,
and check every capitalised "Step N" against the steps that exist. Local
numbered lists (the drafting airlock, Spending, Record) are their own
scopes and use lowercase references ("the id from step 1").
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# The runway is numbered without gaps. A step deleted without renumbering
# goes here, with the date and why; any other gap fails.
KNOWN_GAPS: set[int] = set()

# References in tool text that number a tool's own procedure, not the wake
# runway: (file, the number, the exact words after it). Only these exact
# references are skipped; any other step number in the same file is checked.
OWN_STEPS = {
    ("tools/goals.py", 1, "applied:"): "read_goals' own shortening steps",
    ("tools/goals.py", 2, "applied:"): "read_goals' own shortening steps",
    ("tools/goals.py", 3, "applied:"): "read_goals' own shortening steps",
}
# Every Python file under tools/ is tool text the agent may be shown.
TOOL_TEXT = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "tools").glob("*.py"))

DASH = "[–—-]"  # en dash, em dash, hyphen
ITEM = re.compile(rf"^(\d+)(?:{DASH}(\d+))?\.\s")
SEQUENCE_LINE = re.compile(rf"Steps 1{DASH}(\d+) are one continuous")
# Joins inside a reference: "12–17", "3 to 5", "3, 4 and 5", "3, 4, and 5",
# "3 or 4", "3 & 4", "3/4".
JOIN = rf"\s*(?:{DASH}|\bto\b|,\s*and\b|,\s*or\b|,|&|/|\band\b|\bor\b)\s*"
SPEC = rf"\d+(?:{JOIN}\d+)*"
# Capitalised: the wake runway. Lowercase: a local list, or ambiguous.
REFERENCE = re.compile(rf"\bSteps?\s+({SPEC})")
LOCAL_REFERENCE = re.compile(rf"\bsteps?\s+({SPEC})")
# Anything that still looks like more of a reference after a match: the
# reference was only partly read, so it is reported, never half-checked.
LEFTOVER = re.compile(rf"^\s*(?:{DASH}|\bto\b|,|&|/|\band\b|\bor\b)[\s,&/–—-]*\d")
HEADING = re.compile(r"^#{1,6} ")


def _plain(line: str) -> str:
    """Markdown emphasis and code marks removed, so "Step **9**" reads as
    "Step 9"."""
    return re.sub(r"[*_`]", "", line)


def _numbers(spec: str) -> tuple[list[int], list[str]]:
    """'12–17' -> 12..17; '3, 4 and 5' -> [3, 4, 5]. A descending range is
    an error, and both its ends are still checked."""
    out: list[int] = []
    errors: list[str] = []
    for part in re.split(r"\s*(?:,\s*and|,\s*or|,|&|/|\band\b|\bor\b)\s*", spec):
        bounds = re.split(rf"\s*(?:{DASH}|\bto\b)\s*", part)
        ends = [int(b) for b in bounds]
        if len(ends) > 2:
            errors.append(f"'{part.strip()}' chains more than two ends into one range")
            out.extend(ends)  # every end is still checked
        elif len(ends) == 2:
            lo, hi = ends
            if lo > hi:
                errors.append(f"range {lo} to {hi} runs backwards")
                out.extend([lo, hi])
            else:
                out.extend(range(lo, hi + 1))
        else:
            out.append(ends[0])
    return out, errors


def _scan(body: str, pattern: re.Pattern) -> list[tuple[re.Match, list[int], list[str]]]:
    found = []
    for m in pattern.finditer(body):
        numbers, errors = _numbers(m.group(1))
        if LEFTOVER.match(body[m.end():]):
            errors.append(f"'{m.group(0)}' is followed by more of a reference that was not read")
        found.append((m, numbers, errors))
    return found


def parse(text: str) -> dict:
    """Read a protocol's structure.

    Returns the main sequence (number -> line), the number the "Steps 1–N"
    sentence names, the local lists (one set of numbers per heading
    section), every reference with its line number, and any reference that
    could not be read whole.
    """
    lines = text.splitlines()
    main: dict[int, int] = {}
    stated = None
    in_main = False
    section = 0
    local: dict[int, set[int]] = {}
    item_lines: set[int] = set()
    for no, line in enumerate(lines, start=1):
        m = SEQUENCE_LINE.search(line)
        if m:
            stated = int(m.group(1))
            in_main = True
        if line.startswith("## ") and in_main and main:
            in_main = False  # the next top-level heading ends the runway
        if HEADING.match(line):
            section = no
        item = ITEM.match(line)  # column 0 only: indented lists are nested
        if not item:
            continue
        item_lines.add(no)
        first, last = int(item.group(1)), int(item.group(2) or item.group(1))
        numbers = range(first, last + 1)
        if in_main:
            for n in numbers:
                if n in main:
                    raise ValueError(f"line {no}: step {n} repeated (first at line {main[n]})")
                main[n] = no
        else:
            local.setdefault(section, set()).update(numbers)

    references: list[tuple[int, int]] = []
    local_refs: list[tuple[int, int, int]] = []
    unread: list[str] = []
    section = 0
    for no, line in enumerate(lines, start=1):
        if HEADING.match(line):
            section = no
        body = ITEM.sub("", line) if no in item_lines else line
        # The "Steps 1–N are one continuous" words are checked separately;
        # the rest of that line (e.g. "until Step 13 is complete") is not.
        body = _plain(SEQUENCE_LINE.sub("", body))
        for _, numbers, errors in _scan(body, REFERENCE):
            references.extend((no, n) for n in numbers)
            unread.extend(f"line {no}: {e}" for e in errors)
        for _, numbers, errors in _scan(body, LOCAL_REFERENCE):
            local_refs.extend((no, section, n) for n in numbers)
            unread.extend(f"line {no}: {e}" for e in errors)
    return {"main": main, "stated": stated, "local": local,
            "references": references, "local_refs": local_refs, "unread": unread}


def problems(text: str, known_gaps: set[int] = frozenset()) -> list[str]:
    try:
        p = parse(text)
    except ValueError as e:
        return [str(e)]
    out: list[str] = list(p["unread"])
    main = p["main"]
    if not main:
        return out + ["no main sequence found"]
    last = max(main)
    for n in range(1, last + 1):
        if n not in main and n not in known_gaps:
            out.append(f"step {n} is missing from the sequence")
    if p["stated"] is not None and p["stated"] != last:
        out.append(f"'Steps 1–{p['stated']}' but the last step is {last}")
    for no, n in p["references"]:
        if n not in main:
            out.append(f"line {no}: Step {n} does not exist")
    for no, section, n in p["local_refs"]:
        if n not in p["local"].get(section, set()):
            out.append(f"line {no}: lowercase 'step {n}' has no local list to point at")
    return out


def tool_references(rel: str, main: dict[int, int], text: str | None = None) -> list[str]:
    """Every step number in tool text names a wake step, unless it is one
    of the exact OWN_STEPS references. Lowercase counts too: tool text has
    no local lists of its own."""
    if text is None:
        text = (ROOT / rel).read_text(encoding="utf-8")
    out = []
    any_case = re.compile(rf"\b[Ss]teps?\s+({SPEC})")
    for no, line in enumerate(text.splitlines(), start=1):
        body = _plain(line)
        for m, numbers, errors in _scan(body, any_case):
            after = body[m.end():].strip()
            if len(numbers) == 1 and not errors and any(
                    rel == f and numbers[0] == n and after.startswith(words)
                    for f, n, words in OWN_STEPS):
                continue
            out.extend(f"{rel}:{no}: {e}" for e in errors)
            for n in numbers:
                if n not in main:
                    out.append(f"{rel}:{no}: Step {n} does not exist")
    return out


def _protocol() -> str:
    return (ROOT / "GEMINI.md").read_text(encoding="utf-8")


# --- The real files ---------------------------------------------------------

def test_protocol_step_references_are_real():
    assert problems(_protocol(), KNOWN_GAPS) == []


def test_known_gaps_are_still_gaps():
    # When the runway is renumbered, KNOWN_GAPS must be emptied with it.
    main = parse(_protocol())["main"]
    assert not KNOWN_GAPS & set(main), "a known gap is a step again: update KNOWN_GAPS"


def test_governance_step_references_are_real():
    main = parse(_protocol())["main"]
    assert tool_references("STANDING-ORDERS.md", main) == []


@pytest.mark.parametrize("rel", TOOL_TEXT)
def test_tool_text_step_references_are_real(rel):
    main = parse(_protocol())["main"]
    assert tool_references(rel, main) == []


def test_every_exemption_is_used_and_has_a_reason():
    for (rel, n, words), reason in OWN_STEPS.items():
        assert rel in TOOL_TEXT and reason.strip()
        text = _plain((ROOT / rel).read_text(encoding="utf-8"))
        assert re.search(rf"\b[Ss]tep\s+{n}\s+{re.escape(words)}", text), f"unused exemption: {rel} {n}"


def test_an_exempted_phrase_with_a_different_number_is_checked():
    main = parse(_protocol())["main"]
    text = "Step 999 applied:"
    assert tool_references("tools/goals.py", main, text) == \
        ["tools/goals.py:1: Step 999 does not exist"]


def test_lowercase_in_tool_text_is_checked():
    main = parse(_protocol())["main"]
    assert tool_references("tools/x.py", main, "go back to step 999") == \
        ["tools/x.py:1: Step 999 does not exist"]


def test_a_wrong_wake_reference_fails_even_in_an_exempted_file():
    main = parse(_protocol())["main"]
    text = "Step 1 applied:\nNow go to Step 999."
    found = tool_references("tools/goals.py", main, text)
    assert found == ["tools/goals.py:2: Step 999 does not exist"]


def test_real_gate_on_the_sequence_line_is_checked():
    # GEMINI.md line 9 holds both the "Steps 1–N" sentence and the Act gate.
    text = _protocol().replace("until Step 13 is complete", "until Step 999 is complete")
    assert any("Step 999 does not exist" in p for p in problems(text, KNOWN_GAPS))


@pytest.mark.parametrize("phrase", [
    "Steps 3, 4, and 99", "Steps 3, 4 and 99", "Steps 3 to 99",
    "Steps 3 or 99", "Step 3, 99",
    # Astra's probes, 15 Oct:
    "Step **99**", "Step\t99", "Steps 3 & 99", "Steps 3/99", "Steps 3—99",
])
def test_every_number_in_a_reference_is_checked(phrase):
    text = CLEAN.replace("Everything in Steps 3–5", f"Everything in {phrase}")
    assert any("Step 99 does not exist" in p for p in problems(text))


def test_descending_range_fails_and_its_ends_are_checked():
    text = CLEAN.replace("Everything in Steps 3–5", "Everything in Steps 99-3")
    found = problems(text)
    assert any("runs backwards" in p for p in found)
    assert any("Step 99 does not exist" in p for p in found)


@pytest.mark.parametrize("phrase", ["Steps 3-4-99", "Steps 3 to 4 to 99"])
def test_a_chained_range_fails_and_every_end_is_checked(phrase):
    # Astra, recheck 15 Oct.
    text = CLEAN.replace("Everything in Steps 3–5", f"Everything in {phrase}")
    found = problems(text)
    assert any("chains more than two ends" in p for p in found)
    assert any("Step 99 does not exist" in p for p in found)


def test_a_partly_read_reference_is_reported():
    text = CLEAN.replace("Everything in Steps 3–5", "Everything in Steps 3 ,  ,99")
    assert any("not read" in p for p in problems(text))


def test_lowercase_plural_outside_a_local_list_fails():
    text = CLEAN.replace("Everything in Steps 3–5", "Everything in steps 3–99")
    assert any("no local list" in p for p in problems(text))


def test_sequence_line_gate_is_checked():
    text = CLEAN.replace("mandatory sequence.", "mandatory sequence. Do nothing until Step 99.")
    assert any("Step 99 does not exist" in p for p in problems(text))


# --- The checker ------------------------------------------------------------

CLEAN = """\
Steps 1–9 are one continuous, mandatory sequence.

1. **One.** Remember this for Step 8.
2–7. **Card.** Steps 2–7 are one call.
8. **Eight.** You did this in Step 1.
9. **Nine.** Everything in Steps 3–5 was the card.

## Act

### Spending

1. Authorise.
2. Preflight.
3. Pay with the id from step 1.
"""


def test_clean_protocol_passes():
    assert problems(CLEAN) == []


def test_reference_inside_a_range_item_passes():
    assert problems(CLEAN.replace("Step 8.", "Step 5.")) == []


def test_gap_fails():
    text = CLEAN.replace("8. **Eight.**", "10. **Eight.**").replace("Steps 1–9", "Steps 1–10")
    found = problems(text)
    assert any("step 8 is missing" in p for p in found)
    assert not any("step 9 is missing" in p for p in found)


def test_known_gap_passes():
    text = CLEAN.replace("8. **Eight.**", "10. **Eight.**").replace("Steps 1–9", "Steps 1–10")
    text = text.replace("9. **Nine.**", "11. **Nine.**").replace("Steps 1–10", "Steps 1–11")
    text = text.replace("Remember this for Step 8.", "Remember this for Step 10.")
    assert problems(text, {8, 9}) == []


def test_reference_to_a_known_gap_fails():
    text = CLEAN.replace("8. **Eight.**", "10. **Eight.**").replace("Steps 1–9", "Steps 1–10")
    text = text.replace("9. **Nine.**", "11. **Nine.**").replace("Steps 1–10", "Steps 1–11")
    assert any("Step 8 does not exist" in p for p in problems(text, {8, 9}))


def test_repeated_step_fails():
    text = CLEAN.replace("9. **Nine.**", "8. **Nine.**")
    assert any("repeated" in p for p in problems(text))


def test_stated_last_step_must_match():
    text = CLEAN.replace("Steps 1–9", "Steps 1–20")
    assert any("last step is 9" in p for p in problems(text))


def test_reference_past_the_end_fails():
    text = CLEAN.replace("Step 8.", "Step 21.")
    assert any("Step 21 does not exist" in p for p in problems(text))


def test_range_reference_past_the_end_fails():
    text = CLEAN.replace("Steps 3–5", "Steps 8–10")
    assert any("Step 10 does not exist" in p for p in problems(text))


def test_list_reference_checks_every_number():
    text = CLEAN.replace("Steps 3–5", "Steps 3, 8 and 12")
    found = problems(text)
    assert any("Step 12 does not exist" in p for p in found)
    assert not any("Step 3 does" in p or "Step 8 does" in p for p in found)


def test_lowercase_reference_outside_a_local_list_fails():
    text = CLEAN.replace("Everything in Steps 3–5", "as in step 2,")
    assert any("no local list" in p for p in problems(text))


def test_nested_numbered_list_is_not_a_main_step():
    text = CLEAN.replace("8. **Eight.** You did this in Step 1.",
                         "8. **Eight.** You did this in Step 1.\n    10. a nested item")
    assert problems(text) == []


def test_hyphen_ranges_are_read_too():
    text = CLEAN.replace("2–7.", "2-7.").replace("Steps 2–7", "Steps 2-7")
    assert problems(text) == []
