"""Render invented bureau fixture names through the active naming scheme."""
import json
import re
from tools import names

_reference = json.loads((names.REPO_ROOT / "naming" / "bureau.json").read_text(encoding="utf-8"))
_pairs = {value.format(**_reference["dirs"]): names.rel(key)
          for key, value in _reference["paths"].items()}
for key, value in _reference["dirs"].items():
    actual = names.dir_name(key)
    _pairs.update({value: actual, value.capitalize(): actual.capitalize(),
                   value + "x/": actual + "x/", value + ".md": actual + ".md"})
_pairs.update({value: names.heading(key) for key, value in _reference["headings"].items()})
_pairs.update({value: names.tool(key) for key, value in _reference["tools"].items() if value != key})
_pattern = re.compile(r"(?<![\w-])(" + "|".join(re.escape(key) for key in sorted(_pairs, key=len, reverse=True)) + r")(?![\w-])")


def fixture_name(value: str | bytes) -> str | bytes:
    binary = isinstance(value, bytes)
    text = value.decode("utf-8", "surrogateescape") if binary else value
    text = _pattern.sub(lambda match: _pairs[match.group()], text)
    text = re.sub(r"SR-([0-9]+)", lambda match: names.wake_id(int(match.group(1))), text)
    text = text.replace("SR-", names.load()["wake_id"]["prefix"])
    return text.encode("utf-8", "surrogateescape") if binary else text
