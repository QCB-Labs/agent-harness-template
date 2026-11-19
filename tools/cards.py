"""Append-only memory cards, deterministic tags, and lossless migration."""

from __future__ import annotations

from tools import names

import json
import math
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from pydantic_core import to_json

SYSTEM_FIELDS = frozenset(
    {"id", "tags", "created_at", "updated_at", "wake", "source", "channel", "weight", "origin", "role"}
)
LOGICAL_NAMES = (
    "lessons", "city_rules", "open_threads", "goals", "journal", "residents", "goal_files", "projects", "threads",
    "threads_archive"
)
VALID_KINDS = {
    "goal", "commitment", "thread", "rule", "reflection", "decision",
    "relationship", "log", "handover", "project",
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


def _jsonl_cards(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    """Read physical JSONL records; Unicode separators belong to card text."""
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8", newline="") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            if raw_line.strip():
                yield line_number, json.loads(raw_line)


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
        role: str | None = None,
    ) -> dict[str, Any]:
        forbidden = sorted(SYSTEM_FIELDS.intersection(supplied))
        if forbidden:
            raise CardError("caller may not supply system field(s): " + ", ".join(forbidden))
        kind = supplied.get("kind")
        text = supplied.get("text")
        if kind not in VALID_KINDS:
            raise CardError(f"invalid card kind: {kind!r}")
        empty_source = (
            (origin or {}).get("key") == "header"
            and ((origin or {}).get("logical"), kind) in {("journal", "log"), ("residents", "relationship")}
        )
        if not isinstance(text, str) or (not text and not empty_source):
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
        if role is not None:
            card["role"] = role
        card["tags"] = sorted(set(card["tags"]) | set((origin or {}).get("file_tags", [])))
        if supplied.get("title") is not None:
            card["title"] = supplied["title"]
        line = json.dumps(card, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        with self.cards_path.open("a", encoding="utf-8", newline="") as handle:
            handle.write(line)
        self._refresh_index()
        return card

    def _refresh_index(self) -> None:
        """The index is disposable and nothing reads it to answer. If it
        can't be rewritten (another program holding the file), the card
        already written stands and the rest of the cut goes on; the next
        write rebuilds it whole."""
        try:
            self.rebuild_index()
        except OSError:
            pass

    def _current_state(self) -> dict[str, dict[str, Any]]:
        state: dict[str, dict[str, Any]] = {}
        for _, card in _jsonl_cards(self.cards_path):
            state[card["id"]] = card
        return state


    def append_version(self, card: dict[str, Any], *, role: str | None = None, **changes: Any) -> dict[str, Any]:
        """Append a complete new version of an existing card."""
        forbidden = (SYSTEM_FIELDS - {"origin"}).intersection(changes)
        if forbidden:
            raise CardError("version changes may not set system field(s): " + ", ".join(sorted(forbidden)))
        updated = dict(card)
        updated.update(changes)
        if role is not None:
            updated["role"] = role
        updated["updated_at"] = self.clock()
        updated["tags"] = extract_tags(
            kind=updated["kind"],
            text=updated["text"],
            title=updated.get("title"),
            patterns=self.patterns,
            name_index=self.name_index,
        )
        updated["tags"] = sorted(set(updated["tags"]) | set((updated.get("origin") or {}).get("file_tags", [])))
        line = json.dumps(updated, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        with self.cards_path.open("a", encoding="utf-8", newline="") as handle:
            handle.write(line)
        self._refresh_index()
        return updated

    def rebuild_index(self) -> dict[str, Any]:
        ids: dict[str, int] = {}
        tags: dict[str, list[str]] = defaultdict(list)
        for line_number, card in _jsonl_cards(self.cards_path):
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
    indexes = {key: value.format(**dirs) for key, value in data.get("indexes", {}).items()}
    # A naming file that names no resident index still has one: the folder's index.md.
    if "residents" not in indexes and paths.get("residents") and not paths["residents"].endswith(".md"):
        indexes["residents"] = paths["residents"] + "/index.md"
    return {"paths": paths, "indexes": indexes}


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


def _pieces_for(logical: str, text: str, path: Path, shapes: dict[str, Any] | None = None) -> list[tuple[int, str]]:
    if logical == "lessons":
        return _segments(text, re.compile(r"(?m)^## \d{4}-\d{2}-\d{2}[^\r\n]*"), logical, path)
    if logical == "city_rules":
        return _segments(text, re.compile(r"(?m)^(?:## [^\r\n]+|- (?! )[^\r\n].*)"), logical, path)
    if logical == "open_threads":
        return _segments(text, re.compile(r"(?m)^- \*\*.+?\*\*[^\r\n]*"), logical, path)
    if logical == "threads_archive":
        # The grammar of tools/threads.py: an entry runs to the next entry or
        # the next heading, so the operator's heading lines are pieces of
        # their own and no entry carries them.
        starts = sorted({0, *(m.start() for m in re.finditer(r"(?m)^(?:- \*\*[^\r\n]*?\*\*[^\r\n]*|## [^\r\n]*)", text))})
        return [(start, text[start:starts[i + 1] if i + 1 < len(starts) else len(text)])
                for i, start in enumerate(starts) if start < len(text)]
    if logical == "goals":
        marker = re.compile(r"(?m)^(?:## [^\r\n]+|- (?! )[^\r\n].*)")
        if text and not marker.search(text):
            return [(0, text)]
        return _segments(text, marker, logical, path)
    if not text:
        if logical in {"journal", "residents"}:
            return [(0, text)]
        raise MigrationFormatError(f"{logical}: empty source file: {path}")
    if logical in {"journal", "residents"}:
        headings = list(re.finditer(r"(?m)^## [^\r\n]*(?:\r?\n|$)", text))
        cuts = {0, *(m.start() for m in headings)}
        if logical == "residents":
            cfg = (shapes or {}).get("residents", {})
            observation = cfg.get("entries_heading")
            entry_pattern = cfg.get("dated_entry")
            if observation and entry_pattern:
                for i, heading in enumerate(headings):
                    if not re.search(observation, heading.group(), re.IGNORECASE):
                        continue
                    end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
                    entries = list(re.finditer(entry_pattern, text[heading.end():end], re.MULTILINE))
                    if entries:
                        # The observations heading stays above its first entry.
                        # If an earlier section exists, its header is that section.
                        if i == 0 and heading.start() > 0:
                            cuts.discard(heading.start())
                        cuts.update(heading.end() + m.start() for m in entries)
        starts = sorted(cuts)
        return [(start, text[start:starts[i + 1] if i + 1 < len(starts) else len(text)])
                for i, start in enumerate(starts) if start < len(text)]
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


def _is_structure(logical: str, rules: list[dict[str, str]], text: str, position: int, segment: str) -> bool:
    """True for a piece that holds the file's shape and no entry of its own.

    Such a piece is still a card, because the cards must rebuild the file
    byte for byte, but it is never shown as a rule or returned by recall.
    """
    if logical == "lessons":
        return not any(line.strip() and not line.startswith("#") for line in segment.splitlines())
    if logical == "city_rules":
        if any(rule.get("skip") and re.search(rule["skip"], segment, re.IGNORECASE) for rule in rules):
            return True
        if not re.search(r"(?m)^## ", text):
            # a rulebook with no sections: only a piece with no bullet is structure
            return not re.search(r"(?m)^- ", segment)
        if re.match(r"#{1,6} ", segment):
            return True
        # bullets above the first section heading explain the file; they are not entries
        return not re.search(r"(?m)^## ", text[:position])
    return False


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
        if _is_structure(logical, [], text, position, segment):
            supplied["status"] = "structure"
        created_at = _lesson_created_at(segment)
    elif logical == "city_rules":
        supplied["kind"] = "rule"
        rules = (section_status or {}).get(logical, [])
        status = _section_status(rules, text, position, segment)
        if _is_structure(logical, rules, text, position, segment):
            status = "structure"
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
    elif logical == "threads_archive":
        # An archived entry is finished from its first cut. A piece that is
        # not an entry holds the file's shape and is never returned.
        entry = re.match(r"- \*\*", segment)
        supplied.update(kind="thread", title=_thread_title(segment) if entry else None,
                        status="done" if entry else "structure")
    elif logical == "goals":
        supplied["kind"] = "goal"
    elif logical == "journal":
        supplied["kind"] = "log"
        wake = _wake_from_path(path)
    else:
        supplied["kind"] = "relationship"
    return supplied, wake, created_at


def _heading_text(segment: str) -> str | None:
    match = re.match(r"## +([^\r\n]*)", segment)
    if not match:
        return None
    heading = match.group(1).strip()
    return heading[next((i for i, ch in enumerate(heading) if ch.isalnum()), len(heading)):]


def _prepare_pieces(logical: str, path: Path, text: str, relative: str,
                    patterns: dict[str, Any]) -> list[tuple[dict[str, Any], str, str | None, dict[str, Any], str | None]]:
    """One preparation path for fresh migration and keyed re-cutting."""
    if logical == "threads":
        return _prepare_threads(path, text, relative, patterns)
    if logical in {"goal_files", "projects"}:
        from tools import goals
        supplied = {"text": text, "title": goals.title_of(text),
                    "kind": "goal" if logical == "goal_files" else "project"}
        origin = {"logical": logical, "file": relative, "position": 0,
                  "order": 1, "key": path.stem, "file_tags": []}
        if logical == "goal_files":
            origin["file_tags"] = ["goal:" + goals.slug(supplied["title"])]
        if logical == "projects":
            parts = goals.project_parts(text)
            if parts["serves"] != "none":
                origin["file_tags"] = ["goal:" + goals.slug(parts["serves"])]
            if path.parent.name == "closed":
                supplied["status"] = "done"
        return [(supplied, "migration", None, origin, "current")]
    shapes = patterns.get("card_shapes", {})
    cfg = shapes.get(logical, {})
    keyed = logical in {"journal", "residents"}
    file_tags: set[str] = set()
    resident_id = None
    handle = path.stem
    first_heading = re.search(r"(?m)^## ", text)
    header = text[:first_heading.start()] if first_heading else text
    if logical == "journal":
        wake = _wake_from_path(path)
        if re.fullmatch(r"w\d+", wake):
            file_tags.add(f"wake:{wake}")
        date = re.search(cfg["file_date"], header, re.MULTILINE | re.IGNORECASE) if cfg.get("file_date") else None
        if date:
            file_tags.add(f"date:{date.group(1)}")
        prefix = wake.upper() if re.fullmatch(r"w\d+", wake) else wake
    elif logical == "residents":
        resident_id = next((m.group(1) for pattern in cfg.get("resident_id", [])
                            if (m := re.search(pattern, header, re.MULTILINE | re.IGNORECASE))), None)
        found = re.search(cfg["handle"], header, re.MULTILINE | re.IGNORECASE) if cfg.get("handle") else None
        if resident_id and found:
            handle = found.group(1).strip()
        file_tags.add(f"name:{handle.lower()}")
        if resident_id:
            file_tags.add(f"resident:{resident_id}")
        prefix = cfg.get("prefix", "").format(id=resident_id or "", handle=handle) if resident_id else cfg.get("prefix_without_id", "").format(handle=handle)
    seen: set[str] = set()
    entry_starts = set()
    if logical == "residents" and cfg.get("entries_heading") and cfg.get("dated_entry"):
        headings = list(re.finditer(r"(?m)^## [^\r\n]*(?:\r?\n|$)", text))
        for i, heading in enumerate(headings):
            if re.search(cfg["entries_heading"], heading.group(), re.IGNORECASE):
                end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
                entry_starts.update(heading.end() + m.start() for m in re.finditer(cfg["dated_entry"], text[heading.end():end], re.MULTILINE))
    counts: dict[str, int] = defaultdict(int)
    prepared = []
    for order, (position, segment) in enumerate(_pieces_for(logical, text, path, shapes), start=1):
        supplied, wake, created_at = _card_spec(logical, path, text, position, segment, patterns.get("section_status"))
        origin = {"logical": logical, "file": relative, "position": position, "order": order}
        role = None
        if keyed:
            heading = _heading_text(segment)
            if logical == "residents" and order == 1 and entry_starts and len(segment) == min(entry_starts):
                heading = None  # The observations heading is part of the header.
            entry = re.match(cfg["dated_entry"], segment) if position in entry_starts else None
            if entry:
                label = entry.group(1)
                base_key = f"entry:{label}"
                role = "history"
                created_at = _first_date(label)
            elif heading is not None:
                label = heading
                raw_heading = re.match(r"## +([^\r\n]*)", segment).group(1).strip()
                base_key = re.sub(r"-+", "-", "".join(ch if ch.isalnum() else "-" for ch in raw_heading.lower()))
                role = "history" if logical == "journal" else "current"
            else:
                label = cfg.get("header_label", "")
                base_key = "header"
                role = "history" if logical == "journal" else "current"
            counts[base_key] += 1
            key = base_key if counts[base_key] == 1 else f"{base_key}-{counts[base_key]}"
            while key in seen:
                counts[base_key] += 1
                key = f"{base_key}-{counts[base_key]}"
            seen.add(key)
            origin.update(key=key, file_tags=sorted(file_tags))
            title = cfg.get("label", "{prefix}{section}").format(prefix=prefix, section=label)
            supplied["title"] = title[:97] + "..." if len(title) > 100 else title
            if logical == "residents" and resident_id is None:
                supplied["status"] = "recheck"
        prepared.append((supplied, wake, created_at, origin, role))
    return prepared


def _prepare_threads(path: Path, text: str, relative: str, patterns: dict[str, Any]):
    from tools.goals import slug, title_of
    cfg = patterns["card_shapes"]["threads"]
    title = title_of(text)
    headings = list(re.finditer(cfg["entries_heading"], text))
    start = headings[-1].end() if headings else len(text)
    entries = list(re.finditer(cfg["dated_entry"], text[start:]))
    cuts = [0] + [start + entry.start() for entry in entries]
    counts = defaultdict(int)
    result = []
    for order, position in enumerate(cuts):
        end = cuts[order + 1] if order + 1 < len(cuts) else len(text)
        segment = text[position:end]
        if order:
            entry = entries[order - 1]
            date, wake = entry.group(1), str(int(entry.group(2)))
            key = f"entry:{date}-{wake}"
            label = f"{date} ({names.wake_id(int(wake))})"
            role = "history"
        else:
            key, label, role = "header", cfg["header_label"], "current"
        counts[key] += 1
        if counts[key] > 1:
            key += f"-{counts[key]}"
        supplied = dict(kind="thread", text=segment,
                        title=cfg["label"].format(title=title, section=label))
        if path.parent.name == "closed":
            supplied["status"] = "done"
        origin = dict(logical="threads", file=relative, position=position, order=order + 1,
                      key=key, file_tags=["thread:" + slug(title)])
        result.append((supplied, "migration", _first_date(label) if order else None, origin, role))
    return result


def _tag_plan(root: Path, prepared) -> None:
    from tools.goals import folders, files, slug, text, title_of
    config_path = root / "config/cards.json"
    if not config_path.is_file():
        return
    pattern = _config(config_path).get("plan_heading")
    if not pattern:
        return
    names = [title_of(text(path)) for folder in folders(root) for path in files(folder)]
    for supplied, _, _, origin, _ in prepared:
        if not re.search(pattern, supplied["text"]):
            continue
        # A literal whole-name search, with Unicode word boundaries. Names
        # never become file-search patterns, and only this plan piece is tagged.
        body = supplied["text"].casefold()
        tags = set(origin.get("file_tags", []))
        for name in names:
            needle = name.casefold()
            cursor = 0
            while (found := body.find(needle, cursor)) >= 0:
                end = found + len(needle)
                if (found == 0 or not (body[found - 1].isalnum() or body[found - 1] == "_")) and (end == len(body) or not (body[end].isalnum() or body[end] == "_")):
                    tags.add("goal:" + slug(name))
                    break
                cursor = end
        origin["file_tags"] = sorted(tags)


def _source_files(root: Path, logical: str, relative: str, excluded: str | None) -> list[Path]:
    target = root / relative
    if target.is_file():
        return [target]
    if not target.is_dir():
        if logical in {"goal_files", "projects", "threads", "open_threads", "threads_archive"}:
            return []
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
            if logical in {"goal_files", "projects", "threads", "open_threads", "threads_archive"}:
                continue
            raise MigrationFormatError(f"{logical}: no source files under {relative}")
        for path in files:
            text = _read_source_text(path)
            relative_path = path.relative_to(root).as_posix()
            prepared = _prepare_pieces(logical, path, text, relative_path, store.patterns)
            if logical == "journal":
                _tag_plan(root, prepared)
            for supplied, wake, created_at, origin, role in prepared:
                card = store.write(supplied, wake=wake, source="self", channel="migration",
                                   origin=origin, created_at=created_at, role=role)
                if logical == "residents" and supplied.get("status") == "recheck":
                    migration_notes.append(f"- {relative_path}: resident ID missing; tagged `name:{path.stem.lower()}` and marked `recheck`.")
                if logical == "lessons" and card["status"] != "structure":
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
    for _, card in _jsonl_cards(Path(cards_path)):
        current[card["id"]] = card
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for card in current.values():
        origin = card.get("origin")
        if origin and card.get("status_reason") not in {"removed from source file", "re-cut into keyed pieces"}:
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


def read_card(memory_root: Path, card_id: str, history: bool = False, *,
              record_read: bool = False, wake: str = "read", conversation: str | None = None,
              session: str = "wake") -> dict[str, Any]:
    memory_root = Path(memory_root)
    versions: list[dict[str, Any]] = []
    cards_path = memory_root / "cards" / "cards.jsonl"
    for _, card in _jsonl_cards(cards_path):
        if card["id"] == card_id:
            versions.append(card)
    result: dict[str, Any] = {"card": versions[-1]} if versions else {"error": "NOT_FOUND", "id": card_id}
    if history and versions:
        result["history"] = versions
    if record_read:
        record_read_result(memory_root, "read_card", result, wake=wake, conversation=conversation,
                           session=session, ids=[card_id] if versions else [], refused=not versions,
                           query={"read_card": card_id, "history": history})
    return result


def _config(path: Path) -> dict[str, Any]:
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    if "recall_tokens" in config:
        raise CardError("recall_tokens is no longer supported; use recall_bytes.")
    return config


def _utf8_bytes(text: str) -> int:
    return len(text.encode("utf-8"))


LEFT_OUT_NAMED = 5


def _left_out_entry(card: dict[str, Any]) -> dict[str, Any]:
    """What a card left out of an answer is: enough to decide whether to read it.

    Logs and lessons carry no title, so the first line of the text stands in.
    """
    title = card.get("title") or ""
    if not title:
        first = next((line for line in card["text"].splitlines() if line.strip()), "")
        title = first.strip().lstrip("#-").strip().strip("*:").strip()
    keyed = (card.get("origin") or {}).get("logical") in {"journal", "residents"} and (card.get("origin") or {}).get("key") is not None
    if not keyed and len(title) > 80:
        title = title[:77].rstrip() + "..."
    return {
        "id": card["id"],
        "kind": card["kind"],
        "title": title,
        "bytes": _utf8_bytes(card["text"]),
        "from": (card.get("origin") or {}).get("file", ""),
        "role": card.get("role", "current"),
    }


def _recall_reference(card: dict[str, Any]) -> dict[str, Any]:
    entry = _left_out_entry(card)
    return {key: entry[key] for key in ("id", "title", "bytes")}


def _recall_answer_size(answer: dict[str, Any]) -> int:
    # FastMCP serializes dictionary tool results with this exact serializer.
    return len(to_json(answer, fallback=str, indent=2))


def linked_labels(returned: list[dict[str, Any]], store: Iterable[dict[str, Any]],
                  weights: dict[str, float], settings: dict[str, Any], *,
                  query_tags: Iterable[str] = (), excluded_ids: Iterable[str] = (),
                  omissions: dict[str, int] | None = None) -> dict[str, list[dict[str, Any]]]:
    """One hop over configured entity tags; no writes or reinforcement.

    Group order is fixed for stage C. Note-like cards with configured place
    or thing tags use those groups, retaining their stored kind.
    Each group gets three slots, within the overall cap (normally twelve).
    """
    if omissions is not None:
        omissions.update(kind_cap=0, total_cap=0, answer_trim=0)
    limit = max(0, int(settings.get("recall_linked_listed", 0)))
    if not limit:
        return {}
    prefixes = tuple(settings.get("link_tag_prefixes", ()))
    tags = {tag for c in returned for tag in c.get("tags", [])
            if tag.startswith(prefixes)} - set(query_tags)
    excluded = set(excluded_ids) | {c["id"] for c in returned}
    live = [c for c in store if c.get("status") == "live"]
    counts: dict[str, int] = defaultdict(int)
    for c in live:
        for tag in set(c.get("tags", [])) & tags:
            counts[tag] += 1
    note_prefixes = tuple(settings.get("link_note_tag_prefixes", ()))
    entity_groups = settings.get("link_entity_group_prefixes", {})
    ranked = []
    for c in live:
        shared = tags & set(c.get("tags", []))
        if c["id"] in excluded or not shared:
            continue
        strength = sum(1 / math.log2(1 + counts[t]) for t in sorted(shared))
        tag = min(shared, key=lambda t: (counts[t], t))
        ranked.append((c, strength, tag, any(t.startswith(note_prefixes) for t in shared)))
    # Stable sorts preserve strength and weight ahead of recency and ID ties.
    ranked.sort(key=lambda item: item[0]["id"])
    ranked.sort(key=lambda item: _card_date(item[0]), reverse=True)
    ranked.sort(key=lambda item: (item[1], float(weights.get(item[0]["id"], item[0].get("weight", 1)))), reverse=True)
    groups = {name: [] for name in ("threads", "places", "things", "notes", "wakes")}
    kind_group = {"thread": "threads", "log": "wakes",
                  "rule": "notes", "reflection": "notes", "decision": "notes"}
    for c, _, tag, is_note in ranked:
        name = kind_group.get(c["kind"], c["kind"])
        if name == "notes":
            for entity_group in ("places", "things"):
                if any(t.startswith(tuple(entity_groups.get(entity_group, ()))) for t in c.get("tags", [])):
                    name = entity_group
                    break
        groups.setdefault(name, []).append((c, tag, is_note))
    result = {}
    note_links = 0
    fixed = ("threads", "places", "things", "notes", "wakes")
    for name in (*fixed, *sorted(set(groups) - set(fixed))):
        kept = 0
        for c, tag, is_note in groups[name]:
            # Apply kind and note-tag caps before the overall label cap.
            if kept >= 3 or (is_note and note_links >= 3):
                if omissions is not None:
                    omissions["kind_cap"] += 1
                continue
            kept += 1
            note_links += is_note
            if not limit:
                if omissions is not None:
                    omissions["total_cap"] += 1
                continue
            result.setdefault(name, []).append(dict(_recall_reference(c), kind=c["kind"], tag=tag))
            limit -= 1
    return result


def _limit_recall_answer(answer: dict[str, Any], budget: int) -> None:
    for field in ("linked", "same_file", "people", "left_out_cards"):
        if _recall_answer_size(answer) <= budget:
            return
        answer.pop(field, None)
    if _recall_answer_size(answer) > budget and answer["left_out"]:
        answer["message"] = (f"{answer['left_out']} matching card(s) left out. "
                             'Read one with read_card(card_id="<id>").')
    if _recall_answer_size(answer) > budget:
        notice = f"Returned cards and answer fields exceed the whole-answer limit of {budget} bytes."
        answer["message"] = (answer.get("message", "") + " " + notice).strip()


_RECALL_KIND_ORDER = {"relationship": 2, "thread": 2, "commitment": 2, "handover": 2, "log": 0}


def recall(
    memory_root: Path,
    *,
    tags: list[str],
    text: str | None = None,
    kinds: list[str] | None = None,
    limit: int = 10,
    config_path: Path,
    wake: str = "read",
    record_read: bool = True,
    history: bool = False,
    conversation: str | None = None,
    session: str = "wake",
) -> dict[str, Any]:
    memory = Path(memory_root)
    store_path = memory / "cards" / "cards.jsonl"
    state: dict[str, dict[str, Any]] = {}
    for _, card in _jsonl_cards(store_path):
        state[card["id"]] = card
    wanted = {tag.lower() for tag in tags}
    needle = text.lower() if text else None
    allowed = set(kinds) if kinds else None
    matches = [
        card for card in state.values()
        if (card.get("status") in {"live", "recheck"} or (history and card.get("status") == "done" and (card["kind"] == "project" or (card["kind"] == "thread" and (card.get("origin") or {}).get("logical") in {"threads", "threads_archive"} and not card.get("status_reason")))))
        and (not wanted or wanted.intersection(card.get("tags", [])))
        and (allowed is None or card["kind"] in allowed)
        and (needle is None or needle in ((card.get("title") or "") + "\n" + card["text"]).lower())
    ]
    # Among equal matches, the card about a person or a thread comes before a
    # log: one long log would otherwise take the whole budget and leave the
    # dossier of the person asked about unread. Logs are ordered by the number
    # in their filename, because re-cutting an old log moves its updated_at.
    matches.sort(
        key=lambda card: (
            len(wanted.intersection(card.get("tags", []))),
            float(card.get("weight", 0)),
            _RECALL_KIND_ORDER.get(card["kind"], 1),
            _log_number(card) if card["kind"] == "log" else 0,
            card.get("updated_at", ""),
        ),
        reverse=True,
    )
    current = [c for c in matches if c.get("role", "current") == "current"]
    historical = [c for c in matches if c.get("role") == "history"]
    historical.sort(key=lambda c: (len(wanted.intersection(c.get("tags", []))),
                                   _log_number(c), _card_date(c), c.get("created_at", "")), reverse=True)
    matches = current + historical
    selected = matches[:max(0, limit)]
    byte_count = sum(_utf8_bytes(card["text"]) for card in selected)
    config = _config(config_path)
    budget = int(config["recall_bytes"])
    history_budget = min(budget, max(0, int(config.get("recall_history_bytes", 6000))))
    current_budget = budget if history or not historical else budget - history_budget
    current_candidates = [c for c in selected if c.get("role", "current") == "current"]
    returned, _ = _fit_cards(current_candidates, current_budget)
    remaining = budget - sum(_utf8_bytes(c["text"]) for c in returned)
    historical_candidates = ([c for c in selected if c.get("role") == "history"] if history
                             else historical[:max(0, limit - len(returned))])
    history_cards, _ = _fit_cards(historical_candidates, remaining if history else min(remaining, history_budget))
    returned += history_cards
    refused = bool(selected) and not returned
    shown = {card["id"] for card in returned}
    missing = [card for card in matches if card["id"] not in shown]
    entries = [{k: v for k, v in _left_out_entry(card).items() if k != "from"} for card in missing]
    kind_counts: dict[str, int] = defaultdict(int)
    role_counts: dict[str, int] = defaultdict(int)
    for entry in entries:
        kind_counts[entry["kind"]] += 1
        role_counts[entry["role"]] += 1
    result = {
        "searched": {"tags": sorted(wanted), "text": text, "kinds": kinds, "limit": limit, "history": history},
        "matched": len(matches),
        "left_out": len(missing),
        "left_out_counts": {"total": len(missing), "by_kind": dict(kind_counts), "by_role": dict(role_counts)},
        "bytes": sum(_utf8_bytes(card["text"]) for card in returned),
        "budget": budget,
        "refused": refused,
        "cards": returned,
    }
    listed = entries[:max(0, int(config.get("recall_left_out_listed", 12)))]
    if listed:
        result["left_out_cards"] = listed
    live = [c for c in state.values() if c.get("status") in {"live", "recheck"}]
    same_file = {}
    for card in returned:
        origin = card.get("origin") or {}
        filename = origin.get("file")
        if not filename or origin.get("logical") not in {"journal", "residents"} or filename in same_file:
            continue
        siblings = sorted((c for c in live if (c.get("origin") or {}).get("file") == filename and c["id"] not in shown),
                          key=lambda c: (c.get("origin") or {}).get("order", 0))
        if siblings:
            same_file[filename] = {"cards": [_recall_reference(c) for c in siblings[:8]], "more": max(0, len(siblings) - 8)}
    if same_file:
        result["same_file"] = same_file
    residents = {tag for tag in wanted if tag.startswith("resident:")}
    people = [_recall_reference(c) for c in live if c["id"] not in shown
              and c.get("role", "current") == "current"
              and residents.intersection(c.get("tags", []))
              and (c.get("origin") or {}).get("logical") == "residents"]
    if people:
        result["people"] = people
    if missing:
        named = "; ".join(
            f"{entry['id']} ({entry['kind']}, {entry['role']}, {entry['bytes']} bytes): {entry['title']}"
            for entry in entries[:LEFT_OUT_NAMED]
        )
        more = len(missing) - LEFT_OUT_NAMED
        result["message"] = (
            f"{len(missing)} matching card(s) left out ({byte_count} bytes asked, {budget} bytes allowed): {named}"
            + (f"; and {more} more" if more > 0 else "")
            + '. Read one with read_card(card_id="<id>").'
        )
    omissions: dict[str, int] = {}
    try:
        linked = linked_labels(returned, state.values(), {}, config, query_tags=wanted,
                               excluded_ids={entry["id"] for entry in listed}, omissions=omissions)
    except Exception:
        # The list is an extra: a fault in it never costs him the cards.
        linked, omissions = {}, {"kind_cap": 0, "total_cap": 0, "answer_trim": 0, "failed": 1}
    if linked:
        result["linked"] = linked
    _limit_recall_answer(result, int(config.get("recall_answer_bytes", 24000)))
    omissions["answer_trim"] = sum(map(len, linked.values())) - sum(map(len, result.get("linked", {}).values()))
    if record_read:
        extra = None
        if int(config.get("recall_linked_listed", 0)) > 0:
            extra = {"offered": [c["id"] for group in result.get("linked", {}).values() for c in group],
                     "omitted": omissions}
        record_read_result(memory, "recall", result, wake=wake, conversation=conversation,
                           session=session, ids=[card["id"] for card in returned],
                           refused=refused, query=result["searched"], extra=extra)
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
    if relative == naming["indexes"].get(logical):
        # An index lists the files; it is not one of them. Cut as a card it
        # carried every name in it and grew by its whole length at each write.
        return {"changed": [], "new": [], "removed": [], "unchanged": []}
    existing = sorted(
        [
            card for card in state.values()
            if (card.get("origin") or {}).get("logical") == logical and (card.get("origin") or {}).get("file") == relative
            # a card already removed from this file is not in it to be removed again
            and card.get("status_reason") != "removed from source file"
        ],
        key=lambda card: card["origin"]["order"],
    )
    source_text = _read_source_text(path) if path.is_file() else ""
    result = {"changed": [], "new": [], "removed": [], "unchanged": []}
    if logical in {"goal_files", "projects"}:
        if not path.is_file():
            for old in existing:
                store.append_version(old, status="done", status_reason="removed from source file")
                result["removed"].append(old["id"])
            return result
        supplied, wake, created_at, origin, role = _prepare_pieces(logical, path, source_text, relative, store.patterns)[0]
        # Closed filenames are independent keys, including legacy slug-only names.
        old = next((c for c in state.values()
                    if (c.get("origin") or {}).get("logical") == logical
                    and (c.get("origin") or {}).get("key") == origin["key"]
                    and (c.get("origin") or {}).get("file") == relative), None)
        if old is None and logical == "projects" and path.parent.name == "closed":
            from tools.goals import slug
            opened = path.parent.parent / (slug(supplied["title"]) + ".md")
            # Transfer this closing's open card; the next opening gets a new card.
            if not opened.exists():
                open_relative = opened.relative_to(root).as_posix()
                old = next((c for c in state.values()
                            if (c.get("origin") or {}).get("logical") == logical
                            and (c.get("origin") or {}).get("file") == open_relative
                            and c.get("status") == "live"
                            and c.get("title") == supplied["title"]), None)
        if old is None:
            card = store.write(supplied, wake=wake, source="self", channel="migration",
                               origin=origin, created_at=created_at, role=role)
            result["new"].append(card["id"])
        else:
            changes = dict(text=supplied["text"], title=supplied["title"],
                           status=supplied.get("status", "live"), origin=origin)
            if old.get("status_reason") == "removed from source file":
                changes["status_reason"] = None
            if all(old.get(k) == v for k, v in changes.items()) and old.get("role") == role:
                result["unchanged"].append(old["id"])
            else:
                store.append_version(old, role=role, **changes)
                result["changed"].append(old["id"])
        return result
    if logical in {"threads", "open_threads", "threads_archive"} and not path.is_file():
        for old in existing:
            store.append_version(old, status="done", status_reason="removed from source file")
            result["removed"].append(old["id"])
        return result
    full_prepared = _prepare_pieces(logical, path, source_text, relative, store.patterns)
    if logical == "journal":
        _tag_plan(root, full_prepared)
    if logical in {"journal", "residents", "threads"}:
        active = [c for c in existing if c.get("status_reason") != "re-cut into keyed pieces"]
        if logical == "threads" and path.parent.name == "closed" and not active:
            from tools.goals import slug, title_of
            opened = path.parent.parent / (slug(title_of(source_text)) + ".md")
            if not opened.exists():
                open_relative = opened.relative_to(root).as_posix()
                active = [c for c in state.values() if (c.get("origin") or {}).get("logical") == logical
                          and (c.get("origin") or {}).get("file") == open_relative
                          and c.get("status") == "live"]
        if any((c.get("origin") or {}).get("key") is None for c in active):
            for old in active:
                store.append_version(old, status="done", status_reason="re-cut into keyed pieces")
                result["removed"].append(old["id"])
            active = []
        keyed = {c["origin"]["key"]: c for c in active}
        # A removed key remains known; its id resumes if the piece returns.
        removed_keys = {
            c["origin"]["key"]: c for c in state.values()
            if (c.get("origin") or {}).get("logical") == logical
            and (c.get("origin") or {}).get("file") == relative
            and (c.get("origin") or {}).get("key") is not None
            and c.get("status_reason") == "removed from source file"
        }
        removed_keys.update(keyed)
        keyed = removed_keys
        for supplied, wake, created_at, origin, role in full_prepared:
            old = keyed.pop(origin["key"], None)
            if old is None:
                card = store.write(supplied, wake=wake, source="self", channel="migration", origin=origin, created_at=created_at, role=role)
                result["new"].append(card["id"])
            else:
                changes = dict(text=supplied["text"], title=supplied.get("title"), status=supplied.get("status", "live"), origin=origin)
                if old.get("status_reason") == "removed from source file":
                    changes["status_reason"] = None
                if all(old.get(k) == v for k, v in changes.items()) and old.get("role") == role:
                    result["unchanged"].append(old["id"])
                else:
                    store.append_version(old, role=role, **changes)
                    result["changed"].append(old["id"])
        for old in keyed.values():
            if old.get("status_reason") == "removed from source file":
                continue
            store.append_version(old, status="done", status_reason="removed from source file")
            result["removed"].append(old["id"])
        return result
    prepared = [item[:4] for item in full_prepared]

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


def recut_all(repo_root: Path, naming_path: Path, patterns_path: Path, logical: str) -> dict[str, int]:
    """Re-cut every configured file of one logical name; append only."""
    if logical not in LOGICAL_NAMES:
        raise CardError(f"invalid logical name: {logical!r}")
    naming = load_naming(naming_path)
    if logical not in naming["paths"]:
        return {"files": 0, "made": 0, "closed": 0, "changed": 0, "left_alone": 0}
    files = _source_files(Path(repo_root), logical, naming["paths"][logical], naming["indexes"].get(logical))
    counts = {"files": 0, "made": 0, "closed": 0, "changed": 0, "left_alone": 0}
    for path in files:
        result = recut_file(repo_root, naming_path, patterns_path, logical, path)
        counts["files"] += 1
        for output, key in (("made", "new"), ("closed", "removed"), ("changed", "changed"), ("left_alone", "unchanged")):
            counts[output] += len(result[key])
    if logical in {"goal_files", "projects", "threads", "open_threads", "threads_archive"}:
        raw = json.loads(Path(naming_path).read_bytes())
        memory = Path(repo_root) / raw.get("dirs", {}).get("memory", "memory")
        store = CardStore(memory, patterns_path)
        missing_files = {(c.get("origin") or {}).get("file") for c in store._current_state().values()
                         if (c.get("origin") or {}).get("logical") == logical
                         and c.get("status_reason") != "removed from source file"
                         and not (Path(repo_root) / c["origin"]["file"]).is_file()}
        for missing in sorted(missing_files):
            result = recut_file(repo_root, naming_path, patterns_path, logical, Path(repo_root) / missing)
            counts["closed"] += len(result["removed"])
    return counts


def _fit_cards(cards: list[dict[str, Any]], budget: int) -> tuple[list[dict[str, Any]], int]:
    chosen: list[dict[str, Any]] = []
    used = 0
    for card in cards:
        cost = _utf8_bytes(card["text"])
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


def _wake_added_section(
    name: str, candidates: list[dict[str, Any]], budget: int, max_cards: int | None = None,
) -> dict[str, Any]:
    """Fit whole entries, including separators and recall guidance in the budget."""
    is_index = name == "Open threads (index)"
    entries = []
    for card in candidates:
        if is_index:
            tags = ", ".join(t for t in card.get("tags", []) if t.startswith(("resident:", "place:", "post:", "name:")))
            entries.append(f"- {card['id']}: {card.get('title') or card['id']} [{tags}]")
        else:
            entries.append(card["text"])

    def render(chosen: list[int]) -> str:
        body = ("\n" if is_index else "\n\n").join(entries[i] for i in chosen) if chosen else "none"
        if is_index:
            body += "\nRecall a thread with read_card(card_id=\"<id>\") or recall(tags=[...])."
        missing = [candidates[i]["id"] for i in range(len(entries)) if i not in chosen]
        if missing and is_index:
            # Name them: an open thread that isn't listed must still be findable.
            body += f"\n\n{len(missing)} more thread(s) not listed: {', '.join(missing)}. Read each with read_card(card_id=\"<id>\")."
        elif missing:
            kind = {"Rules": "rule"}[name]
            body += f'\n\n{len(missing)} more card(s) left out. Use recall(tags=[], kinds=["{kind}"]) with narrower tags.'
        return body

    chosen: list[int] = []
    for i in range(len(entries)):
        if _utf8_bytes(render(chosen + [i])) <= budget:
            chosen.append(i)
    if max_cards is not None:
        chosen = chosen[:max_cards]
    body = render(chosen)
    return {"name": name, "text": body, "bytes": _utf8_bytes(body), "shown": len(chosen), "left_out": len(entries) - len(chosen),
            "ids": [candidates[i]["id"] for i in chosen]}


def _log_number(card: dict[str, Any]) -> int:
    # "SR-0135-2080-01-01.md" is 135. A name with no letter prefix has no number.
    match = re.match(r"[A-Za-z]+-?(\d+)", Path((card.get("origin") or {}).get("file", "")).name)
    return int(match.group(1)) if match else -1


def _card_date(card: dict[str, Any]) -> str:
    key = (card.get("origin") or {}).get("key", "")
    if key.startswith("entry:"):
        return key[6:16]
    filename = (card.get("origin") or {}).get("file", "")
    match = re.search(r"\d{4}-\d{2}-\d{2}(?:-\d{4,6})?", filename)
    return match.group() if match else card.get("created_at", "")


def _plan_section(text: str, plan_heading: str | None = None) -> str:
    """A log's own plan for next time, under the log's title line.

    The section runs from the first heading that says "plan next" to the next
    heading of the same or a higher level. A log with no such heading is
    returned whole.
    """
    match = re.search(plan_heading, text) if plan_heading else None
    if not match:
        return text
    following = re.compile(rf"(?m)^#{{1,{len(match.group(1))}}} ").search(text, match.end())
    section = text[match.start():following.start() if following else len(text)].rstrip()
    title = text.splitlines()[0] if text.startswith("#") and match.start() > 0 else ""
    return f"{title}\n\n{section}" if title else section


def read_wake_card(
    repo_root: Path,
    naming_path: Path,
    *,
    config_path: Path,
    here_tags: list[str] | None = None,
    wake: str = "read",
    conversation: str | None = None,
    record_read: bool = False,
    session: str = "wake",
) -> dict[str, Any]:
    root = Path(repo_root)
    raw_naming = json.loads(Path(naming_path).read_text(encoding="utf-8"))
    dirs = raw_naming.get("dirs", {})
    resolved = {key: value.format(**dirs) for key, value in raw_naming.get("paths", {}).items()}
    memory = root / dirs.get("memory", "memory")
    state: dict[str, dict[str, Any]] = {}
    cards_path = memory / "cards" / "cards.jsonl"
    for _, card in _jsonl_cards(cards_path):
        state[card["id"]] = card
    cards = list(state.values())
    config = _config(config_path)
    cfg = config["wake_sections"]
    plan_heading = config.get("plan_heading")
    identity = _read_source_text(root / resolved["identity"])
    governance = _read_source_text(root / resolved["governance"])
    handovers = [c for c in cards if c["kind"] == "handover" and c["status"] in {"live", "recheck"}]
    handovers.sort(key=lambda c: c["updated_at"], reverse=True)
    doing: list[dict[str, Any]] = handovers[:1]
    log_bundle = None
    # Every card whose text the bundle shows, so the read log credits each.
    bundle_ids: list[str] = []
    due_card = None
    if not doing:
        # The newest log is the one with the highest number in its filename.
        # updated_at moves whenever a log is re-cut, so it can't say which is newest.
        logs = sorted(
            (c for c in cards if c["kind"] == "log" and c["status"] == "live"),
            key=lambda c: (_log_number(c), _card_date(c) if (c.get("origin") or {}).get("key") is not None else c["updated_at"], c["updated_at"]), reverse=True,
        )
        if logs:
            newest = logs[0]
            if (newest.get("origin") or {}).get("key") is not None:
                filename = newest["origin"]["file"]
                pieces = sorted((c for c in logs if c["origin"]["file"] == filename), key=lambda c: c["origin"]["order"])
                plan = next((c for c in pieces if plan_heading and re.search(plan_heading, c["text"])), None)
                first_pattern = config.get("log_first_heading")
                observation_pattern = config.get("log_observations_heading")
                first = next((c for c in pieces if first_pattern and re.match(first_pattern, c["text"])), None)
                if first is not None:
                    selected = [c for c in pieces if c is first or c is plan]
                    observations = next((c for c in pieces if observation_pattern and re.match(observation_pattern, c["text"])), None)
                    if observations is not None and sum(_utf8_bytes(c["text"]) for c in selected + [observations]) <= int(config.get("wake_log_bytes", 5000)):
                        selected = [c for c in pieces if c in selected or c is observations]
                    header = next((c for c in pieces if c["origin"]["key"] == "header"), None)
                    title = header["text"].splitlines()[0] if header and header["text"].splitlines() else ""
                    fallback = dict(first)
                    fallback["text"] = (title + "\n\n" if title else "") + "".join(c["text"] for c in selected)
                    log_bundle = fallback
                    bundle_ids = ([header["id"]] if title else []) + [c["id"] for c in selected]
                    lists = list(re.finditer(r"(?im)^[ \t]*(?:(?:[-+*]|\d+[.)])[ \t]+)?[#*_ \t]*(Due|Waiting|Deferred|Done)\b", first["text"]))
                    due = next((m for m in lists if m.group(1).lower() == "due"), None)
                    if due:
                        end = next((m.start() for m in lists if m.start() > due.start() and m.group(1).lower() != "due"), len(first["text"]))
                        due_card = dict(first, text=first["text"][due.start():end])
                else:
                    fallback = dict(plan or next((c for c in pieces if c["origin"]["key"] == "header"), pieces[0]))
                    fallback["text"] = fallback["title"] + "\n\n" + _plan_section(fallback["text"], plan_heading)
                    if plan is None:
                        fallback["text"] += "\n\n" + "\n".join(c["title"] for c in pieces if c["id"] != fallback["id"])
            else:
                fallback = dict(newest)
                fallback["text"] = _plan_section(fallback["text"], plan_heading)
            doing.append(fallback)
    goal_cards = [c for c in cards if c["kind"] == "goal" and c["status"] == "live"]
    # Legacy goals.md cards retain their cutter and precede the separate files.
    doing.extend(c for c in goal_cards if (c.get("origin") or {}).get("logical") != "goal_files")
    doing.extend(_file_order([c for c in goal_cards if (c.get("origin") or {}).get("logical") == "goal_files"]))
    project_cards = _file_order([c for c in cards if c["kind"] == "project" and c["status"] == "live"])
    doing.extend(project_cards)
    owing = sorted((c for c in cards if c["kind"] == "commitment" and c["status"] == "live"), key=lambda c: c["created_at"])
    if due_card is not None:
        owing.insert(0, due_card)
    from tools import threads as thread_files
    new_threads = thread_files.exists(root)
    if new_threads:
        headers = {c["origin"]["file"]: c for c in cards
                   if (c.get("origin") or {}).get("logical") == "threads"
                   and c.get("origin", {}).get("key") == "header" and c["status"] == "live"}
        threads = []
        for item in thread_files.summaries(root):
            relative = item["path"].relative_to(root).as_posix()
            header = headers.get(relative)
            tags = header.get("tags", []) if header else []
            threads.append(dict(id=relative, kind="thread", title=item["title"], tags=tags,
                                card_id=header["id"] if header else None))
    else:
        threads = _file_order([c for c in cards if c["kind"] == "thread" and c["status"] == "live"])
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
        and c.get("role", "current") == "current"
        and wanted.intersection(c.get("tags", []))
    ]
    groups = [
        ("What I was doing", doing, int(cfg["what_i_was_doing"])),
        ("What I owe", owing, int(cfg["what_i_owe"])),
        ("Open threads (index)", threads, int(cfg["thread_index"])),
        ("Rules", rules, int(cfg["rules"])),
        ("What needs checking", checking, int(cfg["what_needs_checking"])),
        ("What's here", here, int(cfg["whats_here"])),
    ]
    def build_section(
        name: str, candidates: list[dict[str, Any]], budget: int, max_cards: int | None = None,
    ) -> dict[str, Any]:
        if name == "Open threads (index)" and new_threads:
            thread_cfg = thread_files.settings(root)
            entries = [thread_cfg["wake_line"].format(title=c["title"],
                       tags=", ".join(t for t in c["tags"] if t.startswith(("place:", "resident:", "post:", "name:"))))
                       for c in candidates]
            def tail(left):
                return [thread_cfg["wake_omitted"].format(count=left)] if left else []
            body, indexes = thread_files.fit_line_indexes(entries, budget, tail, max_cards)
            return {"name": name, "text": body, "bytes": _utf8_bytes(body),
                    "shown": len(indexes), "left_out": len(entries) - len(indexes),
                    "ids": [candidates[i]["card_id"] or candidates[i]["id"] for i in indexes]}
        if name in {"Open threads (index)", "Rules"}:
            section = _wake_added_section(name, candidates, budget, max_cards)
            if name == "Rules" and any(withheld_rules.values()):
                note = (
                    f"\n\nNot shown here: {withheld_rules['recheck']} rule card(s) waiting to be checked "
                    f"and {withheld_rules['done']} closed. They are not confirmed. "
                    'Find them with recall(tags=[...], kinds=["rule"]) or read_card(card_id="<id>").'
                )
                section["text"] += note
                section["bytes"] = _utf8_bytes(section["text"])
            return section
        if name == "What I was doing" and project_cards:
            from tools.goals import project_parts
            # Reserve every title/next-step pair before choosing any full project.
            def summary(card):
                parts = project_parts(card["text"])
                return "# " + parts["title"] + "\n\n## Next step\n\n" + parts["next_step"]
            projects = [c for c in candidates if c["kind"] == "project"]
            other = [c for c in candidates if c["kind"] != "project"]
            protected = [c for c in other if c is log_bundle]
            summaries = [summary(c) for c in projects]
            reserve = _utf8_bytes("\n\n".join(summaries))
            used_other = list(other)
            def render_other(chosen):
                body = "\n\n".join(c["text"] for c in chosen)
                left = len(other) - len(chosen)
                if left:
                    body += f"\n\n{left} more card(s) left out. Use recall with narrower tags or kinds."
                return body
            # Project summaries are never discarded by section or whole-card fitting.
            allowed = len(other) if max_cards is None else max(0, max_cards - len(projects))
            while used_other and (len(used_other) > allowed or
                    _utf8_bytes(render_other(used_other)) + (2 if used_other else 0) + reserve > budget):
                removable = [c for c in used_other if c not in protected]
                if not removable:
                    break
                used_other.remove(removable[-1])
            body = render_other(used_other)
            if _utf8_bytes(body) + (2 if body else 0) + reserve > budget:
                raise CardError("what_i_was_doing is too small for the newest log and all project titles and next steps.")
            entries = list(summaries)
            if max_cards is None:
                for i, card in enumerate(projects):
                    trial = list(entries)
                    trial[i] = card["text"]
                    candidate = "\n\n".join(([body] if body else []) + trial)
                    if _utf8_bytes(candidate) <= budget:
                        entries = trial
            body = "\n\n".join(([body] if body else []) + entries)
            return {"name": name, "text": body, "bytes": _utf8_bytes(body),
                    "shown": len(used_other) + len(projects), "left_out": len(other) - len(used_other),
                    "ids": [c.get("id") for c in used_other + projects]}
        protected = [c for c in candidates if c is log_bundle or c is due_card]
        if protected:
            rest, _ = _fit_cards([c for c in candidates if c not in protected], max(0, budget - sum(_utf8_bytes(c["text"]) for c in protected)))
            chosen = protected + rest
        else:
            chosen, _ = _fit_cards(candidates, budget)
        if max_cards is not None:
            chosen = chosen[:max(max_cards, len(protected))]

        def render_chosen(chosen):
            left = len(candidates) - len(chosen)
            body = "\n\n".join(card["text"] for card in chosen) if chosen else "none"
            if left:
                body += f"\n\n{left} more card(s) left out. Use recall with narrower tags or kinds."
            return body, left
        # The separators and the notice count against the section's budget too.
        body, left = render_chosen(chosen)
        while _utf8_bytes(body) > budget:
            removable = [c for c in chosen if c not in protected]
            if not removable:
                break
            chosen.remove(removable[-1])
            body, left = render_chosen(chosen)
        return {"name": name, "text": body, "bytes": _utf8_bytes(body), "shown": len(chosen), "left_out": left,
                "ids": [c.get("id") for c in chosen]}

    sections = [{"name": "Who I am", "text": identity + "\n\n" + governance, "bytes": _utf8_bytes(identity + "\n\n" + governance), "shown": 2, "left_out": 0}]
    sections.extend(build_section(*group) for group in groups)

    def render():
        return "\n\n".join(f"## {section['name']}\n\n{section['text']}" for section in sections)

    rendered = render()
    limit = int(config["wake_card_bytes"])
    dropped = 0
    if project_cards and _utf8_bytes(rendered) > limit:
        sections[1] = build_section(*groups[0], max_cards=sections[1]["shown"])
        rendered = render()
    for index in range(len(groups), 0, -1):
        protected_count = sum(c is log_bundle or c is due_card or c["kind"] == "project"
                              for c in groups[index - 1][1])
        while _utf8_bytes(rendered) > limit and sections[index]["shown"] > protected_count:
            sections[index] = build_section(*groups[index - 1], max_cards=sections[index]["shown"] - 1)
            dropped += 1
            rendered = render()
    if _utf8_bytes(rendered) > limit:
        raise CardError("wake_card_bytes is too small for whole identity, governance and section notices.")
    # The text goes out once. Sections carry sizes and counts only, so the card
    # doesn't cost twice its length in the agent's context.
    summary = [{key: value for key, value in section.items() if key not in {"text", "ids"}} for section in sections]
    result = {"text": rendered, "sections": summary, "total_bytes": _utf8_bytes(rendered), "dropped_cards": dropped}
    if record_read:
        shown: list[str] = []
        for section in sections:
            for card_id in section.get("ids", []):
                expanded = bundle_ids if log_bundle is not None and card_id == log_bundle["id"] else [card_id]
                for one in expanded:
                    if one and one not in shown:
                        shown.append(one)
        record_read_result(memory, "read_wake_card", result, wake=wake, conversation=conversation,
                           session=session, ids=shown, query={"wake_card": True})
    return result


def record_read_result(
    memory: Path, tool: str, result: Any, *, wake: str,
    conversation: str | None = None, session: str = "wake",
    ids: list[str] | None = None, refused: bool = False, query: Any = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Meter the exact FastMCP text answer without making reads depend on logging."""
    try:
        text = result if isinstance(result, str) else to_json(result, fallback=str, indent=2).decode("utf-8")
        event = {"time": _utc_now(), "wake": wake, "tool": tool, "bytes": _utf8_bytes(text),
                 "ids": ids or [], "refused": refused, "conversation": conversation, "session": session}
        if query is not None:
            event["query"] = query
        if extra:
            event.update(extra)
        _append_read(memory, event)
    except Exception:
        # Research telemetry must never change or prevent the memory answer.
        pass


def _append_read(memory: Path, event: dict[str, Any]) -> None:
    reads = Path(memory) / "private" / "card-reads.jsonl"
    reads.parent.mkdir(parents=True, exist_ok=True)
    with reads.open("a", encoding="utf-8", newline="") as handle:
        handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")


def record_wake_closed(memory_root: Path, wake: str, conversation: str | None, session: str = "wake") -> None:
    """Mark a conversation as a real wake: its mission log was written.

    Sleep counts reads only from conversations marked here, so read-only and
    test sessions never make a card look read.
    """
    _append_read(Path(memory_root), {"time": _utc_now(), "wake": wake, "closed": True, "conversation": conversation,
                                     "session": session})


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Re-cut configured source files into memory cards.")
    parser.add_argument("logical", choices=LOGICAL_NAMES)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--naming", type=Path, default=Path("naming.json"))
    parser.add_argument("--patterns", type=Path, default=Path("config/tag-patterns.city.json"))
    args = parser.parse_args()
    counts = recut_all(args.repo_root, args.repo_root / args.naming, args.repo_root / args.patterns, args.logical)
    print(f"Files read: {counts['files']}; cards made: {counts['made']}; closed: {counts['closed']}; changed: {counts['changed']}; left alone: {counts['left_alone']}.")


if __name__ == "__main__":
    main()
