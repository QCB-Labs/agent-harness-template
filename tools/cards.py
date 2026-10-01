"""Append-only memory cards, deterministic tags, and lossless migration."""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
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


def _without_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


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

    names = patterns.get("names_from_index")
    if names and name_index and name_index.is_file():
        index_text = name_index.read_text(encoding="utf-8")
        # handle -> its ID in the index, or None. A line regex has one group
        # (the handle) or two (handle, ID).
        known: dict[str, str | None] = {}
        for line_regex in names.get("regexes") or [names["regex"]]:
            for match in re.finditer(line_regex, index_text, re.IGNORECASE):
                handle = match.group(1).strip().lower()
                found_id = match.group(2) if match.re.groups > 1 else None
                known[handle] = found_id or known.get(handle)
        id_tag = names.get("id_tag")
        # An index handle is plain letters; the text may write the same name
        # with accents. Compare both without them.
        plain = _without_accents(haystack)
        for handle, known_id in known.items():
            mention = rf"(?<![\w-]){re.escape(_without_accents(handle))}(?![\w-])"
            if not re.search(mention, plain, re.IGNORECASE):
                continue
            tags.add(f"name:{handle}")
            # The index's own ID written right beside the handle is that ID's
            # type. Any other number beside the name stays an untyped ref.
            if id_tag and known_id and re.search(rf"{mention}\W{{0,3}}#{known_id}(?!\d)", plain, re.IGNORECASE):
                tags.add(id_tag.format(value=known_id).lower())
                typed_refs.add(known_id)

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

    def append_version(self, card: dict[str, Any], **changes: Any) -> dict[str, Any]:
        """Append a complete new version of an existing card."""
        forbidden = (SYSTEM_FIELDS - {"origin"}).intersection(changes)
        if forbidden:
            raise CardError("version changes may not set system field(s): " + ", ".join(sorted(forbidden)))
        updated = dict(card)
        updated.update(changes)
        updated["updated_at"] = self.clock()
        updated["tags"] = extract_tags(
            kind=updated["kind"],
            text=updated["text"],
            title=updated.get("title"),
            patterns=self.patterns,
            name_index=self.name_index,
        )
        line = json.dumps(updated, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        with self.cards_path.open("a", encoding="utf-8", newline="") as handle:
            handle.write(line)
        self.rebuild_index()
        return updated

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


def _continuity_segments(text: str, path: Path) -> list[tuple[int, str]]:
    bullets = list(re.finditer(r"(?m)^- (?! )[^\r\n]+", text))
    if not bullets:
        raise MigrationFormatError(f"continuity: no valid cut marker in {path}")
    starts = [0]
    heading = re.compile(r"(?m)^#{2,3} [^\r\n]+")
    for previous, current in zip(bullets, bullets[1:]):
        headings = list(heading.finditer(text, previous.end(), current.start()))
        starts.append(headings[0].start() if headings else current.start())
    return [
        (bullets[index].start(), text[starts[index]: starts[index + 1] if index + 1 < len(starts) else len(text)])
        for index in range(len(bullets))
    ]


def _read_source_text(path: Path) -> str:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


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
    for pattern in (
        r"(?im)^-\s*\*\*Resident ID:\*\*\s*(\d+)\s*$",
        r"(?im)^#\s*Resident:[^\r\n]*?\(#(\d+)\)",
        r"(?i)\bresident\s*#(\d+)",
    ):
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def _pieces_for(logical: str, text: str, path: Path) -> list[tuple[int, str]]:
    if logical == "lessons":
        return _segments(text, re.compile(r"(?m)^## \d{4}-\d{2}-\d{2}[^\r\n]*"), logical, path)
    if logical == "city_rules":
        return _segments(text, re.compile(r"(?m)^(?:## [^\r\n]+|- (?! )[^\r\n].*)"), logical, path)
    if logical == "open_threads":
        return _segments(text, re.compile(r"(?m)^- \*\*.+?\*\*[^\r\n]*"), logical, path)
    if logical == "goals":
        return _segments(text, re.compile(r"(?m)^(?:## [^\r\n]+|- (?! )[^\r\n].*)"), logical, path)
    if logical == "continuity":
        return _continuity_segments(text, path)
    if not text:
        raise MigrationFormatError(f"{logical}: empty source file: {path}")
    return [(0, text)]


def _section_status(rules: list[dict[str, str]], text: str, position: int, segment: str) -> str | None:
    """Status for an entry, from the heading of the section it sits under.

    Headings themselves keep their status, so the shape of the file stays
    visible even when every entry under a heading is withheld.
    """
    if re.match(r"#{1,6} ", segment):
        return None
    if any(rule.get("skip") and re.search(rule["skip"], segment, re.IGNORECASE) for rule in rules):
        return None
    headings = [m for m in re.finditer(r"(?m)^#{1,6} +([^\r\n]+)", text) if m.start() < position]
    if not headings:
        return None
    heading = headings[-1].group(1).strip()
    for rule in rules:
        if "heading" in rule and re.search(rule["heading"], heading, re.IGNORECASE):
            return rule["status"]
    return None


def _first_date(segment: str) -> str | None:
    match = re.search(r"(\d{4}-\d{2}-\d{2})(?:-(\d{2})(\d{2})(\d{2})?)?", segment)
    if not match:
        return None
    date, hour, minute, second = match.groups()
    return f"{date}T{hour or '00'}:{minute or '00'}:{second or '00'}Z"


def _card_spec(
    logical: str, path: Path, text: str, position: int, segment: str,
    section_status: dict[str, list[dict[str, str]]] | None = None,
) -> tuple[dict[str, Any], str, str | None]:
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
        status = _section_status((section_status or {}).get(logical, []), text, position, segment)
        if status:
            supplied["status"] = status
        # The entry's own date, where it wrote one: the migration's clock
        # says nothing about how old a rule is.
        created_at = _first_date(segment)
    elif logical == "open_threads":
        supplied.update(kind="thread", title=_thread_title(segment))
        resolved_offset = text.find("## Resolved threads")
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
        supplied["kind"] = "relationship"
    return supplied, wake, created_at


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
    migration_notes: list[str] = []

    for logical in LOGICAL_NAMES:
        relative = naming["paths"].get(logical)
        if relative is None:
            continue
        files = _source_files(root, logical, relative, naming["indexes"].get(logical))
        if not files:
            raise MigrationFormatError(f"{logical}: no source files under {relative}")
        for path in files:
            text = _read_source_text(path)
            relative_path = path.relative_to(root).as_posix()
            pieces = _pieces_for(logical, text, path)
            for order, (position, segment) in enumerate(pieces, start=1):
                origin = {"logical": logical, "file": relative_path, "position": position, "order": order}
                supplied, wake, created_at = _card_spec(logical, path, text, position, segment, store.patterns.get("section_status"))
                resident_id = _resident_id(text) if logical == "residents" else None
                if logical == "residents" and resident_id is None:
                    supplied["status"] = "recheck"
                card = store.write(
                    supplied,
                    wake=wake,
                    source="self",
                    channel="migration",
                    origin=origin,
                    created_at=created_at,
                )
                if logical == "residents":
                    expected = f"resident:{resident_id}" if resident_id else f"name:{path.stem.lower()}"
                    if expected not in card["tags"]:
                        card["tags"].append(expected)
                        card["tags"].sort()
                        # The just-appended version is immutable; add the corrected full version.
                        with store.cards_path.open("a", encoding="utf-8", newline="") as handle:
                            handle.write(json.dumps(card, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
                        store.rebuild_index()
                    if resident_id is None:
                        migration_notes.append(f"- {relative_path}: resident ID missing; tagged `name:{path.stem.lower()}` and marked `recheck`.")
                if logical == "lessons":
                    lesson_reviews.append((card["id"], card["kind"]))
                cards.append(card)

    review_lines = ["# Lesson kind review", "", "All migrated lessons remain `recheck` until confirmed.", ""]
    review_lines.extend(f"- [ ] `{card_id}` — proposed `{kind}`" for card_id, kind in lesson_reviews)
    cards_dir.mkdir(parents=True, exist_ok=True)
    (cards_dir / "kind-review.md").write_text("\n".join(review_lines) + "\n", encoding="utf-8", newline="")
    report_lines = ["# Migration report", ""]
    report_lines.extend(migration_notes or ["No migration issues."])
    (cards_dir / "migration-report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8", newline="")
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
        with target.open("w", encoding="utf-8", newline="") as handle:
            handle.write("".join(card["text"] for card in ordered))
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
        left_bytes = source.read_bytes()
        right_bytes = generated.read_bytes()
        if left_bytes == right_bytes:
            continue
        left = _read_source_text(source).splitlines(keepends=True)
        right = _read_source_text(generated).splitlines(keepends=True)
        limit = max(len(left), len(right))
        for line_number in range(limit):
            old = left[line_number] if line_number < len(left) else "<missing>"
            new = right[line_number] if line_number < len(right) else "<missing>"
            if old != new:
                differences.append(f"{relative}:{line_number + 1}: {old!r} != {new!r}")
                break
    return differences


def read_card(memory_root: Path, card_id: str, history: bool = False) -> dict[str, Any]:
    memory_root = Path(memory_root)
    versions: list[dict[str, Any]] = []
    cards_path = memory_root / "cards" / "cards.jsonl"
    if cards_path.is_file():
        for line in cards_path.read_text(encoding="utf-8").splitlines():
            card = json.loads(line)
            if card["id"] == card_id:
                versions.append(card)
    if not versions:
        return {"error": "NOT_FOUND", "id": card_id}
    result: dict[str, Any] = {"card": versions[-1]}
    if history:
        result["history"] = versions
    return result


def _config(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def recall(
    memory_root: Path,
    *,
    tags: list[str],
    text: str | None = None,
    kinds: list[str] | None = None,
    limit: int = 10,
    config_path: Path,
    wake: str = "read",
) -> dict[str, Any]:
    memory = Path(memory_root)
    store_path = memory / "cards" / "cards.jsonl"
    state: dict[str, dict[str, Any]] = {}
    for line in store_path.read_text(encoding="utf-8").splitlines() if store_path.is_file() else []:
        card = json.loads(line)
        state[card["id"]] = card
    wanted = {tag.lower() for tag in tags}
    needle = text.lower() if text else None
    allowed = set(kinds) if kinds else None
    matches = [
        card for card in state.values()
        if card.get("status") in {"live", "recheck"}
        and (not wanted or wanted.intersection(card.get("tags", [])))
        and (allowed is None or card["kind"] in allowed)
        and (needle is None or needle in (card.get("title", "") + "\n" + card["text"]).lower())
    ]
    matches.sort(
        key=lambda card: (
            len(wanted.intersection(card.get("tags", []))),
            float(card.get("weight", 0)),
            card.get("updated_at", ""),
        ),
        reverse=True,
    )
    selected = matches[:max(0, limit)]
    token_count = sum(_tokens(card["text"]) for card in selected)
    budget = int(_config(config_path)["recall_tokens"])
    refused = token_count > budget
    returned = [] if refused else selected
    result = {
        "searched": {"tags": sorted(wanted), "text": text, "kinds": kinds, "limit": limit},
        "matched": len(matches),
        "left_out": len(matches) - len(returned),
        "tokens": token_count,
        "budget": budget,
        "refused": refused,
        "cards": returned,
    }
    if refused:
        result["message"] = f"{len(selected)} cards require {token_count} tokens; narrow the query."
    reads = memory / "cards" / "reads.jsonl"
    reads.parent.mkdir(parents=True, exist_ok=True)
    event = {"time": _utc_now(), "wake": wake, "query": result["searched"], "ids": [card["id"] for card in returned], "refused": refused}
    with reads.open("a", encoding="utf-8", newline="") as handle:
        handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
    return result


def recut_file(repo_root: Path, naming_path: Path, patterns_path: Path, logical: str, path: Path) -> dict[str, list[str]]:
    root = Path(repo_root)
    path = Path(path)
    naming = load_naming(Path(naming_path))
    memory = root / json.loads(Path(naming_path).read_text(encoding="utf-8")).get("dirs", {}).get("memory", "memory")
    resident_index_rel = naming["indexes"].get("residents")
    store = CardStore(memory, patterns_path, name_index=(root / resident_index_rel if resident_index_rel else None))
    state = store._current_state()
    relative = path.relative_to(root).as_posix()
    existing = sorted(
        [card for card in state.values() if card.get("origin", {}).get("logical") == logical and card.get("origin", {}).get("file") == relative],
        key=lambda card: card["origin"]["order"],
    )
    source_text = _read_source_text(path)
    pieces = _pieces_for(logical, source_text, path)
    result = {"changed": [], "new": [], "removed": [], "unchanged": []}
    prepared: list[tuple[dict[str, Any], str, str | None, dict[str, Any]]] = []
    for index, (position, segment) in enumerate(pieces):
        supplied, wake, created_at = _card_spec(logical, path, source_text, position, segment, store.patterns.get("section_status"))
        origin = {"logical": logical, "file": relative, "position": position, "order": index + 1}
        prepared.append((supplied, wake, created_at, origin))

    matcher = SequenceMatcher(
        a=[card["text"] for card in existing],
        b=[item[0]["text"] for item in prepared],
        autojunk=False,
    )

    def add_new(item: tuple[dict[str, Any], str, str | None, dict[str, Any]]) -> None:
        supplied, wake, created_at, origin = item
        card = store.write(supplied, wake=wake, source="self", channel="migration", origin=origin, created_at=created_at)
        result["new"].append(card["id"])

    def remove_old(old: dict[str, Any]) -> None:
        updated = store.append_version(old, status="done", status_reason="removed from source file")
        result["removed"].append(updated["id"])

    for opcode, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if opcode == "equal":
            for old, item in zip(existing[old_start:old_end], prepared[new_start:new_end]):
                supplied, _, _, origin = item
                desired_status = supplied.get("status", "live")
                metadata_same = (
                    old.get("title") == supplied.get("title")
                    and old.get("status") == desired_status
                    and old.get("origin") == origin
                )
                if not metadata_same:
                    store.append_version(
                        old,
                        title=supplied.get("title"),
                        status=desired_status,
                        origin=origin,
                    )
                result["unchanged"].append(old["id"])
        elif opcode == "replace":
            old_items = existing[old_start:old_end]
            new_items = prepared[new_start:new_end]
            paired = min(len(old_items), len(new_items))
            for offset in range(paired):
                old = old_items[offset]
                supplied, _, _, origin = new_items[offset]
                updated = store.append_version(
                    old,
                    text=supplied["text"],
                    title=supplied.get("title"),
                    status=supplied.get("status", old["status"]),
                    origin=origin,
                )
                result["changed"].append(updated["id"])
            for item in new_items[paired:]:
                add_new(item)
            for old in old_items[paired:]:
                remove_old(old)
        elif opcode == "insert":
            for item in prepared[new_start:new_end]:
                add_new(item)
        elif opcode == "delete":
            for old in existing[old_start:old_end]:
                remove_old(old)
    return result


def _fit_cards(cards: list[dict[str, Any]], budget: int) -> tuple[list[dict[str, Any]], int]:
    chosen: list[dict[str, Any]] = []
    used = 0
    for card in cards:
        cost = _tokens(card["text"])
        if used + cost <= budget:
            chosen.append(card)
            used += cost
    return chosen, len(cards) - len(chosen)


def _file_order(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cards in the order their source file holds them.

    A migrated card's updated_at is the time of the migration, not the age of
    the memory, so it can't order them. Cards written since (no origin file)
    come first, newest first.
    """
    fresh = [c for c in cards if not (c.get("origin") or {}).get("file")]
    filed = [c for c in cards if (c.get("origin") or {}).get("file")]
    fresh.sort(key=lambda c: c["updated_at"], reverse=True)
    filed.sort(key=lambda c: (c["origin"]["file"], c["origin"].get("order", 0)))
    return fresh + filed


def _wake_added_section(name: str, candidates: list[dict[str, Any]], budget: int) -> dict[str, Any]:
    """Fit whole entries, including separators and recall guidance in the budget."""
    is_index = name == "Open threads (index)"
    entries = []
    for card in candidates:
        if is_index:
            tags = ", ".join(t for t in card.get("tags", []) if t.startswith(("resident:", "place:")))
            entries.append(f"- {card['id']}: {card.get('title') or card['id']} [{tags}]")
        else:
            entries.append(card["text"])

    def render(chosen: list[int]) -> str:
        body = ("\n" if is_index else "\n\n").join(entries[i] for i in chosen) if chosen else "none"
        if is_index:
            body += "\nRecall a thread with read_card(<id>) or recall(tags=[...])."
        missing = [candidates[i]["id"] for i in range(len(entries)) if i not in chosen]
        if missing and is_index:
            # Name them: an open thread that isn't listed must still be findable.
            body += f"\n\n{len(missing)} more thread(s) not listed: {', '.join(missing)}. Read each with read_card(<id>)."
        elif missing:
            kind = {"Decisions": "decision", "Rules": "rule"}[name]
            body += f'\n\n{len(missing)} more card(s) left out. Use recall(kinds=["{kind}"]) with narrower tags.'
        return body

    chosen: list[int] = []
    for i in range(len(entries)):
        if _tokens(render(chosen + [i])) <= budget:
            chosen.append(i)
    body = render(chosen)
    return {"name": name, "text": body, "tokens": _tokens(body), "shown": len(chosen), "left_out": len(entries) - len(chosen)}


def read_wake_card(
    repo_root: Path,
    naming_path: Path,
    *,
    config_path: Path,
    here_tags: list[str] | None = None,
) -> dict[str, Any]:
    root = Path(repo_root)
    raw_naming = json.loads(Path(naming_path).read_text(encoding="utf-8"))
    dirs = raw_naming.get("dirs", {})
    resolved = {key: value.format(**dirs) for key, value in raw_naming.get("paths", {}).items()}
    memory = root / dirs.get("memory", "memory")
    state: dict[str, dict[str, Any]] = {}
    cards_path = memory / "cards" / "cards.jsonl"
    for line in cards_path.read_text(encoding="utf-8").splitlines():
        card = json.loads(line)
        state[card["id"]] = card
    cards = list(state.values())
    cfg = _config(config_path)["wake_sections"]
    identity = _read_source_text(root / resolved["identity"])
    governance = _read_source_text(root / resolved["governance"])
    handovers = [c for c in cards if c["kind"] == "handover" and c["status"] in {"live", "recheck"}]
    handovers.sort(key=lambda c: c["updated_at"], reverse=True)
    doing: list[dict[str, Any]] = handovers[:1]
    if not doing:
        logs = sorted((c for c in cards if c["kind"] == "log"), key=lambda c: c["updated_at"], reverse=True)
        if logs:
            fallback = dict(logs[0])
            parts = re.split(r"(?m)(?=^#{1,6} )", fallback["text"])
            fallback["text"] = parts[-1] if parts else fallback["text"]
            doing.append(fallback)
    doing.extend(c for c in cards if c["kind"] == "goal" and c["status"] == "live")
    owing = sorted((c for c in cards if c["kind"] == "commitment" and c["status"] == "live"), key=lambda c: c["created_at"])
    threads = _file_order([c for c in cards if c["kind"] == "thread" and c["status"] == "live"])
    decisions = sorted((c for c in cards if c["kind"] == "decision" and c["status"] == "live"), key=lambda c: ((c.get("origin") or {}).get("file", ""), (c.get("origin") or {}).get("order", 0)))
    rules = _file_order([c for c in cards if c["kind"] == "rule" and c["status"] == "live"])
    checking = sorted((c for c in cards if c["status"] == "recheck"), key=lambda c: (c.get("weight", 0), c["created_at"]), reverse=True)
    withheld_rules = {
        status: sum(1 for c in cards if c["kind"] == "rule" and c["status"] == status)
        for status in ("recheck", "done")
    }
    wanted = {tag.lower() for tag in (here_tags or [])}
    here = [
        c for c in cards
        if c["kind"] in {"thread", "relationship"} and c["status"] == "live"
        and wanted.intersection(c.get("tags", []))
    ]
    groups = [
        ("What I was doing", doing, int(cfg["what_i_was_doing"])),
        ("What I owe", owing, int(cfg["what_i_owe"])),
        ("Open threads (index)", threads, int(cfg["thread_index"])),
        ("Decisions", decisions, int(cfg["decisions"])),
        ("Rules", rules, int(cfg["rules"])),
        ("What needs checking", checking, int(cfg["what_needs_checking"])),
        ("What's here", here, int(cfg["whats_here"])),
    ]
    sections = [{"name": "Who I am", "text": identity + "\n\n" + governance, "tokens": _tokens(identity + "\n\n" + governance), "shown": 2, "left_out": 0}]
    for name, candidates, budget in groups:
        if name in {"Open threads (index)", "Decisions", "Rules"}:
            section = _wake_added_section(name, candidates, budget)
            if name == "Rules" and any(withheld_rules.values()):
                note = (
                    f"\n\nNot shown here: {withheld_rules['recheck']} rule card(s) waiting to be checked "
                    f"and {withheld_rules['done']} closed. They are not confirmed. "
                    'Find them with recall(kinds=["rule"]) or read_card(<id>).'
                )
                section["text"] += note
                section["tokens"] = _tokens(section["text"])
            sections.append(section)
            continue
        chosen, left = _fit_cards(candidates, budget)
        body = "\n\n".join(card["text"] for card in chosen) if chosen else "none"
        if left:
            body += f"\n\n{left} more card(s) left out. Use recall with narrower tags or kinds."
        sections.append({"name": name, "text": body, "tokens": _tokens(body), "shown": len(chosen), "left_out": left})
    rendered = "\n\n".join(f"## {section['name']}\n\n{section['text']}" for section in sections)
    # The text goes out once. Sections carry sizes and counts only, so the card
    # doesn't cost twice its length in the agent's context.
    summary = [{key: value for key, value in section.items() if key != "text"} for section in sections]
    return {"text": rendered, "sections": summary, "total_budgeted_tokens": sum(section["tokens"] for section in sections[1:])}
