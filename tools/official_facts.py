"""Shape a site's own official facts so the agent can read them inside its context.

The Square and the Market each publish their rules at /api/official. Those
records are the source; the agent should read them there rather than keep a
private copy. A large record is returned as an index of its fields, so the
agent asks for the ones it needs.
"""
from __future__ import annotations

import json
from typing import Any

WHOLE_UNDER_BYTES = 8000
INLINE_UNDER_BYTES = 700


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def official_view(facts: Any, fields: list[str] | None, *, source: str) -> dict[str, Any]:
    """Return the whole record, the named fields, or an index of what it holds."""
    if isinstance(facts, dict) and "error" in facts:
        return facts
    if not isinstance(facts, dict):
        return {"error": "MALFORMED_RESPONSE", "message": f"{source} did not return an object."}
    if fields:
        unknown = sorted(set(fields) - set(facts))
        if unknown:
            return {
                "error": "UNKNOWN_FIELD",
                "message": f"{source} has no field {unknown}.",
                "fields": sorted(facts),
            }
        return {"source": source, "facts": {name: facts[name] for name in fields}}
    if _size(facts) <= WHOLE_UNDER_BYTES:
        return {"source": source, "facts": facts}
    return {
        "source": source,
        "facts": {name: value for name, value in facts.items() if _size(value) <= INLINE_UNDER_BYTES},
        "larger_fields": {name: _size(value) for name, value in facts.items() if _size(value) > INLINE_UNDER_BYTES},
        "message": "The larger fields are named with their size in bytes. Ask for them by name in `fields`.",
    }
