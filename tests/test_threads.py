"""Brief 23: invented fixtures, literal bytes, and small thread tools."""
import ast
import asyncio
import datetime
import inspect
import json
from pathlib import Path
import re
import subprocess
import sys

import pytest
from tools import cards as c, harness_mcp as h, threads as t

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.datetime(2026, 10, 11, 12, tzinfo=datetime.timezone.utc)


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode('utf-8'))


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(h, 'REPO', tmp_path)
    monkeypatch.setattr(h, '_cards_patterns', lambda: tmp_path / 'config/tag-patterns.city.json')
    monkeypatch.setattr(h, '_utc_now', lambda: NOW)
    naming = json.loads((ROOT / 'naming.json').read_bytes())
    # Only invented sources are configured, and no real source is consulted.
    naming['paths'] = {key: value for key, value in naming['paths'].items()
                      if key in {'identity','governance','threads','open_threads','goal_files','projects','journal'}}
    naming['indexes'] = {}
    write(tmp_path / 'naming.json', json.dumps(naming))
    for name in ['cards.json','tag-patterns.city.json','tag-patterns.generic.json']:
        write(tmp_path / 'config' / name, (ROOT / 'config' / name).read_bytes())
    write(tmp_path / 'PERSONNEL-FILE.md', '# Invented identity\n')
    write(tmp_path / 'STANDING-ORDERS.md', '# Invented governance\n')
    write(tmp_path / 'budget.json', b'{"wake_count":151}')
    write(tmp_path / 'records/open-matters.md', legacy())
    write(tmp_path / 'records/shift-reports/SR-0151.md', b'# Invented log SR-0151\n\n## What I plan next\nCount spoons.\n')
    return tmp_path


def legacy(eol=b'\n', duplicate=False, collision=False):
    titles = ['Visit amber','Café 日本','Bare entry','x' * 210,'Question four','Question five','Question six','Question seven']
    if duplicate:
        titles[1] = titles[0]
    if collision:
        titles[1] = 'Visit-amber'
    lines = ['# Open Matters', '', '## Living threads', '']
    for i, title in enumerate(titles):
        lines.append('- **' + title + '**')
        if i != 2:
            lines += ['  - **Initiated:** SR-0140','  - **Context:** resident #260 at Place 823, café 日本.',
                      '  - **Progression:** An invented conversation.','  - **Status:** Pending.']
        lines.append('')
    lines += ['## Resolved threads','','- **Settled first**','  - **Status:** Done.','','- **Settled second**','']
    return eol.join(line.encode('utf-8') for line in lines)


def snapshot(repo):
    return {p.relative_to(repo).as_posix(): p.read_bytes() for p in repo.rglob('*') if p.is_file()}


def store(repo):
    return c.CardStore(repo / 'records', repo / 'config/tag-patterns.city.json')


def recut(repo, logical):
    return c.recut_all(repo, repo / 'naming.json', repo / 'config/tag-patterns.city.json', logical)


def wake(repo):
    return c.read_wake_card(repo, repo / 'naming.json', config_path=repo / 'config/cards.json')


def test_shape_updates_verbatim_stands_and_multiline(repo):
    stands = 'Café 日本\r\nresident #260 at Place 823\r\n'
    assert h.open_thread(title='Amber', stands=stands) == 'Thread "Amber" opened.'
    path = repo / 'records/threads/amber.md'
    assert path.read_bytes() == t.settings(repo)['file'].format(title='Amber', stands=stands).encode()
    first = path.read_bytes()
    update = 'First café\r\nNext line\nThird 日本'
    assert h.update_thread(title='Amber', update=update) == 'Thread "Amber" updated.'
    expected = first + '- **2026-10-11 (SR-0152):** First café\r\n  Next line\n  Third 日本\n'.encode()
    assert path.read_bytes() == expected
    h.update_thread(title='Amber', update='Second.', stands='Now settled.\r\nNothing owed.')
    body = path.read_bytes()
    assert stands.encode() not in body and b'Now settled.\r\nNothing owed.' in body
    assert expected.split(b'## Updates\n', 1)[1] in body
    assert body.endswith(b'- **2026-10-11 (SR-0152):** Second.\n')
    cards = sorted((card for card in store(repo)._current_state().values()
                    if card['origin']['file'] == 'records/threads/amber.md'), key=lambda card: card['origin']['order'])
    assert [card['origin']['key'] for card in cards] == ['header','entry:2026-10-11-152','entry:2026-10-11-152-2']
    assert [card['role'] for card in cards] == ['current','history','history']
    assert [card['title'] for card in cards] == ['Thread · Amber · where it stands','Thread · Amber · 2026-10-11 (SR-0152)','Thread · Amber · 2026-10-11 (SR-0152)']
    assert all('thread:amber' in card['tags'] for card in cards)
    assert ''.join(card['text'] for card in cards).encode() == body


def test_writers_never_limit_size_or_cut_title(repo):
    title = 'Large ' + 'x' * 220
    stands = '- **2099-01-01 (SR-0999):** Quoted example, not an update.\n' + '日本' * 20000
    h.open_thread(title=title, stands=stands)
    assert 'no updates' in h.read_threads()
    h.update_thread(title=title, update='é' * 20000)
    assert title in h.read_thread(title=title) and stands in h.read_thread(title=title)
    assert len(t.slug(title)) == 60
    assert len(h.read_threads().encode()) <= 6000


@pytest.mark.parametrize('name,kwargs,fact', [
 ('open_thread', dict(title='Amber', stands='new'), 'exists'),
 ('open_thread', dict(title='Am-ber', stands='new'), None),
 ('open_thread', dict(title='???', stands='new'), 'slug'),
 ('open_thread', dict(title='two\nlines', stands='new'), 'title'),
 ('open_thread', dict(title='Empty', stands=None), 'stands'),
 ('update_thread', dict(title='Missing', update='text'), 'missing'),
 ('update_thread', dict(title='Amber', update=None), 'update'),
 ('update_thread', dict(title='Amber', update='text', stands=None), 'stands'),
 ('close_thread', dict(title='Missing', how_it_closed='done'), 'missing'),
 ('close_thread', dict(title='Amber', how_it_closed=''), 'closure'),
 ('close_thread', dict(title='Amber', how_it_closed='two\nlines'), 'closure'),
 ('read_thread', dict(title='Missing'), 'any_missing'),
 ('read_thread', dict(title='Amber', closed_in='152'), 'closed_in'),
 ('read_thread', dict(title='Amber', closed_in='SR-0999'), 'closing_missing'),
])
def test_refusals_change_nothing(repo, name, kwargs, fact):
    h.open_thread(title='Amber', stands='Original.')
    if fact is None:
        # A different pair that really shares a slug.
        h.open_thread(title='Am ber', stands='Original.')
    before = snapshot(repo)
    answer = getattr(h, name)(**kwargs)
    assert 'REFUSED: nothing was changed because' in answer
    assert answer.endswith('; call ' + t.CALLS[name] + '.')
    assert snapshot(repo) == before
    if fact:
        assert t.settings(repo)['errors'][fact] in answer


def test_malformed_files_and_stop_check_refuse_without_changes(repo, monkeypatch):
    h.open_thread(title='Amber', stands='Original.')
    path = repo / 'records/threads/amber.md'
    write(path, path.read_bytes().replace(b'- **Status:** open', b'- **Status:** unknown'))
    before = snapshot(repo)
    assert 'required sections' in h.update_thread(title='Amber', update='new')
    assert 'required sections' in h.close_thread(title='Amber', how_it_closed='done')
    assert snapshot(repo) == before
    write(repo / 'STOP.md', b'Invented stop')
    before = snapshot(repo)
    for name, kwargs in [('open_thread', dict(title='New', stands='new')), ('update_thread', dict(title='Amber', update='new')), ('close_thread', dict(title='Amber', how_it_closed='done'))]:
        assert getattr(h, name)(**kwargs) == t.refusal(name, 'STOP.md is set.')
        assert snapshot(repo) == before
    monkeypatch.setattr(h.stop_marker, 'marker_present', lambda root: (_ for _ in ()).throw(PermissionError()))
    assert h.open_thread(title='New', stands='new') == t.refusal('open_thread', 'The stop marker could not be checked.')
    assert snapshot(repo) == before


def test_close_reopen_suffixes_cards_recall_and_read_card(repo):
    original_ids = None
    for n in range(3):
        h.open_thread(title='Amber', stands=f'Opening {n}')
        h.update_thread(title='Amber', update='An update.')
        path = repo / 'records/threads/amber.md'
        before = path.read_bytes()
        assert h.close_thread(title='Amber', how_it_closed='Settled.') == 'Thread "Amber" closed.'
        closed = repo / 'records/threads/closed' / ('amber-SR-0152.md' if n == 0 else f'amber-SR-0152-{n+1}.md')
        assert closed.read_bytes() == before.replace(b'- **Status:** open', b'- **Status:** closed in SR-0152, 2026-10-11: Settled.', 1)
        assert h.read_thread(title='Amber', closed_in='SR-0152' if n == 0 else f'SR-0152-{n+1}').encode() == closed.read_bytes()
        current = [card for card in store(repo)._current_state().values() if card['origin']['logical'] == 'threads']
        assert len(current) == 2 * (n + 1) and all(card['status'] == 'done' for card in current)
        if n == 0:
            original_ids = {card['id']: card for card in current}
        else:
            assert all(store(repo)._current_state()[key] == value for key, value in original_ids.items())
    assert h.read_thread(title='Amber').endswith('Earlier closings: SR-0152-2, SR-0152.')
    args = dict(tags=['thread:amber'], kinds=['thread'], config_path=repo / 'config/cards.json', record_read=False)
    assert not c.recall(repo / 'records', **args)['cards']
    historical = c.recall(repo / 'records', history=True, **args)['cards']
    assert len(historical) == 6
    for card in historical:
        assert c.read_card(repo / 'records', card['id'])['card'] == card
    assert 'Amber' not in wake(repo)['text']
    h.open_thread(title='Amber', stands='Fourth opening')
    assert h.read_thread(title='Amber').startswith('# Amber') and 'Fourth opening' in h.read_thread(title='Amber')
    stable = store(repo).cards_path.read_bytes()
    assert recut(repo, 'threads')['changed'] == 0
    assert store(repo).cards_path.read_bytes() == stable
    generated = repo / 'generated'
    c.generate_views(store(repo).cards_path, generated)
    for path in t.files(repo) + t.files(repo, True):
        assert (generated / path.relative_to(repo)).read_bytes() == path.read_bytes()


def test_read_older_wake_and_slug_collision_with_closed_lookup(repo):
    h.open_thread(title='A B', stands='First')
    h.close_thread(title='A B', how_it_closed='Settled')
    first = (repo / 'records/threads/closed/a-b-SR-0152.md').read_bytes()
    h.open_thread(title='A-B', stands='Different title, same slug')
    assert h.read_thread(title='A B').encode() == first
    h.close_thread(title='A-B', how_it_closed='Settled')
    write(repo / 'budget.json', b'{"wake_count":152}')
    h.open_thread(title='A B', stands='Again')
    h.close_thread(title='A B', how_it_closed='Settled')
    assert h.read_thread(title='A B', closed_in='SR-0152').encode() == first
    assert h.read_thread(title='A B').endswith('Earlier closings: SR-0152.')


def test_old_tools_exact_before_and_after_first_file(repo):
    before = h.read_open_threads()
    assert before == (repo / 'records/open-matters.md').read_text(encoding='utf-8')
    t.folder(repo).mkdir()
    assert h.read_open_threads() == before
    new = before + '\n- **An invented additional thread**\n'
    assert h.update_open_threads(new_text=new) == f'records/open-matters.md updated ({len(new)} chars).'
    h.open_thread(title='Amber', stands='Invented standing text')
    assert h.read_open_threads() == h.read_threads()
    stable = snapshot(repo)
    assert h.update_open_threads(new_text='do not write') == t.UPDATE_REFUSAL
    assert snapshot(repo) == stable
    h.close_thread(title='Amber', how_it_closed='Settled')
    assert h.read_open_threads() == 'Closed threads: 1.'
    assert h.update_open_threads(new_text=new) == t.UPDATE_REFUSAL


def test_index_overflow_sorting_and_wake_header_tags(repo):
    for i in range(150):
        t.open_thread(repo, f'Thread {i:03} ' + 'é' * 25, f'resident #260 at Place {i + 800}.')
        t.update_thread(repo, f'Thread {i:03} ' + 'é' * 25, 'Place 99999.', '', 152+i, '2026-10-11')
    recut(repo, 'threads')
    answer = h.read_threads()
    assert len(answer.encode()) <= 6000
    lines = [line for line in answer.splitlines() if ' bytes.' in line]
    assert lines and lines[0].startswith('Thread 149')
    assert f'{150-len(lines)} more open threads are not shown;' in answer
    assert answer.endswith(f'read_threads(start={len(lines)+1}).')
    cfg = json.loads((repo / 'config/cards.json').read_bytes())
    cfg['wake_sections']['thread_index'] = 400
    write(repo / 'config/cards.json', json.dumps(cfg))
    result = wake(repo)
    section = result['text'].split('## Open threads (index)\n\n')[1].split('\n\n## Rules')[0]
    assert section.startswith('- Thread 149') and 'resident:260' in section and 'place:949' in section
    assert 'place:99999' not in section
    shown = len([line for line in section.splitlines() if line.startswith('- ')])
    assert f'{150-shown} more open threads are not listed; read_threads().' in section
    assert len(section.encode()) <= 400


def test_settings_and_missing_folder_recut(repo):
    assert recut(repo, 'threads') == dict(files=0, made=0, closed=0, changed=0, left_alone=0)
    cfg = json.loads((repo / 'config/cards.json').read_bytes())
    cfg['threads']['file'] = cfg['threads']['file'].replace('Where it stands','Situation').replace('Updates','Notes')
    cfg['threads']['stands_heading'] = r'(?m)^## Situation\r?\n'
    cfg['threads']['updates_heading'] = r'(?m)^## Notes\r?\n'
    write(repo / 'config/cards.json', json.dumps(cfg))
    patterns = json.loads((repo / 'config/tag-patterns.city.json').read_bytes())
    patterns['card_shapes']['threads'].update(stands_heading=cfg['threads']['stands_heading'], entries_heading=cfg['threads']['updates_heading'], header_label='situation', label='Discussion / {title} / {section}')
    write(repo / 'config/tag-patterns.city.json', json.dumps(patterns))
    h.open_thread(title='Amber', stands='Café 日本')
    h.update_thread(title='Amber', update='Noted')
    current = list(store(repo)._current_state().values())
    assert any(card.get('title') == 'Discussion / Amber / situation' for card in current)
    assert '## Situation' in h.read_thread(title='Amber')


def test_goal_self_tag_and_plan_links_only_whole_names(repo):
    h.set_goal(name='Learn café', how='Read')
    h.open_project(title='Amber model', target='A model', next_step='Draw', serves='Learn café')
    path = repo / 'records/shift-reports/SR-0999.md'
    write(path, '# Log SR-0999\n\n## Other\nLearn café, Amber model.\n\n## What I plan next\nLEARN CAFÉ; Amber model. NotLearn caféish.\n')
    recut(repo, 'journal')
    state = store(repo)._current_state()
    goal = next(card for card in state.values() if card['origin']['logical'] == 'goal_files')
    assert 'goal:learn-cafe' in goal['tags']
    log = [card for card in state.values() if card['origin']['file'].endswith('SR-0999.md')]
    plan = next(card for card in log if 'plan next' in card['text'])
    assert {'goal:learn-cafe','goal:amber-model'} <= set(plan['tags'])
    assert all(not any(tag.startswith('goal:') for tag in card['tags']) for card in log if card != plan)
    before = plan.copy()
    h.drop_goal(name='Learn café', reason='Changed')
    assert store(repo)._current_state()[plan['id']] == before
    answer = c.recall(repo / 'records', tags=['goal:learn-cafe'], history=True, config_path=repo / 'config/cards.json', record_read=False)
    assert any(card['kind'] == 'project' for card in answer['cards'])
    assert any(card['id'] == plan['id'] for card in answer['cards'])


@pytest.mark.parametrize('eol', [b'\n',b'\r\n'])
def test_operator_archive_split_check_exact_bytes_and_noops(repo, eol):
    data = legacy(eol)
    original = repo / 'original.md'
    write(original, data)
    old, archive, before = t.legacy_paths(repo)
    write(old, data)
    original_entries = t.entries(data)
    selected = ['Settled second','Café 日本','Settled first']
    list_path = repo / 'finished.txt'
    write(list_path, '\n'.join(selected))
    moved = b''.join(entry['data'] for entry in original_entries if entry['title'] in selected)
    assert t.archive_entries(repo, list_path, '2026-10-11') == f'Moved 3 entries and {len(moved)} bytes.'
    assert archive.read_bytes() == t.settings(repo)['archive_heading'].format(date='2026-10-11').encode() + moved
    stable = snapshot(repo)
    assert 'already archived' in t.archive_entries(repo, list_path, '2026-10-11')
    assert snapshot(repo) == stable
    remaining = old.read_bytes()
    assert t.split_old_file(repo, '2026-10-11').startswith('Wrote 7 thread files')
    assert before.read_bytes() == remaining and not old.exists()
    for entry in t.entries(remaining):
        body = (t.folder(repo) / (t.slug(entry['title']) + '.md')).read_bytes()
        assert entry['data'] in body
        assert body == t.shape(repo, entry['title'], t.settings(repo)['move_comment'].format(date='2026-10-11') + entry['data'].decode())
    assert t.check_move(repo, original) == 'All 10 entries accounted for.'
    stable = snapshot(repo)
    assert 'nothing was changed' in t.split_old_file(repo, '2026-10-11')
    assert t.check_move(repo, original) == 'All 10 entries accounted for.'
    assert snapshot(repo) == stable
    path = t.folder(repo) / 'visit-amber.md'
    write(path, path.read_bytes().replace(b'Pending.', b'Pending!', 1))
    assert 'Visit amber' in t.check_move(repo, original)
    recut(repo, 'open_threads')
    recut(repo, 'threads')
    assert not any(card['status'] == 'live' and card['origin']['logical'] == 'open_threads' for card in store(repo)._current_state().values())


@pytest.mark.parametrize('variant', ['unknown','ambiguous','resolved','collision'])
def test_operator_preflights_change_nothing(repo, variant):
    old = t.legacy_paths(repo)[0]
    write(old, legacy(duplicate=variant == 'ambiguous', collision=variant == 'collision'))
    listing = repo / 'list.txt'
    write(listing, 'Absent' if variant == 'unknown' else 'Visit amber')
    if variant == 'collision':
        listing.write_bytes(b'Settled first\nSettled second')
        t.archive_entries(repo, listing, '2026-10-11')
    stable = snapshot(repo)
    if variant in {'unknown','ambiguous'}:
        answer = t.archive_entries(repo, listing, '2026-10-11')
        assert 'matches 0' in answer if variant == 'unknown' else 'matches 2' in answer
    else:
        answer = t.split_old_file(repo, '2026-10-11')
        assert 'resolved entry' in answer if variant == 'resolved' else 'same slug' in answer
    assert snapshot(repo) == stable


def test_protocol_help_registry_and_operator_commands_not_registered(repo):
    registry = {tool.name: tool for tool in asyncio.run(h.mcp.list_tools())}
    names = set(t.CALLS) | {'read_threads'}
    assert names <= registry.keys()
    assert not {'archive_entries','split_old_file','check_move'} & registry.keys()
    group = h.help().split('Threads\n')[1].split('\n\n')[0]
    for name in names:
        assert name + ':' in group
        assert 'Example: ' + name + '(' in h.help(tool=name)
    for sample in list(t.CALLS.values()) + [t.UPDATE_REFUSAL, t.settings(repo)['omitted'].format(count=2, next=3), t.settings(repo)['wake_omitted']]:
        for match in re.finditer(r'(\w+)\(([^()]*)\)', sample):
            call = ast.parse(match.group(), mode='eval').body
            assert not call.args
            kwargs = {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords}
            inspect.signature(getattr(h, call.func.id)).bind(**kwargs)
    args = [sys.executable, '-m', 'tools.threads', 'split-old-file', '--repo-root', str(repo)]
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0 and 'resolved entry' in result.stdout


def test_wake_legacy_fallback_exact_index(repo):
    recut(repo, 'open_threads')
    recut(repo, 'journal')
    result = wake(repo)
    state = list(store(repo)._current_state().values())
    candidates = c._file_order([card for card in state if card['kind'] == 'thread' and card['status'] == 'live'])
    expected = c._wake_added_section('Open threads (index)', candidates, 4800)['text']
    section = result['text'].split('## Open threads (index)\n\n')[1].split('\n\n## Rules')[0]
    assert section.encode() == expected.encode()


def run_shown_call(answer):
    call = ast.parse(answer.rsplit('; call ', 1)[1][:-1], mode='eval').body
    assert not call.args
    kwargs = {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords}
    return getattr(h, call.func.id)(**kwargs)


def test_read_threads_fitting_answer_unchanged(repo):
    t.open_thread(repo, 'Café 日本', 'Invented.')
    t.open_thread(repo, 'Amber', 'Invented.')
    t.update_thread(repo, 'Amber', 'First.', '', 152, '2026-10-11')
    # Literal baseline layout, including the unchanged closed count and no heading.
    expected = (f'Amber — 2026-10-11; {(t.folder(repo) / "amber.md").stat().st_size} bytes.\n'
                f'Café 日本 — no updates; {(t.folder(repo) / "cafe.md").stat().st_size} bytes.\n'
                'Closed threads: 0.').encode()
    assert h.read_threads().encode() == expected
    assert h.read_threads(start=1).encode() == expected
    assert h.read_open_threads().encode() == expected


def test_read_threads_paging_every_title_once_with_byte_budget(repo):
    for i in range(90):
        title = f'Topic {i:03} ' + ('é日本' * (12 + i % 7))
        t.open_thread(repo, title, 'Invented.')
        # Includes ties, whose title ordering must remain deterministic.
        t.update_thread(repo, title, 'Update.', '', 152 + i // 3, '2026-10-11')
    expected = [item['title'] for item in t.summaries(repo)]
    start, seen, pages = 1, [], []
    while True:
        answer = h.read_threads(start=start)
        assert h.read_threads(start=start) == answer
        assert len(answer.encode()) <= 6000
        shown = [line.split(' — ', 1)[0] for line in answer.splitlines() if ' bytes.' in line]
        assert shown
        seen.extend(shown)
        pages.append(answer)
        last = start + len(shown) - 1
        if start > 1:
            assert answer.splitlines()[0] == f'Open threads {start} to {last} of 90.'
        else:
            assert not answer.startswith('Open threads ')
        if last == 90:
            assert 'more open threads are not shown' not in answer
            break
        assert answer.endswith(f'{90-last} more open threads are not shown; the next are at read_threads(start={last+1}).')
        call = ast.parse(answer.rsplit(' at ', 1)[1][:-1], mode='eval').body
        start = ast.literal_eval(call.keywords[0].value)
        assert len(pages) < 90
    assert seen == expected and len(set(seen)) == 90
    assert len(pages) > 1
    assert h.read_threads() == pages[0] == h.read_open_threads()


@pytest.mark.parametrize('start', [0, 2, 1.5, '1', None, True])
def test_read_threads_bad_start_refuses_without_change(repo, start):
    t.open_thread(repo, 'Amber', 'Invented.')
    stable = snapshot(repo)
    answer = h.read_threads(start=start)
    assert answer == t.refusal('read_threads', t.settings(repo)['errors']['start'])
    assert answer.endswith('read_threads(start=1).')
    assert snapshot(repo) == stable


def test_read_threads_empty_default_unchanged(repo):
    assert h.read_threads() == h.read_threads(start=1) == 'Closed threads: 0.'
    stable = snapshot(repo)
    assert 'REFUSED' in h.read_threads(start=2)
    assert snapshot(repo) == stable


@pytest.mark.parametrize('tool,kwargs', [
    ('update_thread', {'update': 'Invented update.'}),
    ('close_thread', {'how_it_closed': 'Settled.'}),
    ('read_thread', {}),
])
@pytest.mark.parametrize('query', ['Café', 'a café discussion with "amber" \\ and 日本'])
def test_one_title_hint_is_exact_callable_and_never_writes(repo, tool, kwargs, query):
    title = 'A café discussion with "Amber" \\ and 日本'
    h.open_thread(title=title, stands='Invented.')
    stable = snapshot(repo)
    answer = getattr(h, tool)(title=query, **kwargs)
    assert 'no open thread has that exact title; one open title contains it: ' + json.dumps(title, ensure_ascii=False) in answer
    assert 'the file name' not in answer
    assert snapshot(repo) == stable
    result = run_shown_call(answer)
    assert 'REFUSED' not in result
    if tool == 'read_thread':
        assert result.startswith('# ' + title)
        assert snapshot(repo) == stable


@pytest.mark.parametrize('count', [3, 8])
@pytest.mark.parametrize('tool,kwargs', [
    ('update_thread', {'update': 'New.'}),
    ('close_thread', {'how_it_closed': 'Settled.'}),
    ('read_thread', {}),
])
def test_multiple_title_hints_sorted_and_capped(repo, count, tool, kwargs):
    for i in range(count):
        title = f'Question café {i}'
        t.open_thread(repo, title, 'Invented.')
        t.update_thread(repo, title, 'Update.', '', 152 + i, '2026-10-11')
    stable = snapshot(repo)
    answer = getattr(h, tool)(title='CAFÉ', **kwargs)
    assert f'{count} open titles contain it: ' in answer
    expected = ', '.join(json.dumps(f'Question café {i}', ensure_ascii=False) for i in range(count-1, max(-1, count-6), -1))
    assert expected in answer
    if count > 5:
        assert '; 3 more are not named' in answer
        assert '"Question café 2"' not in answer
    else:
        assert 'more are not named' not in answer
    assert answer.endswith('; call ' + t.CALLS[tool] + '.')
    assert snapshot(repo) == stable


@pytest.mark.parametrize('selector', ['', 'SR-0152'])
def test_closed_title_hint_and_open_priority(repo, selector):
    title = 'Settled café question'
    h.open_thread(title=title, stands='Invented.')
    h.close_thread(title=title, how_it_closed='Settled.')
    stable = snapshot(repo)
    answer = h.read_thread(title='CAFÉ', closed_in=selector)
    assert 'no closed thread has that exact title; one closed title contains it: ' in answer
    assert snapshot(repo) == stable
    assert run_shown_call(answer).startswith('# ' + title)
    h.open_thread(title='Open café question', stands='Invented.')
    stable = snapshot(repo)
    answer = h.read_thread(title='CAFÉ')
    assert 'one open title contains it: "Open café question"' in answer
    assert snapshot(repo) == stable


def test_closed_title_hints_deduplicate_and_limit(repo):
    for i in range(7):
        title = f'Closed café {i}'
        for closing in range(2):
            t.open_thread(repo, title, 'Invented.')
            t.update_thread(repo, title, 'Update.', '', 152+i, '2026-10-11')
            t.close_thread(repo, title, 'Settled.', 160, '2026-10-11')
    stable = snapshot(repo)
    answer = h.read_thread(title='CAFÉ')
    assert '7 closed titles contain it: "Closed café 6", "Closed café 5"' in answer
    assert '; 2 more are not named' in answer
    assert snapshot(repo) == stable
    # The cap is read from settings, not hard-coded.
    cfg = json.loads((repo / 'config/cards.json').read_bytes())
    cfg['threads']['suggestion_limit'] = 2
    write(repo / 'config/cards.json', json.dumps(cfg))
    assert '; 5 more are not named' in h.read_thread(title='CAFÉ')


@pytest.mark.parametrize('count', [2, 3])
def test_each_same_wake_closing_has_its_own_selector(repo, count):
    copies = []
    for i in range(count):
        h.open_thread(title='Amber', stands=f'Opening {i+1}.')
        h.close_thread(title='Amber', how_it_closed=f'Closing {i+1}.')
        selector = 'SR-0152' + (f'-{i+1}' if i else '')
        copies.append((selector, (t.folder(repo) / 'closed' / f'amber-{selector}.md').read_bytes()))
    for selector, body in copies:
        assert h.read_thread(title='Amber', closed_in=selector).encode() == body
    earlier = ', '.join(selector for selector, _ in reversed(copies[:-1]))
    assert h.read_thread(title='Amber').encode() == copies[-1][1] + ('\n\nEarlier closings: ' + earlier + '.').encode()
    for selector in ['152', 'SR-0152-1', 'SR-0152-0', 'SR-0152-two']:
        stable = snapshot(repo)
        answer = h.read_thread(title='Amber', closed_in=selector)
        assert 'such as SR-0152 or SR-0152-2' in answer
        assert answer.endswith('; call read_thread(title="<title>", closed_in="").')
        assert snapshot(repo) == stable


def test_help_describes_pages_and_individual_closings(repo):
    index_help = h.help(tool='read_threads')
    thread_help = h.help(tool='read_thread')
    assert t.settings(repo)['read_threads_description'] in index_help
    assert 'start = 1' in index_help and 'Example: read_threads(start=1)' in index_help
    assert t.settings(repo)['read_thread_description'] in thread_help
    assert "closed_in='SR-0152-2'" in thread_help
    for answer in [index_help, thread_help]:
        call = ast.parse(answer.split('Example: ')[1], mode='eval').body
        kwargs = {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords}
        inspect.signature(getattr(h, call.func.id)).bind(**kwargs)


def test_mcp_bad_start_returns_plain_refusal(repo):
    t.open_thread(repo, 'Amber', 'Invented.')
    stable = snapshot(repo)
    for value in [0, 2, 1.5, 'one', None, True]:
        content = asyncio.run(h.mcp.call_tool('read_threads', {'start': value}))
        # FastMCP versions return either content or (content, structured result).
        blocks = content[0] if isinstance(content, tuple) else content
        assert any('read_threads REFUSED:' in block.text for block in blocks)
        assert snapshot(repo) == stable


@pytest.mark.parametrize('tool,kwargs', [
    ('update_thread', {'update': 'New.'}),
    ('close_thread', {'how_it_closed': 'Settled.'}),
])
def test_no_containing_title_preserves_previous_collision_refusal(repo, tool, kwargs):
    h.open_thread(title='Am ber', stands='Invented.')
    stable = snapshot(repo)
    answer = getattr(h, tool)(title='Am-ber', **kwargs)
    assert answer == t.refusal(tool, 'the file name am-ber.md belongs to "Am ber"')
    assert snapshot(repo) == stable
