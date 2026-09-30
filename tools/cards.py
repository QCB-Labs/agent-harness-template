"""Append-only memory cards, deterministic tags, and lossless migration."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

SYSTEM_FIELDS = frozenset(
    {"id", "tags", "created_at", "updated_at", "wake", "source", "channel", "weight", "origin"}
)
LOGICAL_NAMES = (
    "lessons", "city_rules", "open_threads", "goals", "continuity", "journal", "residents"
)
VALID_KINDS = {
    "goal", "commitment", "thread", "rule", "reflection", "decision",
    "relationship", "log", "handover",
}


class CardError(ValueError):
    """A card request violates the storage contract."""


class MigrationFormatError(ValueError):
    """A source file does not fit its specified cutting rule."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_patterns(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def extract_tags(
    *,
    kind: str,
    text: str,
    title: str | None = None,
    patterns: dict[str, Any],
    name_index: Path | None = None,
) -> list[str]:
    """Extract stable, sorted, lower-case tags using only configuration rules."""
    haystack = "\n".join(part for part in (title, text) if part)
    tags: set[str] = {f"kind:{kind}".lower()}
    typed_refs: set[str] = set()

    for rule in patterns.get("patterns", []):
        regex = re.compile(rule["regex"], re.IGNORECASE | re.MULTILINE)
        for match in regex.finditer(haystack):
            raw_values: Iterable[str]
            captured = match.group(int(rule.get("group", 1)))
            if "capture_all" in rule:
                raw_values = re.findall(rule["capture_all"], captured)
            else:
                raw_values = (captured,)
            for value in raw_values:
                normalized = value.lower()
                tags.add(rule["tag"].format(value=normalized).lower())
                if rule.get("typed_ref"):
                    typed_refs.add(normalized.lstrip("#"))

    if patterns.get("names_from_index") and name_index and name_index.is_file():
        index_text = name_index.read_text(encoding="utf-8")
        candidates = {
            value.lower()
            for value in re.findall(patterns["names_from_index"]["regex"], index_text, re.IGNORECASE)
        }
        for handle in candidates:
            if re.search(rf"(?<![\w-]){re.escape(handle)}(?![\w-])", haystack, re.IGNORECASE):
                tags.add(f"name:{handle}")

    if patterns.get("ambiguous_refs", True):
        for ref in re.findall(r"#(\d+)", haystack):
            if ref not in typed_refs:
                tags.add(f"ref:{ref}")
    return sorted(tags)


class CardStore:
    """An append-only JSONL store with a disposable lookup index."""

    def __init__(
        self,
        memory_root: Path,
        patterns_path: Path,
        *,
        clock: Callable[[], str] = _utc_now,
        name_index: Path | None = None,
    ) -> None:
        self.memory_root = Path(memory_root)
        self.cards_dir = self.memory_root / "cards"
        self.cards_path = self.cards_dir / "cards.jsonl"
        self.index_path = self.cards_dir / "index.json"
        self.patterns = load_patterns(Path(patterns_path))
        self.clock = clock
        self.name_index = name_index

    def write(
        self,
        supplied: dict[str, Any],
        *,
        wake: str,
        source: str,
        channel: str,
        origin: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        forbidden = sorted(SYSTEM_FIELDS.intersection(supplied))
        if forbidden:
            raise CardError("caller may not supply system field(s): " + ", ".join(forbidden))
        kind = supplied.get("kind")
        text = supplied.get("text")
        if kind not in VALID_KINDS:
            raise CardError(f"invalid card kind: {kind!r}")
        if not isinstance(text, str) or not text:
            raise CardError("text must be a non-empty string")
        links = supplied.get("links", [])
        if not isinstance(links, list):
            raise CardError("links must be a list")

        self.cards_dir.mkdir(parents=True, exist_ok=True)
        state = self._current_state()
        number = 1 + max(
            (int(card_id.rsplit("-", 1)[1]) for card_id in state if card_id.startswith(f"{kind}-")),
            default=0,
        )
        now = self.clock()
        card = {
            "id": f"{kind}-{number:04d}",
            "kind": kind,
            "text": text,
            "tags": extract_tags(
                kind=kind,
                text=text,
                title=supplied.get("title"),
                patterns=self.patterns,
                name_index=self.name_index,
            ),
            "status": supplied.get("status", "live"),
            "links": links,
            "created_at": created_at or now,
            "updated_at": now,
            "wake": wake,
            "source": source,
            "channel": channel,
            "weight": 1.0,
            "origin": origin,
        }
        if supplied.get("title") is not None:
            card["title"] = supplied["title"]
        line = json.dumps(card, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        with self.cards_path.open("a", encoding="utf-8", newline="") as handle:
            handle.write(line)
        self.rebuild_index()
        return card

    def _current_state(self) -> dict[str, dict[str, Any]]:
        state: dict[str, dict[str, Any]] = {}
        if not self.cards_path.is_file():
            return state
        for raw_line in self.cards_path.read_text(encoding="utf-8").splitlines():
            if raw_line.strip():
                card = json.loads(raw_line)
                state[card["id"]] = card
        return state

    def rebuild_index(self) -> dict[str, Any]:
        ids: dict[str, int] = {}
        tags: dict[str, list[str]] = defaultdict(list)
        if self.cards_path.is_file():
            for line_number, raw_line in enumerate(
                self.cards_path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if not raw_line.strip():
                    continue
                card = json.loads(raw_line)
                ids[card["id"]] = line_number
        state = self._current_state()
        for card_id, card in state.items():
            for tag in card["tags"]:
                tags[tag].append(card_id)
        index = {"ids": dict(sorted(ids.items())), "tags": {k: sorted(v) for k, v in sorted(tags.items())}}
        self.cards_dir.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="",
        )
        return index


def load_naming(path: Path) -> dict[str, Any]:
    """Load fixture paths by logical name; filenames never live in Python."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    dirs = data.get("dirs", {})
    paths = {
        key: value.format(**dirs)
        for key, value in data.get("paths", {}).items()
        if key in LOGICAL_NAMES
    }
    return {"paths": paths, "indexes": data.get("indexes", {})}


def _segments(text: str, marker: re.Pattern[str], logical: str, path: Path) -> list[tuple[int, str]]:
    matches = list(marker.finditer(text))
    if not matches:
        raise MigrationFormatError(f"{logical}: no valid cut marker in {path}")
    result: list[tuple[int, str]] = []
    for index, match in enumerate(matches):
        start = 0 if index == 0 else match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        result.append((match.start(), text[start:end]))
    return result


def _lesson_created_at(segment: str) -> str:
    match = re.search(r"(?m)^## (\d{4}-\d{2}-\d{2})(?:-(\d{2})(\d{2})(\d{2})?)?", segment)
    if not match:
        raise MigrationFormatError("lesson segment has no date-stamp heading")
    date, hour, minute, second = match.groups()
    return f"{date}T{hour or '00'}:{minute or '00'}:{second or '00'}Z"


def _wake_from_path(path: Path) -> str:
    match = re.search(r"(?i)(?:^|[^a-z0-9])(w\d{3,})(?:[^a-z0-9]|$)", path.stem)
    if match:
        return match.group(1).lower()
    date = re.search(r"(\d{4}-\d{2}-\d{2}(?:-\d{4,6})?)", path.stem)
    return date.group(1) if date else path.stem.lower()


def _thread_title(segment: str) -> str | None:
    match = re.search(r"(?m)^- \*\*(.+?)\*\*", segment)
    return match.group(1) if match else None


def _resident_id(text: str) -> str | None:
    for pattern in (r"(?i)resident\s*#(\d+)", r"\(#(\d+)\)", r"(?m)^#.*?\b#(\d+)\b"):
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def _source_files(root: Path, logical: str, relative: str, excluded: str | None) -> list[Path]:
    target = root / relative
    if target.is_file():
        return [target]
    if not target.is_dir():
        raise MigrationFormatError(f"{logical}: configured path does not exist: {relative}")
    excluded_path = (root / excluded).resolve() if excluded else None
    return [
        path
        for path in sorted(target.rglob("*.md"))
        if path.name != ".gitkeep" and (excluded_path is None or path.resolve() != excluded_path)
    ]


def migrate(
    fixture_root: Path,
    naming_path: Path,
    patterns_path: Path,
    *,
    output_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Cut configured logical files into cards without changing source text."""
    root = Path(fixture_root)
    naming = load_naming(Path(naming_path))
    destination = Path(output_root) if output_root else root
    cards_dir = destination / "cards"
    if (cards_dir / "cards.jsonl").exists():
        raise CardError(f"migration output already exists: {cards_dir / 'cards.jsonl'}")
    resident_index_rel = naming["indexes"].get("residents")
    resident_index = root / resident_index_rel if resident_index_rel else None
    store = CardStore(destination, Path(patterns_path), name_index=resident_index)
    cards: list[dict[str, Any]] = []
    lesson_reviews: list[tuple[str, str]] = []

    for logical in LOGICAL_NAMES:
        relative = naming["paths"].get(logical)
        if relative is None:
            continue
        files = _source_files(root, logical, relative, naming["indexes"].get(logical))
        if not files:
            raise MigrationFormatError(f"{logical}: no source files under {relative}")
        for path in files:
            text = path.read_text(encoding="utf-8")
            relative_path = path.relative_to(root).as_posix()
            pieces: list[tuple[int, str]]
            if logical == "lessons":
                pieces = _segments(text, re.compile(r"(?m)^## \d{4}-\d{2}-\d{2}[^\r\n]*$"), logical, path)
            elif logical == "city_rules":
                pieces = _segments(text, re.compile(r"(?m)^(?:## [^\r\n]+|- (?! )[^^\r\n].*)$"), logical, path)
            elif logical == "open_threads":
                pieces = _segments(text, re.compile(r"(?m)^- \*\*.+?\*\*.*$"), logical, path)
            elif logical == "goals":
                pieces = _segments(text, re.compile(r"(?m)^(?:## [^\r\n]+|- (?! )[^^\r\n].*)$"), logical, path)
            elif logical == "continuity":
                pieces = _segments(text, re.compile(r"(?m)^#{2,3} [^\r\n]+$"), logical, path)
            else:
                if not text:
                    raise MigrationFormatError(f"{logical}: empty source file: {relative_path}")
                pieces = [(0, text)]

            resolved_offset = text.find("## Resolved threads") if logical == "open_threads" else -1
            for order, (position, segment) in enumerate(pieces, start=1):
                origin = {"logical": logical, "file": relative_path, "position": position, "order": order}
                supplied: dict[str, Any] = {"text": segment}
                created_at = None
                wake = "migration"
                if logical == "lessons":
                    body_length = len(segment.rstrip())
                    kind = "rule" if body_length < 400 and not re.search(r"(?m)^### ", segment) else "reflection"
                    supplied.update(kind=kind, status="recheck")
                    created_at = _lesson_created_at(segment)
                elif logical == "city_rules":
                    supplied["kind"] = "rule"
                elif logical == "open_threads":
                    supplied.update(kind="thread", title=_thread_title(segment))
                    if resolved_offset >= 0 and position >= resolved_offset:
                        supplied["status"] = "done"
                elif logical == "goals":
                    supplied["kind"] = "goal"
                elif logical == "continuity":
                    supplied["kind"] = "decision"
                elif logical == "journal":
                    supplied["kind"] = "log"
                    wake = _wake_from_path(path)
                else:
                    resident_id = _resident_id(text)
                    if resident_id is None:
                        raise MigrationFormatError(f"residents: no resident id in {relative_path}")
                    supplied["kind"] = "relationship"
                card = store.write(
                    supplied,
                    wake=wake,
                    source="self",
                    channel="migration",
                    origin=origin,
                    created_at=created_at,
                )
                if logical == "residents":
                    expected = f"resident:{resident_id}"
                    if expected not in card["tags"]:
                        card["tags"].append(expected)
                        card["tags"].sort()
                        # The just-appended version is immutable; add the corrected full version.
                        with store.cards_path.open("a", encoding="utf-8", newline="") as handle:
                            handle.write(json.dumps(card, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
                        store.rebuild_index()
                if logical == "lessons":
                    lesson_reviews.append((card["id"], card["kind"]))
                cards.append(card)

    review_lines = ["# Lesson kind review", "", "All migrated lessons remain `recheck` until confirmed.", ""]
    review_lines.extend(f"- [ ] `{card_id}` — proposed `{kind}`" for card_id, kind in lesson_reviews)
    cards_dir.mkdir(parents=True, exist_ok=True)
    (cards_dir / "kind-review.md").write_text("\n".join(review_lines) + "\n", encoding="utf-8", newline="")
    return cards


def generate_views(cards_path: Path, destination_root: Path) -> list[Path]:
    """Rebuild source files by concatenating their original ordered card text."""
    current: dict[str, dict[str, Any]] = {}
    for raw_line in Path(cards_path).read_text(encoding="utf-8").splitlines():
        if raw_line.strip():
            card = json.loads(raw_line)
            current[card["id"]] = card
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for card in current.values():
        origin = card.get("origin")
        if origin:
            grouped[origin["file"]].append(card)
    written: list[Path] = []
    for relative, cards in grouped.items():
        target = Path(destination_root) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        ordered = sorted(cards, key=lambda card: card["origin"]["order"])
        target.write_text("".join(card["text"] for card in ordered), encoding="utf-8", newline="")
        written.append(target)
    return written


def normalized_lines(path: Path) -> list[str]:
    """Comparison form permitted by §5.3: ignore only end-of-line whitespace."""
    return [line.rstrip() for line in Path(path).read_text(encoding="utf-8").splitlines()]


def round_trip_differences(source_root: Path, generated_root: Path, files: Iterable[str]) -> list[str]:
    differences: list[str] = []
    for relative in files:
        source = Path(source_root) / relative
        generated = Path(generated_root) / relative
        left = normalized_lines(source)
        right = normalized_lines(generated)
        if left == right:
            continue
        limit = max(len(left), len(right))
        for line_number in range(limit):
            old = left[line_number] if line_number < len(left) else "<missing>"
            new = right[line_number] if line_number < len(right) else "<missing>"
            if old != new:
                differences.append(f"{relative}:{line_number + 1}: {old!r} != {new!r}")
                break
    return differences
