"""Small thread files and operator-only, byte-exact legacy moves."""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from tools.goals import slug, title_of, text
from tools.stop_marker import atomic_write
from tools import names

CALLS = {
    'open_thread': 'open_thread(title="<title>", stands="<where it stands>")',
    'update_thread': 'update_thread(title="<title>", update="<what happened>")',
    'close_thread': 'close_thread(title="<title>", how_it_closed="<one line>")',
    'read_thread': 'read_thread(title="<title>", closed_in="")',
    'read_threads': 'read_threads(start=1)',
}
UPDATE_REFUSAL = ('update_open_threads REFUSED: nothing was changed. Each thread is its own file now. '
    'Use open_thread(title="<title>", stands="<where it stands>"), update_thread(title="<title>", update="<what happened>") '
    'or close_thread(title="<title>", how_it_closed="<one line>"). read_threads() lists them all.')


CALLS = {key: names.render_tools(value) for key, value in CALLS.items()}
UPDATE_REFUSAL = names.render_tools(UPDATE_REFUSAL)


class ThreadError(ValueError):
    def __init__(self, fact: str, call: str | None = None):
        super().__init__(fact)
        self.call = call


def settings(root: Path) -> dict:
    path = root / 'config/cards.json'
    if not path.is_file():
        path = Path(__file__).resolve().parents[1] / 'config/cards.json'
    cfg = json.loads(path.read_bytes())['threads']
    def render(value):
        if isinstance(value, str):
            return names.render_tools(value.replace('{wake_prefix}', re.escape(names.load()['wake_id']['prefix'])).replace('{legacy_name}', Path(names.rel('open_threads')).name))
        if isinstance(value, dict):
            return {key: render(item) for key, item in value.items()}
        return value
    return render(cfg)


def folder(root: Path) -> Path:
    path = root / 'naming.json'
    naming = json.loads(path.read_bytes()) if path.is_file() else {}
    return root / naming.get('paths', {}).get('threads', names.rel('threads')).format(
        **(names.load()['dirs'] | naming.get('dirs', {})))


def files(root: Path, closed: bool = False) -> list[Path]:
    return sorted((folder(root) / 'closed' if closed else folder(root)).glob('*.md'))


def exists(root: Path) -> bool:
    return bool(files(root) or files(root, True))


def refusal(tool: str, fact: str, call: str | None = None) -> str:
    return f'{names.tool(tool)} REFUSED: nothing was changed because {fact.rstrip(".")}; call {call or CALLS[tool]}.'


def validate_line(value: str, fact: str, optional: bool = False) -> None:
    if not isinstance(value, str) or '\r' in value or '\n' in value or (not optional and not value.strip()):
        raise ThreadError(fact)


def named_path(root: Path, title: str) -> Path:
    cfg = settings(root)
    validate_line(title, cfg['errors']['title'])
    key = slug(title)
    if not key:
        raise ThreadError(cfg['errors']['slug'])
    path = folder(root) / (key + '.md')
    if path.is_file() and title_of(text(path)) != title:
        raise ThreadError(cfg['errors']['collision'].format(filename=path.name, title=json.dumps(title_of(text(path)), ensure_ascii=False)))
    return path


def shape(root: Path, title: str, stands: str) -> bytes:
    cfg = settings(root)
    if not isinstance(stands, str):
        raise ThreadError(cfg['errors']['stands'])
    return cfg['file'].format(title=title, stands=stands).encode('utf-8')


def open_thread(root: Path, title: str, stands: str) -> Path:
    path = named_path(root, title)
    if path.exists():
        raise ThreadError(settings(root)['errors']['exists'])
    data = shape(root, title, stands)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, data)
    return path


def parts(root: Path, body: str):
    cfg = settings(root)
    standing = re.search(cfg['stands_heading'], body)
    updates = list(re.finditer(cfg['updates_heading'], body))
    status = re.search(cfg['open_status'], body)
    if not standing or not updates or not status or standing.end() > updates[-1].start():
        raise ThreadError(cfg['errors']['shape'])
    return standing, updates[-1], status


def update_thread(root: Path, title: str, update: str, stands: str, wake: int, date: str) -> Path:
    cfg = settings(root)
    path = exact_open_path(root, title, 'update_thread')
    if not isinstance(update, str):
        raise ThreadError(cfg['errors']['update'])
    if not isinstance(stands, str):
        raise ThreadError(cfg['errors']['stands'])
    body = text(path)
    standing, updates, _ = parts(root, body)
    if stands:
        body = body[:standing.end()] + '\n' + stands + '\n\n' + body[updates.start():]
    # Only physical CR/LF lines are indented; Unicode separators remain text.
    indented = re.sub(r'(\r?\n|\r)', lambda m: m.group() + '  ', update)
    if not body.endswith('\n'):
        body += '\n'
    body += cfg['update'].format(date=date, wake=names.wake_id(wake), text=indented)
    atomic_write(path, body.encode('utf-8'))
    return path


def close_thread(root: Path, title: str, how_it_closed: str, wake: int, date: str) -> tuple[Path, Path]:
    cfg = settings(root)
    validate_line(how_it_closed, cfg['errors']['closure'])
    old = exact_open_path(root, title, 'close_thread')
    body = text(old)
    _, _, status = parts(root, body)
    new = folder(root) / 'closed' / f'{old.stem}-{names.wake_id(wake)}.md'
    suffix = 2
    while new.exists():
        new = new.with_name(f'{old.stem}-{names.wake_id(wake)}-{suffix}.md')
        suffix += 1
    replacement = cfg['closed_status'].format(date=date, wake=names.wake_id(wake), text=how_it_closed)
    new.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(old, (body[:status.start()] + replacement + body[status.end():]).encode('utf-8'))
    old.replace(new)
    return old, new


def newest(root: Path, body: str) -> tuple[str, int]:
    cfg = settings(root)
    headings = list(re.finditer(cfg['updates_heading'], body))
    updates = body[headings[-1].end():] if headings else ''
    matches = list(re.finditer(cfg['dated_entry'], updates))
    return max(((m.group(1), int(m.group(2))) for m in matches), default=('', 0))


def summaries(root: Path, closed: bool = False) -> list[dict]:
    result = []
    for path in files(root, closed):
        body = text(path)
        date, wake = newest(root, body)
        result.append(dict(title=title_of(body), date=date, wake=wake, bytes=path.stat().st_size, path=path))
    return sorted(result, key=lambda item: (item['date'], item['wake'], item['title']), reverse=True)


def fit_line_indexes(entries: list[str], budget: int, tail, max_lines: int | None = None) -> tuple[str, list[int]]:
    """Fit lines in order; a line too long is skipped and later ones still tried.

    Returns the text and the positions of the lines it shows, which need not
    be the first ones.
    """
    chosen: list[int] = []
    for index in range(len(entries)):
        trial = chosen + [index]
        if max_lines is not None and len(trial) > max_lines:
            break
        answer = '\n'.join([entries[i] for i in trial] + tail(len(entries) - len(trial)))
        if len(answer.encode('utf-8')) <= budget:
            chosen = trial
    return '\n'.join([entries[i] for i in chosen] + tail(len(entries) - len(chosen))), chosen


def fit_lines(entries: list[str], budget: int, tail, max_lines: int | None = None) -> tuple[str, int]:
    body, chosen = fit_line_indexes(entries, budget, tail, max_lines)
    return body, len(chosen)


def suggest_title(root: Path, title: str, tool: str, closed: bool = False, closed_in: str = '') -> None:
    cfg = settings(root)
    titles = list(dict.fromkeys(item['title'] for item in summaries(root, closed)
                               if title.casefold() in item['title'].casefold()))
    if not titles:
        return
    kind = 'closed' if closed else 'open'
    quoted = [json.dumps(value, ensure_ascii=False) for value in titles]
    if len(titles) == 1:
        fact = cfg['suggestions'][kind + '_one'].format(title=quoted[0])
        call = cfg['suggestion_calls'][tool].format(title=quoted[0], closed_in=json.dumps(closed_in))
    else:
        limit = cfg['suggestion_limit']
        fact = cfg['suggestions'][kind + '_many'].format(count=len(titles), titles=', '.join(quoted[:limit]))
        if len(titles) > limit:
            fact += cfg['suggestions']['more'].format(more=len(titles) - limit)
        call = None
    raise ThreadError(fact, call)


def exact_open_path(root: Path, title: str, tool: str) -> Path:
    cfg = settings(root)
    validate_line(title, cfg['errors']['title'])
    key = slug(title)
    path = folder(root) / (key + '.md')
    if key and path.is_file() and title_of(text(path)) == title:
        return path
    suggest_title(root, title, tool)
    # With no containing title, retain the previous file-name/validation refusal.
    named_path(root, title)
    raise ThreadError(cfg['errors']['missing'])


def read_threads(root: Path, start: int = 1) -> str:
    cfg = settings(root)
    items = summaries(root)
    total = len(items)
    if type(start) is not int or start < 1 or start > max(1, total):
        raise ThreadError(cfg['errors']['start'])
    entries = [cfg['index_line'].format(**(item | {'date': item['date'] or cfg['no_updates']})) for item in items]
    closed = cfg['closed_count'].format(count=len(files(root, True)))

    def render(count):
        last = start - 1 + count
        heading = [cfg['page_heading'].format(first=start, last=last, total=total)] if start > 1 else []
        tail = [closed]
        if last < total:
            tail.append(cfg['omitted'].format(count=total-last, next=last+1))
        return '\n'.join(heading + entries[start-1:last] + tail)

    remaining = max(0, total - start + 1)
    if len(render(remaining).encode('utf-8')) <= cfg['index_bytes']:
        return render(remaining)
    count = 0
    for candidate in range(1, remaining+1):
        if len(render(candidate).encode('utf-8')) > cfg['index_bytes']:
            break
        count = candidate
    return render(count)


def read_thread(root: Path, title: str, closed_in: str = '') -> str:
    return read_thread_file(root, title, closed_in)[1]


def read_thread_file(root: Path, title: str, closed_in: str = '') -> tuple[Path, str]:
    """Resolve the exact source alongside the unchanged reader answer."""
    cfg = settings(root)
    validate_line(title, cfg['errors']['title'])
    path = folder(root) / (slug(title) + '.md')
    validate_line(closed_in, cfg['errors']['closed_in'], optional=True)
    if closed_in and not re.fullmatch(rf'{re.escape(names.load()["wake_id"]["prefix"])}[0-9]+(?:-(?:[2-9]|[1-9][0-9]+))?', closed_in):
        raise ThreadError(cfg['errors']['closed_in'])
    if not closed_in and path.is_file() and title_of(text(path)) == title:
        return path, text(path)
    closings = []
    for candidate in files(root, True):
        body = text(candidate)
        if title_of(body) != title:
            continue
        match = re.search(cfg['closed_pattern'], body)
        if not match:
            raise ThreadError(cfg['errors']['closed_shape'])
        wake = int(match.group(1))
        suffix = re.fullmatch(re.escape(slug(title)) + '-' + re.escape(names.wake_id(wake)) + r'(?:-([0-9]+))?', candidate.stem)
        closings.append((wake, int(suffix.group(1)) if suffix and suffix.group(1) else 1, candidate.name, body))
    closings.sort(reverse=True)
    def selector(item):
        return names.wake_id(item[0]) + (f'-{item[1]}' if item[1] > 1 else '')
    matching = [item for item in closings if not closed_in or selector(item) == closed_in]
    if not matching:
        # An exact title with an absent closing is not a title mismatch.
        if not closings and not (path.is_file() and title_of(text(path)) == title):
            suggest_title(root, title, 'read_thread', closed_in=closed_in)
            suggest_title(root, title, 'read_thread', closed=True, closed_in=closed_in)
            if not slug(title):
                raise ThreadError(cfg['errors']['slug'])
        raise ThreadError(cfg['errors']['closing_missing'] if closed_in else cfg['errors']['any_missing'])
    answer = matching[0][3]
    if not closed_in and len(closings) > 1:
        answer += '\n\n' + cfg['earlier'].format(wakes=', '.join(selector(item) for item in closings[1:]))
    return folder(root) / "closed" / matching[0][2], answer


def legacy_paths(root: Path) -> tuple[Path, Path, Path]:
    naming_path = root / 'naming.json'
    naming = json.loads(naming_path.read_bytes()) if naming_path.is_file() else {}
    dirs = names.load()['dirs'] | naming.get('dirs', {})
    cfg = settings(root)
    old = root / naming.get('paths', {}).get('open_threads', names.rel('open_threads')).format(**dirs)
    return old, root / naming.get('paths', {}).get('threads_archive', names.rel('threads_archive')).format(**dirs), root / naming.get('paths', {}).get('threads_before', names.rel('threads_before')).format(**dirs)


def entries(data: bytes) -> list[dict]:
    # Literal file grammar; never a search pattern built from an operator title.
    markers = list(re.finditer(rb'(?m)^(?:- \*\*([^\r\n]*?)\*\*[^\r\n]*|## [^\r\n]*)(?:\r?\n|$)', data))
    result = []
    heading = ''
    for i, match in enumerate(markers):
        if match.group(1) is None:
            heading = match.group().decode('utf-8').rstrip('\r\n')
            continue
        end = markers[i + 1].start() if i + 1 < len(markers) else len(data)
        result.append(dict(title=match.group(1).decode('utf-8'), start=match.start(), end=end,
                           data=data[match.start():end], heading=heading))
    return result


def archive_entries(root: Path, list_path: Path, date: str) -> str:
    cfg = settings(root)
    old, archive, _ = legacy_paths(root)
    titles = list_path.read_bytes().decode('utf-8').splitlines()
    data = old.read_bytes() if old.is_file() else b''
    current = entries(data)
    prior = entries(archive.read_bytes()) if archive.is_file() else []
    selected, errors = [], []
    for title in titles:
        matches = [entry for entry in current if entry['title'] == title]
        if len(matches) == 1:
            selected.append(matches[0])
        elif not matches and len([entry for entry in prior if entry['title'] == title]) == 1:
            continue
        else:
            errors.append(cfg['list_error'].format(title=json.dumps(title, ensure_ascii=False), count=len(matches)))
    if errors:
        return '\n'.join(errors)
    selected = sorted({entry['start']: entry for entry in selected}.values(), key=lambda entry: entry['start'])
    if not selected:
        return cfg['archive_noop']
    moved = b''.join(entry['data'] for entry in selected)
    prior_bytes = archive.read_bytes() if archive.is_file() else b''
    header = cfg['archive_heading'].format(date=date).encode('utf-8')
    # A final entry without an EOL remains exact; the next heading has its own separator.
    atomic_write(archive, prior_bytes + header + moved)
    remaining = bytearray()
    cursor = 0
    for entry in selected:
        remaining.extend(data[cursor:entry['start']])
        cursor = entry['end']
    remaining.extend(data[cursor:])
    atomic_write(old, bytes(remaining))
    return cfg['archive_result'].format(count=len(selected), bytes=len(moved))


def split_old_file(root: Path, date: str) -> str:
    cfg = settings(root)
    old, _, before = legacy_paths(root)
    if not old.is_file():
        return cfg['split_noop']
    data = old.read_bytes()
    current = entries(data)
    resolved = [entry for entry in current if re.search(cfg['resolved_heading'], entry['heading'])]
    if resolved:
        return '\n'.join(cfg['resolved_error'].format(title=json.dumps(entry['title'], ensure_ascii=False)) for entry in resolved)
    planned, seen = [], {}
    for entry in current:
        path = named_path(root, entry['title'])
        if path.name in seen:
            return cfg['split_collision'].format(first=json.dumps(seen[path.name], ensure_ascii=False), second=json.dumps(entry['title'], ensure_ascii=False))
        if path.exists() or before.exists():
            return cfg['split_exists'].format(path=path.name if path.exists() else before.name)
        seen[path.name] = entry['title']
        stands = cfg['move_comment'].format(date=date) + entry['data'].decode('utf-8')
        planned.append((path, shape(root, entry['title'], stands)))
    if before.exists():
        return cfg['split_exists'].format(path=before.name)
    written = []
    try:
        for path, content in planned:
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(path, content)
            written.append(path)
        old.replace(before)
    except OSError:
        for path in written:
            path.unlink()
        raise
    return cfg['split_result'].format(count=len(planned), bytes=sum(len(content) for _, content in planned))


def check_move(root: Path, original: Path) -> str:
    cfg = settings(root)
    _, archive, before = legacy_paths(root)
    source = entries(original.read_bytes())
    archive_bytes = archive.read_bytes() if archive.is_file() else b''
    thread_bytes = [path.read_bytes() for path in files(root) + files(root, True)]
    before_entries = entries(before.read_bytes()) if before.is_file() else []
    errors = []
    for entry in source:
        block = entry['data']
        archived = archive_bytes.count(block)
        split = sum(body.count(block) for body in thread_bytes)
        in_before = sum(item['data'] == block for item in before_entries)
        if archived + split != 1 or (split and in_before != 1) or (archived and in_before):
            errors.append(cfg['check_error'].format(title=json.dumps(entry['title'], ensure_ascii=False)))
    return '\n'.join(errors) if errors else cfg['check_result'].format(count=len(source))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['archive-entries', 'split-old-file', 'check-move'])
    parser.add_argument('--repo-root', type=Path, default=Path.cwd())
    parser.add_argument('--list', type=Path)
    parser.add_argument('--original', type=Path)
    args = parser.parse_args()
    date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    if args.command == 'archive-entries':
        if args.list is None:
            parser.error('archive-entries requires --list')
        answer = archive_entries(args.repo_root, args.list, date)
    elif args.command == 'split-old-file':
        answer = split_old_file(args.repo_root, date)
    else:
        if args.original is None:
            parser.error('check-move requires --original')
        answer = check_move(args.repo_root, args.original)
    print(answer)


if __name__ == '__main__':
    main()
