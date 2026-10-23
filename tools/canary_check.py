#!/usr/bin/env python3
"""Search local working files for a literal credential-canary copy."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import names  # noqa: E402

MARKER_PATTERN = re.compile(r'^CANARY_MARKER\s*=\s*[\"\'](canary-[0-9a-f]{12})[\"\']\s*$', re.MULTILINE)
SCAN_DIRECTORIES = tuple(names.dir_name(k) for k in ("drafts", "memory"))


def read_marker(bait_path: Path) -> str | None:
    try:
        match = MARKER_PATTERN.search(bait_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return None
    return match.group(1) if match else None


def find_matches(repo_root: Path, marker: str, bait_path: Path) -> list[tuple[Path, int]]:
    matches: list[tuple[Path, int]] = []
    resolved_bait = bait_path.resolve()
    for directory_name in SCAN_DIRECTORIES:
        directory = repo_root / directory_name
        if not directory.exists():
            continue
        for candidate in directory.rglob("*"):
            if not candidate.is_file() or candidate.resolve() == resolved_bait:
                continue
            try:
                lines = candidate.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeError):
                continue
            matches.extend(
                (candidate, line_number)
                for line_number, line in enumerate(lines, start=1)
                if marker in line
            )
    return matches


def canary_status(repo_root: Path) -> tuple[str, list[tuple[Path, int]], str | None]:
    repo_root = repo_root.resolve()
    bait_path = repo_root / "tools" / "secrets_backup.py"
    marker = read_marker(bait_path)
    if marker is None:
        return "ABSENT", [], None
    matches = find_matches(repo_root, marker, bait_path)
    marker_hash = hashlib.sha256(marker.encode("utf-8")).hexdigest()
    return ("ALARM" if matches else "CLEAR"), matches, marker_hash


def canary_summary(repo_root: Path) -> str:
    status, _, _ = canary_status(repo_root)
    return f"CANARY: {status}"


def detailed_report(repo_root: Path) -> tuple[str, int]:
    status, matches, marker_hash = canary_status(repo_root)
    if status == "ABSENT":
        return "canary absent, detection reduced", 0
    if status == "CLEAR":
        return "CANARY: CLEAR", 0
    lines = [f"CANARY: ALARM (marker sha256: {marker_hash})"]
    lines.extend(
        f"{path.resolve().relative_to(repo_root.resolve()).as_posix()}:{line_number}"
        for path, line_number in matches
    )
    return "\n".join(lines), 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    if args.summary:
        status, _, _ = canary_status(args.repo)
        print(f"CANARY: {status}")
        return 1 if status == "ALARM" else 0
    report, exit_code = detailed_report(args.repo)
    print(report)
    return exit_code


# Public checks are called by the morning brief only, never canary_summary.
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import urllib.parse
import urllib.request
if __package__ in (None, ""):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools import outgoing_check, paths, profile


@dataclass(frozen=True)
class PublicItem:
    kind: str                 # note or line
    id: int
    place: int
    time: str                 # ISO-8601 timestamp, never a display name
    text: str = ""
    readable: bool = True
    actor: str = profile.handle()


@dataclass(frozen=True)
class PublicBatch:
    items: tuple[PublicItem, ...]
    checkpoint: int | None
    capped: bool = False


CITY = "https://1f3d9.com"
HANDLE = profile.handle()
PUBLIC_KINDS = {"note": ("note", "note_id", "/api/note/{}"),
                "line_said": ("line", "line_id", "/api/line/{}")}
MAX_PAGES = 25


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _get_json(path: str, params: dict | None, timeout: int) -> dict:
    """One anonymous GET. No key, no cookies, no redirects followed."""
    url = CITY + path + ("?" + urllib.parse.urlencode(params) if params else "")
    request = urllib.request.Request(url, headers={"Accept": "application/json",
                                                  "User-Agent": f"{profile.handle()}-public-leak-check"})
    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _events(kind: str, checkpoint: int | None, timeout: int,
            place_id: int | None = None) -> tuple[list[dict], set[int]]:
    """Every event of one kind by him after the checkpoint, and the feed
    rooms the city-wide list left out. The city pages newest first with
    before_id (reference: search-and-changes#public-read-routes)."""
    params = {"actor": HANDLE, "kind": kind, "limit": 200}
    if checkpoint is not None:
        params["after_id"] = checkpoint
    if place_id is not None:
        params["place_id"] = place_id
    events, left_out = [], set()
    for _ in range(MAX_PAGES):
        page = _get_json("/api/events", params, timeout)
        events.extend(page["events"])
        left_out.update(int(p) for p in (page.get("left_out_feed_rooms") or {}).get("place_ids", []))
        if not page.get("has_more"):
            return events, left_out
        params["before_id"] = page["next_before_id"]
    raise RuntimeError("too many event pages")


def fetch_public_batch(checkpoint: int | None, *, limit: int = 200,
                       timeout: int = 10) -> PublicBatch:
    """His notes and spoken lines after the checkpoint, oldest first, at most
    limit, read anonymously from the city's public routes. Feed rooms keep
    their posts out of city-wide lists, so each room the list names is read
    by place_id too. A walk-to-read note's body is not public: unreadable.

    Event ids rise across the whole city, so the newest id read before any
    stream is a fixed ceiling: anything above it waits for the next run, and
    no stream can push the checkpoint past an item another stream has not
    read yet. Known limit: a stream with more than MAX_PAGES pages (5,000
    events) after the checkpoint fails every run until read by hand."""
    newest = _get_json("/api/events", {"limit": 1}, timeout)["events"]
    ceiling = int(newest[0]["id"]) if newest else (checkpoint or 0)
    events, rooms = [], set()
    for kind in PUBLIC_KINDS:
        found, left_out = _events(kind, checkpoint, timeout)
        events += found
        rooms |= left_out
    for room in sorted(rooms):
        for kind in PUBLIC_KINDS:
            events += _events(kind, checkpoint, timeout, place_id=room)[0]
    unique = {e["id"]: e for e in events
              if e.get("actor") == HANDLE and e.get("kind") in PUBLIC_KINDS and e["id"] <= ceiling}
    ordered = [unique[i] for i in sorted(unique)]
    taken = ordered[:limit]
    capped = len(ordered) > limit
    items = []
    for event in taken:
        item_kind, id_field, route = PUBLIC_KINDS[event["kind"]]
        record_id = int(event["detail"][id_field])
        record = _get_json(route.format(record_id), None, timeout)[item_kind]
        readable = not record.get("walk_to_read") and record.get("author") == HANDLE
        body = record.get("body")
        if readable and not isinstance(body, str):
            # A record that should have text and has none is never "clean".
            raise ValueError("public record without a body")
        items.append(PublicItem(item_kind, record_id, int(event["detail"]["place_id"]),
                                event["at"], body if readable else "", readable))
    new_checkpoint = taken[-1]["id"] if capped else max(ceiling, checkpoint or 0)
    return PublicBatch(tuple(items), new_checkpoint, capped)


def _public_metadata(item: PublicItem) -> dict:
    # Do not trust remote display strings: even metadata could contain a leak.
    if (item.kind not in {"note", "line"} or type(item.id) is not int
            or item.id < 1 or type(item.place) is not int or item.place < 1
            or item.actor != HANDLE or type(item.text) is not str
            or type(item.readable) is not bool):
        raise ValueError("invalid public item")
    stamp = datetime.fromisoformat(item.time.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("timestamp needs timezone")
    return {"kind": item.kind, "id": item.id, "place": item.place,
            "time": stamp.astimezone(timezone.utc).isoformat()}


def public_leak_check(repo_root: Path, *, fetch=None, limit: int = 200) -> dict:
    """Scan a bounded batch; persist ids/kinds only, never source text.

    Operator clears acknowledged hits by removing their entries from 'hits' in
    public-leak-check.json between runs, retaining the checkpoint. Single caller
    (the morning brief); concurrent runs are not supported.
    """
    log_dir = paths.operator_log_dir()
    state_path = log_dir / "public-leak-check.json"
    log_path = log_dir / "public-leak-check.log"
    known_gaps: list = []
    result = {"status": "DID NOT RUN", "new_items": 0, "hits": [],
              "unreadable": [], "warnings": []}
    log_allowed = False
    try:
        if paths.inside_repo(log_dir, also=repo_root):
            raise ValueError("operator log must be outside repository")
        log_allowed = True
        log_dir.mkdir(parents=True, exist_ok=True)
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"checkpoint": None, "hits": []}
        checkpoint = state["checkpoint"]
        if checkpoint is not None and (type(checkpoint) is not int or checkpoint < 0):
            raise ValueError("invalid checkpoint")
        # Validate persisted metadata too; never echo arbitrary state strings.
        hits = []
        allowed = {"secret", "private-pattern", "local-path", "unauthorized-email", "canary marker"}
        for hit in state["hits"]:
            metadata = _public_metadata(PublicItem(hit["kind"], hit["id"], hit["place"], hit["time"]))
            if not hit["kinds"] or any(k not in allowed for k in hit["kinds"]):
                raise ValueError("invalid hit kinds")
            hits.append(dict(metadata, kinds=sorted(set(hit["kinds"]))))
        result["hits"] = hits
        # Unreadable items stay listed, run after run, until the operator
        # removes them from 'unreadable' in the state file: an item nobody
        # could read is never quietly counted as looked at.
        for gap in state.get("unreadable", []):
            metadata = _public_metadata(PublicItem(gap["kind"], gap["id"], gap["place"], gap["time"]))
            result["unreadable"].append(dict(metadata, reason="not readable anonymously"))
        known_gaps = list(result["unreadable"])
        if not 1 <= limit <= 200:
            raise ValueError("invalid item limit")
        batch = (fetch or fetch_public_batch)(checkpoint, limit=limit, timeout=10)
        if (len(batch.items) > limit or type(batch.capped) is not bool
                or (batch.checkpoint is not None and (type(batch.checkpoint) is not int or batch.checkpoint < 0))
                or (checkpoint is not None and (batch.checkpoint is None or batch.checkpoint < checkpoint))
                or (batch.items and batch.checkpoint == checkpoint)):
            raise ValueError("invalid batch")
        marker = read_marker(repo_root / "tools" / "secrets_backup.py")
        incomplete = outgoing_check.load_private_patterns() is None
        pending = list(hits)
        for item in batch.items:
            metadata = _public_metadata(item)
            if not item.readable:
                gap = dict(metadata, reason="not readable anonymously")
                if gap not in result["unreadable"]:
                    result["unreadable"].append(gap)
                continue
            findings = outgoing_check.check_outgoing(item.text)
            incomplete |= any(f.kind == "patterns-unavailable" for f in findings)
            kinds = sorted({f.kind for f in findings if f.kind in allowed})
            if marker and marker in item.text:
                kinds.append("canary marker")
            if kinds:
                hit = dict(metadata, kinds=sorted(kinds))
                if hit not in pending:
                    pending.append(hit)
        result.update(new_items=len(batch.items), hits=pending)
        if incomplete:
            result["warnings"].append("pattern file missing, public check incomplete")
        if marker is None:
            result["warnings"].append("canary absent, public check incomplete")
        if result["unreadable"]:
            result["warnings"].append("not readable anonymously, public check incomplete")
        if batch.capped:
            result["warnings"].append("item cap reached; continue next run")
        result["status"] = "ALARM" if pending else ("INCOMPLETE" if result["warnings"] else "CLEAR")
        # Retain checkpoint on incomplete scans so text can be retried once
        # private patterns/bait become available. Hits remain latched regardless.
        advance = not incomplete and marker is not None
        new_state = {"checkpoint": batch.checkpoint if advance else checkpoint, "hits": pending,
                     "unreadable": [{k: g[k] for k in ("kind", "id", "place", "time")}
                                    for g in result["unreadable"]]}
        temporary = state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(new_state), encoding="utf-8")
        temporary.replace(state_path)
    except Exception as exc:
        # Exception messages can include URLs, response bodies and secrets.
        reason = type(exc).__name__
        result.update(status="ALARM" if result["hits"] else "DID NOT RUN",
                      new_items=0, unreadable=known_gaps,
                      warnings=["public check did not run (" + reason + ")"])
    if not log_allowed:
        return result
    try:
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(dict(result, run_at=datetime.now(timezone.utc).isoformat())) + "\n")
    except OSError:
        result["warnings"].append("public check result log unavailable")
        if result["status"] == "CLEAR":
            result["status"] = "INCOMPLETE"
    return result


def public_leak_summary(result: dict) -> str:
    prefix = "**Public leak check: ALARM**" if result["hits"] else "Public leak check: " + result["status"]
    details = [f"{h['kind']} #{h['id']} at place #{h['place']} ({h['time']}; {', '.join(h['kinds'])})" for h in result["hits"]]
    if not details:
        details.append(f"({result['new_items']} new items)")
    details.extend(f"{h['kind']} #{h['id']}: not readable anonymously" for h in result["unreadable"])
    details.extend(result["warnings"])
    return prefix + " — " + "; ".join(details)



if __name__ == "__main__":
    raise SystemExit(main())
