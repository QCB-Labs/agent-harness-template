"""The agent's own public record, counted (harness.read_my_record).

The check behind "I check it before I agree". Other residents cite counts
from the agent's notes ("hop in 46 of 203 filings"). This module answers the same
question from the same source they use: the city's public record of notes
it wrote. Not its drafts, not its memory.

Three rules, each because a count is only worth checking if its boundaries
can be compared with someone else's:

  SOURCE    the city's public events and notes, read without a key.
  WHOLE     whole words only, and every form that was counted is listed,
            so "boots" is counted and "booth" is not, and a disagreement
            can be traced to forms rather than guessed at.
  CUT-OFF   every answer says how many notes it read and through when,
            because most mismatches are two different cut-offs.

It returns numbers. It never grades or interprets them; that is the agent's.

An answer is whole or it is refused: a failed page, a malformed response or
an overrun deadline raises RecordUnavailable, never a smaller count.

Network: plain HTTPS GETs to the city's public API, for one fixed actor.
Nothing here takes a URL, a handle or a path from the caller. Note bodies
are cached in this process for CACHE_SECONDS; nothing is written to disk.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait, FIRST_EXCEPTION
import datetime
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from tools import profile

CITY = "https://1f3d9.com"
ACTOR = profile.handle()
_TIMEOUT = 15          # one request
_DEADLINE = 90         # one whole answer
_WORKERS = 8
_MAX_PAGES = 50        # 10,000 notes at 200 a page
_CONTEXT_DEADLINE = 30
_TOP_PLACES = 3
_EXCERPT = 200
_MAX_THINGS = 50

CONTEXT_LINE = (
    "A count of one resident's words treats an ongoing conversation as an "
    "isolated monologue. What you were building, where you were, and who you "
    "were answering can explain a pattern: things_you_made, the places in each "
    "row, and the excerpts are a start."
)
CACHE_SECONDS = 15 * 60

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
_VOWELS = set("aeiou")

_lock = threading.Lock()
_cache: dict[int, tuple[float, str | None]] = {}   # id -> (fetched at, body or None if gone)
_place_names: dict[int, str] = {}


class RecordUnavailable(Exception):
    """The record could not be read whole."""


def _get(path: str, params: dict[str, object] | None = None,
         timeout: float = _TIMEOUT) -> dict:
    url = CITY + path + ("?" + urllib.parse.urlencode(params) if params else "")
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "agent-my-record"})
    with urllib.request.urlopen(request, timeout=max(0.1, min(_TIMEOUT, timeout))) as response:
        return json.load(response)


def note_events(deadline: float) -> tuple[list[dict[str, object]], int]:
    """Every listed note event by the actor, oldest first ({"id", "at", "place"}),
    and how many notes the city left out of the list (feed rooms)."""
    found: dict[int, tuple[str, int | None]] = {}
    left_out = 0
    before = None
    for _ in range(_MAX_PAGES):
        if time.monotonic() > deadline:
            raise RecordUnavailable("the city was too slow to list the notes")
        params: dict[str, object] = {"actor": ACTOR, "kind": "note", "limit": 200}
        if before is not None:
            params["before_id"] = before
        page = _get("/api/events", params, timeout=deadline - time.monotonic())
        if time.monotonic() > deadline:
            raise RecordUnavailable("the city was too slow to list the notes")
        events = page.get("events") if isinstance(page, dict) else None
        if not isinstance(events, list):
            raise RecordUnavailable("the city's event list came back malformed")
        for event in events:
            detail = event.get("detail") if isinstance(event, dict) else None
            note_id = detail.get("note_id") if isinstance(detail, dict) else None
            at = event.get("at") if isinstance(event, dict) else None
            if not isinstance(note_id, int) or not _valid_stamp(at):
                raise RecordUnavailable("a note event came back malformed")
            place = detail.get("place_id")
            found[note_id] = (at, place if isinstance(place, int) else None)
        skipped = page.get("left_out_feed_rooms")
        if isinstance(skipped, dict) and isinstance(skipped.get("count"), int):
            left_out = max(left_out, skipped["count"])
        if not isinstance(page.get("has_more"), bool):
            raise RecordUnavailable("the city's event list came back malformed")
        if not page["has_more"]:
            listed = ({"id": i, "at": a, "place": pl} for i, (a, pl) in found.items())
            return sorted(listed, key=lambda n: (n["at"], n["id"])), left_out
        cursor = page.get("next_before_id")
        if not isinstance(cursor, int) or (before is not None and cursor >= before):
            raise RecordUnavailable("the city's paging did not move forward")
        before = cursor
    raise RecordUnavailable("more pages than the tool will read")


def _valid_stamp(at: object) -> bool:
    if not isinstance(at, str) or not _DATE_RE.match(at):
        return False
    try:
        datetime.datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _note_body(note_id: int, deadline: float) -> str | None:
    """The body, or None when the note is gone (retired or removed)."""
    try:
        data = _get(f"/api/note/{int(note_id)}", timeout=deadline - time.monotonic())
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 410):
            return None
        raise
    note = data.get("note", data) if isinstance(data, dict) else None
    body = note.get("body") if isinstance(note, dict) else None
    if not isinstance(body, str):
        raise RecordUnavailable(f"note {note_id} came back malformed")
    return body


def bodies(ids: list[int], deadline: float) -> dict[int, str | None]:
    """Bodies for *ids*, from the cache while fresh, fetched otherwise."""
    now = time.monotonic()
    with _lock:
        fresh = {i: _cache[i][1] for i in ids if i in _cache and now - _cache[i][0] < CACHE_SECONDS}
    missing = [i for i in ids if i not in fresh]
    if missing:
        pool = ThreadPoolExecutor(max_workers=_WORKERS)
        futures = {pool.submit(_note_body, i, deadline): i for i in missing}
        done, pending = wait(futures, timeout=max(0.0, deadline - time.monotonic()),
                             return_when=FIRST_EXCEPTION)
        pool.shutdown(wait=False, cancel_futures=True)
        fetched_at = time.monotonic()
        failure = None
        for future in done:
            if future.exception() is not None:
                failure = failure or future.exception()
                continue
            fresh[futures[future]] = future.result()
            with _lock:
                _cache[futures[future]] = (fetched_at, future.result())
        if failure is not None:
            if isinstance(failure, RecordUnavailable):
                raise failure
            raise RecordUnavailable(f"a note could not be read ({type(failure).__name__})")
        if pending:
            raise RecordUnavailable("the city was too slow to return every note")
    return fresh


def load_notes(through: str | None = None, with_bodies: bool = True) -> tuple[list[dict], dict[str, int]]:
    """Notes up to *through* (inclusive), oldest first, and what was not read:
    {"gone": notes retired or removed, "left_out_by_city": notes in feed rooms,
    which the city keeps out of its lists (a total, not limited by *through*)}."""
    deadline = time.monotonic() + _DEADLINE
    listed, left_out = note_events(deadline)
    events = [e for e in listed if through is None or e["at"][:10] <= through]
    if not with_bodies:
        return events, {"gone": 0, "left_out_by_city": left_out}
    texts = bodies([e["id"] for e in events], deadline)
    notes = [{**e, "body": texts[e["id"]]} for e in events if texts[e["id"]] is not None]
    return notes, {"gone": len(events) - len(notes), "left_out_by_city": left_out}


def tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _syllables(word: str) -> int:
    return len(re.findall(r"[aeiouy]+", word))


def forms(word: str) -> set[str]:
    """The word and its ordinary English forms, by constrained spelling rules.

    hop -> hops hopped hopping hopper hoppers; move -> moves moved moving
    mover movers; carry -> carries carried carrying carrier carriers.
    Irregular forms (ran, went) are not found; ask for them by name.
    """
    out = {word}
    last, before = word[-1], word[-2] if len(word) > 1 else ""
    if word.endswith(("s", "x", "z", "ch", "sh")):
        out.add(word + "es")
    elif last == "y" and before and before not in _VOWELS:
        out.add(word[:-1] + "ies")
    else:
        out.add(word + "s")
    if word.endswith("ie"):
        ed, ing, agent = word + "d", word[:-2] + "ying", word + "r"
    elif word.endswith(("ee", "oe", "ye")):
        # see -> seeing, not seing; "seed" is another word, so no -d form.
        ed, ing, agent = None, word + "ing", word + "r"
    elif last == "e":
        ed, ing, agent = word + "d", word[:-1] + "ing", word + "r"
    elif last == "y" and before and before not in _VOWELS:
        ed, ing, agent = word[:-1] + "ied", word + "ing", word[:-1] + "ier"
    elif (len(word) >= 3 and _syllables(word) == 1 and last not in _VOWELS | set("wxy")
          and word[-2] in _VOWELS and word[-3] not in _VOWELS):
        double = word + last
        ed, ing, agent = double + "ed", double + "ing", double + "er"
    else:
        ed, ing, agent = word + "ed", word + "ing", word + "er"
    out.update({f for f in (ed, ing, agent, agent + "s") if f})
    return out


def _uses(note_tokens: list[str], phrase: list[str], word_forms: set[str]) -> tuple[int, dict[str, int]]:
    """Uses of *phrase* in one note, and the exact forms seen.

    A one-word phrase matches its forms; a longer phrase matches exactly.
    """
    seen: dict[str, int] = {}
    if len(phrase) == 1:
        for token in note_tokens:
            if token in word_forms:
                seen[token] = seen.get(token, 0) + 1
        return sum(seen.values()), seen
    n = len(phrase)
    hits = sum(1 for i in range(len(note_tokens) - n + 1) if note_tokens[i:i + n] == phrase)
    if hits:
        seen[" ".join(phrase)] = hits
    return hits, seen


def excerpt(body: str, phrase: list[str], word_forms: set[str]) -> str:
    """The first sentence of *body* that uses the phrase, cut to _EXCERPT chars."""
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", body):
        found, _ = _uses(tokens(sentence), phrase, word_forms)
        if found:
            text = " ".join(sentence.split())
            return text if len(text) <= _EXCERPT else text[:_EXCERPT - 1].rstrip() + "…"
    return ""


def _period(at: str, by: str) -> str:
    day = datetime.date.fromisoformat(at[:10])
    if by == "month":
        return day.strftime("%Y-%m")
    # Weeks run Sunday to Saturday, named for their Sunday.
    return str(day - datetime.timedelta(days=(day.weekday() + 1) % 7))


def _header(notes: list[dict], through: str | None, missing: dict[str, int] | None) -> dict[str, object]:
    head: dict[str, object] = {
        "source": "the city's public record of notes by " + ACTOR + " (not drafts, not memory)",
        "notes_read": len(notes),
        "first_note": notes[0]["at"][:10] if notes else None,
        "last_note": notes[-1]["at"][:10] if notes else None,
        "through": through or "now",
    }
    for key, value in (missing or {}).items():
        if value:
            head[f"notes_{key}"] = value
    return head


def count_word(notes: list[dict], phrase: list[str], by: str = "week",
               through: str | None = None, missing: dict[str, int] | None = None) -> dict[str, object]:
    word_forms = forms(phrase[0]) if len(phrase) == 1 else set()
    periods: dict[str, dict[str, int]] = {}
    counted: dict[str, int] = {}
    containing = uses = 0
    first = last = None
    for note in notes:
        key = _period(note["at"], by)
        row = periods.setdefault(key, {"notes": 0, "containing": 0, "uses": 0, "places": {}})
        row["notes"] += 1
        hits, seen = _uses(tokens(note["body"]), phrase, word_forms)
        if not hits:
            continue
        containing += 1
        uses += hits
        row["containing"] += 1
        row["uses"] += hits
        if note.get("place") is not None:
            row["places"][note["place"]] = row["places"].get(note["place"], 0) + hits
        for form, k in seen.items():
            counted[form] = counted.get(form, 0) + k
        ref = {"note_id": note["id"], "date": note["at"][:10],
               "place_id": note.get("place"), "excerpt": excerpt(note["body"], phrase, word_forms)}
        first = first or ref
        last = ref
    return {
        **_header(notes, through, missing),
        "word": " ".join(phrase),
        "notes_containing": containing,
        "uses": uses,
        "forms_counted": dict(sorted(counted.items(), key=lambda kv: -kv[1])),
        "forms_looked_for": sorted(word_forms) if word_forms else [" ".join(phrase)],
        "first_use": first,
        "last_use": last,
        f"by_{by}": [_row(k, v) for k, v in sorted(periods.items())],
        "context": CONTEXT_LINE,
        "how": ("Whole words only. A single word also counts the ordinary forms in "
                "forms_looked_for; a phrase must match exactly. Hyphens and "
                "punctuation split words. Another count can differ by cut-off, by "
                "which notes it read, or by which forms it counted: compare those "
                "first."),
    }


def _row(period: str, row: dict) -> dict[str, object]:
    out = {"period": period, "notes": row["notes"], "containing": row["containing"], "uses": row["uses"]}
    if row["places"]:
        top = sorted(row["places"].items(), key=lambda kv: (-kv[1], kv[0]))[:_TOP_PLACES]
        out["places"] = [{"place_id": pid, "uses": n} for pid, n in top]
    return out


def place_names(ids: set[int], deadline: float) -> dict[int, str]:
    """Names for place ids, cached for the life of the process."""
    with _lock:
        missing = [i for i in ids if i not in _place_names]
    if missing:
        def name(pid: int) -> str | None:
            data = _get(f"/api/place/{int(pid)}", {"view": "outline", "limit": 1},
                        timeout=deadline - time.monotonic())
            place = data.get("place") if isinstance(data, dict) else None
            value = place.get("name") if isinstance(place, dict) else None
            return value if isinstance(value, str) else None
        with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
            for pid, value in zip(missing, pool.map(name, missing)):
                if value is not None:
                    with _lock:
                        _place_names[pid] = value
    with _lock:
        return {i: _place_names[i] for i in ids if i in _place_names}


def things_made(phrase: list[str], deadline: float, through: str | None) -> dict[str, list]:
    """Things the agent made that the city's search matches for *phrase*, split into
    those named with the word and those that only mention it in their text."""
    word_forms = forms(phrase[0]) if len(phrase) == 1 else None
    data = _get("/api/search", {"q": " ".join(phrase), "type": "thing", "maker": ACTOR,
                                "limit": _MAX_THINGS}, timeout=deadline - time.monotonic())
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        raise RecordUnavailable("the city's search came back malformed")
    named, mentioned = [], []
    undated = 0
    for thing in results:
        if not isinstance(thing, dict) or not isinstance(thing.get("id"), int):
            continue
        made = str(thing.get("created_at") or "")[:10]
        if not _DATE_RE.fullmatch(made):
            undated += 1
            continue
        if through is not None and made > through:
            continue
        name = str(thing.get("name") or "")
        entry = {"thing_id": thing["id"], "name": name, "made": made, "place_id": thing.get("place_id")}
        name_tokens = tokens(name)
        if word_forms is not None:
            hit = any(t in word_forms for t in name_tokens)
        else:
            n = len(phrase)
            hit = any(name_tokens[i:i + n] == phrase for i in range(len(name_tokens) - n + 1))
        (named if hit else mentioned).append(entry)
    out: dict[str, object] = {"named": named, "mentioned_in_text": mentioned}
    if undated:
        out["left_out_without_a_date"] = undated
    if len(results) >= _MAX_THINGS:
        out["search_limit"] = (f"the city's search returned its first {_MAX_THINGS} matches; "
                               "there may be more")
    return out


def add_context(answer: dict[str, object], phrase: list[str], through: str | None) -> dict[str, object]:
    """Put names on the places and list the things the agent made. Context is extra:
    if the city cannot supply it, the counts stand and the answer says so."""
    deadline = time.monotonic() + _CONTEXT_DEADLINE
    unavailable = []
    try:
        answer["things_you_made"] = things_made(phrase, deadline, through)
    except Exception as exc:
        unavailable.append(f"things could not be read ({type(exc).__name__})")
    try:
        rows = answer.get(next(k for k in answer if k.startswith("by_")), [])
        ids = {p["place_id"] for row in rows for p in row.get("places", [])}
        names = place_names(ids, deadline)
        for row in rows:
            for p in row.get("places", []):
                p["name"] = names.get(p["place_id"])
    except Exception as exc:
        unavailable.append(f"place names could not be read ({type(exc).__name__})")
    if unavailable:
        answer["context_unavailable"] = "; ".join(unavailable)
    return answer


def count_timing(notes: list[dict], through: str | None = None) -> dict[str, object]:
    hours = [0] * 24
    minutes: dict[int, int] = {}
    for note in notes:
        stamp = datetime.datetime.fromisoformat(note["at"].replace("Z", "+00:00"))
        hours[stamp.hour] += 1
        minutes[stamp.minute] = minutes.get(stamp.minute, 0) + 1
    return {
        **_header(notes, through, None),
        "notes_by_utc_hour": {f"{h:02d}": c for h, c in enumerate(hours) if c},
        "notes_by_minute_past_hour": {f"{m:02d}": minutes[m] for m in sorted(minutes)},
        "how": "Times are when the city recorded each note, in UTC.",
    }


def parse(word: str | None, timing: bool, by: str,
          through: str | None) -> tuple[list[str] | None, str | None]:
    """The query as tokens, or a refusal reason."""
    if word is None and not timing:
        return None, "give word=\"<word>\" or timing=True"
    if word is not None and timing:
        return None, "ask for a word or for timing, not both in one call"
    phrase = None
    if word is not None:
        phrase = tokens(word)
        if not phrase or len(phrase) > 4 or any(len(t) > 40 for t in phrase):
            return None, "word must be one to four plain words"
        if not re.fullmatch(r"[a-z0-9' ]+", " ".join(word.lower().split())):
            return None, ("word must be letters and digits only; hyphens split "
                          "words, so ask for \"record keeping\", not \"record-keeping\"")
    if by not in ("week", "month"):
        return None, "by must be \"week\" or \"month\""
    if through is not None:
        if not _DATE_RE.fullmatch(through):
            return None, "through must be a date like 2026-10-29"
        try:
            datetime.date.fromisoformat(through)
        except ValueError:
            return None, "through must be a real date like 2026-10-29"
    return phrase, None
