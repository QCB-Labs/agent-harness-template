"""Flag a verdict on someone else's reliability before it is stored or posted.

A tripwire, not a judge. It matches phrases from ``config/verdict-words.json``
and nothing else: it cannot tell a true verdict from a false one, it cannot
tell whether a basis is given beside it, and it misses any rewording that
avoids the list. A flag does not refuse a write. The harness writes it at the
end of the very line that holds the verdict, headings included, so no cut of
the file into cards can part the two and a heading's title carries it too.
Only text carrying the flag emoji is taken for a flag; flag-like text without
it is the agent's own and is never touched.

"""
from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable
from pathlib import Path

FLAG = "\U0001F6A9"
MARK_PREFIX = f"*[{FLAG} Flagged by the harness:"
# A flag the harness wrote: it always carries the flag emoji, so text the
# agent writes in a flag's shape without it is his and is left alone. Found
# alone on a line or at the very end of one; never in the middle of a line.
# The body of a flag can never run past its own closing "]*".
_MARK = r"\*\[\U0001F6A9 Flagged by the harness:(?:(?!\]\*)[^\n])*\]\*"
# On a line of its own: taken out with the line break before it (and its own
# carriage return), so the line above is left exactly as it was.
_MARK_OWN_LINE = re.compile(r"\n" + _MARK + r"\r?(?=\n|\Z)")
_MARK_FIRST_LINE = re.compile(r"\A" + _MARK + r"\r?(?:\n|\Z)")
# At the end of a line: taken out with the one space the harness put before it.
_MARK_INLINE = re.compile(r" " + _MARK + r"(?=\r?(?:\n|\Z))")


def _normalise(text: str) -> str:
    """Lower case, one space for any run of whitespace."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).lower()).strip()


def _compile(phrase: str) -> re.Pattern:
    words = [re.escape(word) for word in _normalise(phrase).split(" ") if word]
    return re.compile(r"(?<![a-z0-9])" + r"[ \-]+".join(words) + r"(?![a-z0-9])")


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and _normalise(item)]


def load_words(root: Path) -> tuple[list[str], list[str]]:
    """Return (claims, ignore).

    No file, an unreadable one, a file that is not an object, or a "claims"
    that is not a list of phrases means no words: the tools then store text
    exactly as before. An "ignore" of the wrong shape is treated as empty.
    """
    try:
        data = json.loads((root / "config" / "verdict-words.json").read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return [], []
        claims = _strings(data.get("claims"))
        return (claims, _strings(data.get("ignore"))) if claims else ([], [])
    except Exception:
        return [], []


def _flatten(text: str) -> tuple[str, list[int]]:
    """The text lower-cased with each run of whitespace as one space, and the
    0-based source line of every character, so a phrase wrapped over a line
    break still matches and is placed on the line where it starts."""
    chars: list[str] = []
    line_of: list[int] = []
    line_no, pending_space = 0, False
    # Quote markers are layout; retain newlines for the source line map.
    text = re.sub(r"(?m)^ {0,3}(?:>[ \t]?)+", "", text)
    for ch in unicodedata.normalize("NFKC", text).lower():
        if ch.isspace():
            if ch == "\n":
                line_no += 1
            pending_space = bool(chars)
            continue
        if pending_space:
            chars.append(" ")
            line_of.append(line_no)
            pending_space = False
        chars.append(ch)
        line_of.append(line_no)
    return "".join(chars), line_of


def find_by_line(text: str, claims: list[str], ignore: list[str]) -> dict[int, list[str]]:
    """Listed phrases by the 0-based line each one starts on. Longest wins."""
    # NFKC can change a line's length but never adds or removes a newline.
    flat, line_of = _flatten(text)
    for phrase in ignore:
        flat = _compile(phrase).sub(lambda match: " " * len(match.group(0)), flat)
    taken = bytearray(len(flat))
    by_line: dict[int, list[str]] = {}
    for phrase in sorted({_normalise(item) for item in claims}, key=lambda item: (-len(item), item)):
        for match in _compile(phrase).finditer(flat):
            start, end = match.span()
            if 1 in taken[start:end]:
                continue
            taken[start:end] = b"\x01" * (end - start)
            found = by_line.setdefault(line_of[start], [])
            if phrase not in found:
                found.append(phrase)
    return by_line


def find(text: str, claims: list[str], ignore: list[str]) -> list[str]:
    """The listed phrases found anywhere in ``text``, each once, sorted."""
    return sorted({phrase for found in find_by_line(text, claims, ignore).values() for phrase in found})


def strip_marks(text: str) -> str:
    """Remove flags the harness added earlier, so a re-sent text is checked
    fresh and never carries two.

    Known limit: a flag is plain text, so text he writes himself in exactly a
    flag's form, placed at the very end of a line after a space or alone on a
    line, cannot be told from one the harness wrote, and is removed too.
    Flag-shaped text anywhere else in a line is never touched."""
    return _MARK_INLINE.sub("", _MARK_OWN_LINE.sub("", _MARK_FIRST_LINE.sub("", text)))


def _flag(phrases: list[str], checked: bool = False) -> str:
    # A phrase is shown without the characters that end a flag.
    quoted = ", ".join('"' + re.sub(r"[\[\]*]", "", phrase) + '"' for phrase in phrases)
    state = "Checked." if checked else "Not checked."
    return f"{MARK_PREFIX} {quoted}. {state}]*"


# Exactly where and how the harness writes a flag: at the very end of the
# line (a carriage return aside), after one space or alone on its line. Flag
# text followed by anything, even spaces, is his and is not read as a flag.
_END_MARK = re.compile(r"(?:^|(?<= ))" + _MARK + r"\r?\Z")


def flagged_lines(text: str | None) -> list[tuple[str, str]]:
    """(line without its flag, the flag's quoted phrases) for every line of
    ``text`` that carries a harness flag; for the operator's casefile.

    Only the flag at the very end of a line counts: that is where the
    harness puts it. Flag-shaped text earlier in the line is his and stays
    part of the line."""
    if not text:
        return []
    out: list[tuple[str, str]] = []
    lines = text.split("\n")
    for number, line in enumerate(lines):
        # The line as it is: at most one carriage return (a Windows line
        # ending) may follow a real flag, and the pattern allows exactly that.
        m = _END_MARK.search(line)
        if not m:
            continue
        body = line[:m.start()].rstrip()
        if not body and number > 0:
            body = lines[number - 1].rstrip("\r")  # a flag alone under a heading
        phrases = ", ".join(re.findall(r'"([^"]*)"', m.group(0)))
        out.append((body, phrases))
    return out


def mark(root: Path, text: str | None,
         checked: Callable[[str, str], bool] = lambda line, phrases: False
         ) -> tuple[str | None, list[str]]:
    """Return (text to store, phrases found).

    Text with no listed phrase and no earlier flag comes back exactly as
    given. Otherwise every line on which a listed phrase starts, a heading
    included, ends with one flag, ending "Checked." when the operator's
    evidence names that line's casefile row, else "Not checked.".
    """
    if text is None or not text.strip():
        return text, []
    claims, ignore = load_words(root)
    if not claims:
        return text, []
    clean = strip_marks(text)
    by_line = find_by_line(clean, claims, ignore)
    if not by_line:
        return clean, []
    out: list[str] = []
    for number, line in enumerate(clean.split("\n")):
        found = by_line.get(number)
        if not found:
            out.append(line)
            continue
        ending = "\r" if line.endswith("\r") else ""
        body = line[:-1] if ending else line
        phrases = sorted(found)
        state = checked(body.rstrip(), ", ".join(
            re.sub(r"[\[\]*]", "", phrase) for phrase in phrases))
        out.append(f"{body} {_flag(phrases, state)}{ending}")
    return "\n".join(out), sorted({phrase for found in by_line.values() for phrase in found})
