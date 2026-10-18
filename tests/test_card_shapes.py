"""Invented structural memory cases; all writes stay under tmp_path."""
from collections import Counter
import json
import shutil
from pathlib import Path

import pytest
from tools import cards as c

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/cards-shapes'
CITY = ROOT / 'config/tag-patterns.city.json'
KINDS = ('lessons', 'city_rules', 'open_threads', 'goals', 'journal', 'residents')
MULTI = KINDS[:4]
FILES = {'lessons': 'memory/lessons.md', 'city_rules': 'memory/city-rules.md',
         'open_threads': 'memory/open-threads.md', 'goals': 'goals.md',
         'journal': 'memory/mission_log/W10000.md',
         'residents': 'memory/residents/velvetspoon.md'}

@pytest.fixture
def memory(tmp_path):
    source = tmp_path / 'source'
    shutil.copytree(FIXTURE, source)
    migrated = c.migrate(source, source / 'naming.json', CITY, output_root=source / 'memory')
    return source, migrated

def state(source):
    return c.CardStore(source / 'memory', CITY)._current_state()

def recut(source, kind, path):
    return c.recut_file(source, source / 'naming.json', CITY, kind, path)

def filed(source, kind):
    path = source / FILES[kind]
    entries = sorted((card for card in state(source).values()
                      if card['origin']['file'] == FILES[kind]
                      and card.get('status_reason') != 'removed from source file'),
                     key=lambda card: card['origin']['order'])
    return path, entries

def replace(path, old, new):
    raw = path.read_bytes()
    assert raw.count(old.encode()) == 1
    path.write_bytes(raw.replace(old.encode(), new.encode(), 1))

def write_entries(path, entries):
    path.write_bytes(''.join(card['text'] for card in entries).encode())

def target(entries, kind):
    if kind == 'lessons':
        return next(card for card in entries if '1. お茶' in card['text'])
    if kind == 'journal':
        return entries[0]
    if kind == 'residents':
        return entries[-1]
    return next(card for card in entries if 'Middle' in card['text'])

def inserted(kind):
    return {'text': {'lessons': '## 2080-01-03-1000\r\n\r\nInserted amber lesson.\r\n\r\n',
                    'city_rules': '- **Inserted rule** Keep the amber ribbon.\r\n\r\n',
                    'open_threads': '- **Inserted spool** Find the amber ribbon.\r\n\r\n',
                    'goals': '## Inserted paper goal\r\n\r\nFold amber paper.\r\n\r\n'}[kind]}

@pytest.mark.parametrize('kind', KINDS)
def test_cut_and_rebuild_every_byte(memory, tmp_path, kind):
    source, migrated = memory
    generated = tmp_path / 'generated'
    c.generate_views(source / 'memory/cards/cards.jsonl', generated)
    names = {card['origin']['file'] for card in migrated if card['origin']['logical'] == kind}
    assert names
    for name in names:
        assert (generated / name).read_bytes() == (source / name).read_bytes(), name

@pytest.mark.parametrize('kind', KINDS)
def test_nonblank_line_occurrences_are_preserved_once(memory, kind):
    source, migrated = memory
    entries = [card for card in migrated if card['origin']['logical'] == kind]
    for name in {card['origin']['file'] for card in entries}:
        expected = Counter(line for line in (source / name).read_bytes().decode().splitlines() if line.strip())
        actual = Counter(line for card in entries if card['origin']['file'] == name
                         for line in card['text'].splitlines() if line.strip())
        # Count occurrences: deliberately duplicated empty headings must occur twice.
        assert actual == expected, name

def test_resident_index_is_not_cut_into_cards(memory):
    # By design: the index is the name list the tagger reads, not memory.
    _, migrated = memory
    assert not [card for card in migrated if card['origin']['file'] == 'memory/residents/index.md']

@pytest.mark.parametrize('kind', KINDS)
def test_middle_edit_changes_only_one_card(memory, kind):
    source, _ = memory
    path, entries = filed(source, kind)
    chosen = target(entries, kind)
    before = state(source)
    lines = chosen['text'].splitlines(keepends=True)
    index = min((i for i, line in enumerate(lines) if line.strip() and not line.startswith('#')),
                key=lambda i: abs(i - len(lines)//2))
    lines[index] = lines[index].rstrip('\r\n') + ' Added amber ribbon.\r\n'
    edited = ''.join(lines)
    replace(path, chosen['text'], edited)
    result = recut(source, kind, path)
    after = state(source)
    assert set(after) == set(before)
    assert {key for key in before if before[key]['text'] != after[key]['text']} == {chosen['id']}
    assert result['changed'] == [chosen['id']] and not result['new'] and not result['removed']
    assert after[chosen['id']]['text'] == edited

@pytest.mark.parametrize('kind', KINDS)
def test_insert_then_edit_keeps_the_entry_id(memory, kind):
    source, _ = memory
    path, entries = filed(source, kind)
    chosen = target(entries, kind)
    at = entries.index(chosen)
    old = state(source)
    if kind in MULTI:
        write_entries(path, entries[:at] + [inserted(kind)] + entries[at:])
        insertion_path = path
    else:
        # Insert an earlier sibling file without changing existing keyed pieces.
        insertion_path = path.parent / ('W8000.md' if kind == 'journal' else 'amberbird.md')
        insertion_path.write_bytes(b'# Invented amber entry\r\n\r\nAn amber ribbon waits.\r\n')
    result = recut(source, kind, insertion_path)
    middle = state(source)
    assert len(result['new']) == 1 and not result['removed'] and not result['changed']
    assert set(old) < set(middle)
    assert all(middle[key]['text'] == value['text'] for key, value in old.items())
    edited = chosen['text'] + 'Amber edit stays with this entry.\r\n'
    replace(path, chosen['text'], edited)
    result = recut(source, kind, path)
    after = state(source)
    assert set(after) == set(middle)
    assert result['changed'] == [chosen['id']]
    assert {key for key in middle if middle[key]['text'] != after[key]['text']} == {chosen['id']}
    assert after[chosen['id']]['text'] == edited

@pytest.mark.parametrize('kind', MULTI)
@pytest.mark.xfail(strict=True, reason='One-pass remove plus neighbour edit pairs replacement text with the removed card ID.')
def test_remove_and_edit_in_one_pass_preserves_ownership(memory, kind):
    _remove_edit(memory, kind, two_passes=False)

@pytest.mark.parametrize('kind', MULTI)
def test_remove_then_edit_in_two_passes_preserves_ownership(memory, kind):
    _remove_edit(memory, kind, two_passes=True)

def _remove_edit(memory, kind, two_passes):
    source, _ = memory
    path, entries = filed(source, kind)
    neighbour = target(entries, kind)
    at = entries.index(neighbour)
    removed = entries[at - 1]
    remaining = entries[:at - 1] + entries[at:]
    if two_passes:
        write_entries(path, remaining)
        result = recut(source, kind, path)
        assert result['removed'] == [removed['id']]
        assert state(source)[neighbour['id']]['text'] == neighbour['text']
    edited = neighbour['text'] + 'Neighbour keeps the amber ribbon.\r\n'
    remaining = [dict(card, text=edited) if card['id'] == neighbour['id'] else card for card in remaining]
    write_entries(path, remaining)
    recut(source, kind, path)
    after = state(source)
    evidence = {key: {'status': value['status'], 'first_line': value['text'].splitlines()[0]}
                for key, value in after.items() if value['origin']['file'] == FILES[kind]}
    assert after[removed['id']].get('status_reason') == 'removed from source file', evidence
    assert after[removed['id']]['text'] == removed['text'], evidence
    assert after[neighbour['id']]['text'] == edited, evidence
    for card in entries:
        if card['id'] not in (removed['id'], neighbour['id']):
            assert after[card['id']]['text'] == card['text'], evidence

@pytest.mark.parametrize('kind', ('lessons', 'goals', 'journal', 'residents'))
def test_nonascii_recall_uses_utf8_bytes(memory, tmp_path, kind):
    source, migrated = memory
    chosen = next(card for card in migrated if card['origin']['logical'] == kind
                  and any('\u0400' <= char <= '\u04ff' for char in card['text']))
    needle = next(line for line in chosen['text'].splitlines()
                  if any('\u0400' <= char <= '\u04ff' for char in line))
    cost = len(chosen['text'].encode())
    config = tmp_path / 'budget.json'
    config.write_text(json.dumps({'recall_bytes': cost - 1, 'wake_sections': {}}))
    kwargs = dict(tags=[], text=needle, kinds=[chosen['kind']], config_path=config, limit=100)
    result = c.recall(source / 'memory', **kwargs)
    omitted = next(item for item in result['left_out_cards'] if item['id'] == chosen['id'])
    assert omitted['bytes'] == cost
    assert chosen['id'] not in {card['id'] for card in result['cards']}
    config.write_text(json.dumps({'recall_bytes': 100000, 'wake_sections': {}}))
    result = c.recall(source / 'memory', **kwargs)
    assert chosen['id'] in {card['id'] for card in result['cards']}
    assert result['bytes'] == sum(len(card['text'].encode()) for card in result['cards'])

NAMES = ['Who I am', 'What I was doing', 'What I owe', 'Open threads (index)',
         'Rules', 'What needs checking', "What's here"]
def bodies(result):
    text = result['text']
    starts = [text.index('## '+name+'\n\n') for name in NAMES]
    return {name: text[start+len('## '+name+'\n\n'):starts[i+1]-2 if i+1<len(starts) else len(text)]
            for i,(name,start) in enumerate(zip(NAMES,starts))}

@pytest.mark.parametrize('tight', [False, True])
def test_wake_limit_whole_cards_and_omission_counts(memory, tmp_path, tight):
    source, _ = memory
    config = json.loads((ROOT / 'config/cards.json').read_text())
    config_path = tmp_path / 'wake-config.json'
    if tight:
        config['wake_sections'] = {key: 50000 for key in config['wake_sections']}
        config_path.write_text(json.dumps(config))
        full = c.read_wake_card(source, source / 'naming.json', config_path=config_path, here_tags=['resident:20000'])
        config['wake_card_bytes'] = full['total_bytes'] - 1000
    config_path.write_text(json.dumps(config))
    result = c.read_wake_card(source, source / 'naming.json', config_path=config_path, here_tags=['resident:20000'])
    assert result['total_bytes'] == len(result['text'].encode()) <= config['wake_card_bytes']
    if tight:
        assert result['dropped_cards'] > 0
    sections = bodies(result)
    assert [item['name'] for item in result['sections']] == NAMES
    for name in ('identity.md', 'governance.md'):
        assert (source / name).read_bytes().decode() in sections['Who I am']
    for info in result['sections']:
        body = sections[info['name']]
        assert info['bytes'] == len(body.encode()), info['name']
        if info['left_out']:
            assert f"{info['left_out']} more " in body
    current = list(state(source).values())
    expected = {
        'What I was doing': [card for card in current if card['kind'] == 'goal' and card['status'] == 'live'] +
          [max((card for card in current if card['kind'] == 'log'), key=c._log_number)],
        'Rules': [card for card in current if card['kind'] == 'rule' and card['status'] == 'live'],
        'What needs checking': [card for card in current if card['status'] == 'recheck'],
        "What's here": [card for card in current if card['kind'] in ('thread','relationship') and card['status']=='live' and card.get('role', 'current') == 'current' and 'resident:20000' in card['tags']]}
    for name, candidates in expected.items():
        body = sections[name]
        shown = 0
        for card in candidates:
            if card['text'] in body:
                shown += 1
            else:
                # Unique body lines detect partial inclusion without mistaking shared headings.
                unique = [line for line in card['text'].splitlines() if line.strip() and
                          sum(line in other['text'].splitlines() for other in current) == 1]
                assert all(line not in body for line in unique), (name,card['id'])
        info = next(item for item in result['sections'] if item['name'] == name)
        assert info['shown'] == shown
        assert info['left_out'] == len(candidates) - shown
    threads = [card for card in current if card['kind']=='thread' and card['status']=='live']
    info = next(item for item in result['sections'] if item['name']=='Open threads (index)')
    listed = sum(f"- {card['id']}: {card['title']} [" in sections['Open threads (index)'] for card in threads)
    assert listed == info['shown'] and info['left_out'] == len(threads)-listed

PLAN_LOG = ('# Invented log W60000\r\n\r\nPreamble about a kettle.\r\n\r\n'
            '## What happened\r\n\r\nCounted spoons.\r\n\r\n'
            '## What I learned, and what I plan next\r\n\r\nSpoons come in sets.\r\n\r\n'
            '### What I plan next\r\n\r\n- Visit the amber shelf.\r\n\r\n'
            '## What is not owed\r\n\r\nNo reply to the kettle.\r\n\r\n'
            '## Moved here by the operator\r\n\r\nAn older note.\r\n')

def test_wake_fallback_shows_the_newest_logs_plan(tmp_path):
    source = tmp_path / 'source'
    shutil.copytree(FIXTURE, source)
    (source / 'memory/mission_log/W60000.md').write_bytes(PLAN_LOG.encode())
    migrated = c.migrate(source, source / 'naming.json', CITY, output_root=source / 'memory')
    # Re-cutting an older log moves its updated_at; it must not become "newest".
    older = next(card for card in migrated if card['origin']['file'] == FILES['journal'])
    c.CardStore(source / 'memory', CITY, clock=lambda: '2090-01-01T00:00:00Z').append_version(older)
    doing = bodies(c.read_wake_card(source, source / 'naming.json', config_path=ROOT / 'config/cards.json'))['What I was doing']
    assert 'W60000 · What I learned, and what I plan next' in doing
    assert 'Spoons come in sets.' in doing and '- Visit the amber shelf.' in doing
    for absent in ('Preamble about a kettle', 'Counted spoons', 'No reply to the kettle', 'An older note', older['text']):
        assert absent not in doing

def test_plan_section_returns_a_log_with_no_plan_heading_whole():
    text = 'Prose before any heading.\r\n\r\n## Only heading\r\n\r\n- a bullet\r\n'
    assert c._plan_section(text) == text


def _recall_store(tmp_path, cards):
    folder = tmp_path / 'memory/cards'
    folder.mkdir(parents=True)
    lines = [json.dumps(dict(status='live', weight=0, title='', links=[], **card)) for card in cards]
    (folder / 'cards.jsonl').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return tmp_path / 'memory'

def test_recall_puts_the_person_and_the_thread_before_logs(tmp_path):
    tags = ['place:9000', 'resident:20000']
    def card(card_id, kind, name, size, updated):
        return dict(id=card_id, kind=kind, tags=list(tags), text=f'{card_id} ' + 'x' * size,
                    updated_at=updated, created_at=updated, origin={'file': name, 'logical': kind, 'order': 0})
    # The big log is the most recently touched, as after an operator re-cut.
    memory_root = _recall_store(tmp_path, [
        card('log-0001', 'log', 'memory/mission_log/W10000.md', 14000, '2090-01-03T00:00:00Z'),
        card('relationship-0001', 'relationship', 'memory/residents/velvetspoon.md', 2300, '2080-01-01T00:00:00Z'),
        card('thread-0001', 'thread', 'memory/open-threads.md', 5000, '2080-01-02T00:00:00Z'),
        card('reflection-0001', 'reflection', 'memory/lessons.md', 600, '2080-01-01T00:00:00Z'),
    ])
    result = c.recall(memory_root, tags=tags, config_path=ROOT / 'config/cards.json')
    assert [card['id'] for card in result['cards']] == ['thread-0001', 'relationship-0001', 'reflection-0001']
    assert [entry['id'] for entry in result['left_out_cards']] == ['log-0001']

def test_recall_orders_logs_by_wake_number_not_by_last_touch(tmp_path):
    def log(card_id, name, updated):
        return dict(id=card_id, kind='log', tags=['place:9000'], text=f'{card_id} ' + 'x' * 9000,
                    updated_at=updated, created_at=updated, origin={'file': name, 'logical': 'journal', 'order': 0})
    memory_root = _recall_store(tmp_path, [
        log('log-0001', 'memory/mission_log/W9000.md', '2090-01-03T00:00:00Z'),
        log('log-0002', 'memory/mission_log/W10000.md', '2080-01-01T00:00:00Z'),
    ])
    result = c.recall(memory_root, tags=['place:9000'], config_path=ROOT / 'config/cards.json')
    assert [card['id'] for card in result['cards']] == ['log-0002']
    assert [entry['id'] for entry in result['left_out_cards']] == ['log-0001']

def test_recall_long_log_returns_short_match_and_describes_omission(memory):
    source, migrated = memory
    long_log = next(card for card in migrated if card['origin']['file'].endswith('W50000.md'))
    short_log = next(card for card in migrated if card['origin']['file'].endswith('W9000.md'))
    result = c.recall(source / 'memory', tags=[], text='lanternquery', kinds=['log'], config_path=ROOT / 'config/cards.json')
    assert result['cards'] == [short_log] and result['matched'] == 2 and result['left_out'] == 1
    expected = {'id':long_log['id'], 'kind':'log', 'bytes':len(long_log['text'].encode()),
                'title':'W50000 · header', 'role':'history'}
    assert result['left_out_cards'] == [expected]
    assert f"(log, history, {expected['bytes']} bytes): {expected['title']}" in result['message']
    assert long_log['text'] not in result['message']


def test_city_patterns_extract_invented_tags(memory):
    source, migrated = memory
    lesson = next(card for card in migrated if 'Thing #8000' in card['text'])
    assert {'place:9000','thing:8000','wake:w9000'} <= set(lesson['tags'])
    thread = next(card for card in migrated if 'Places 9000 & 10000' in card['text'])
    assert {'place:9000','place:10000','wake:w9000'} <= set(thread['tags'])
    dossier = next(card for card in migrated if card['origin']['file']==FILES['residents'])
    assert {'resident:20000','name:velvetspoon'} <= set(dossier['tags'])
    tags = c.extract_tags(kind='thread', text='Vélvetspoon #20000 moved through routing [9000, 10000] on W9000.',
                          patterns=c.load_patterns(CITY), name_index=source / 'memory/residents/index.md')
    assert tags == ['kind:thread','name:velvetspoon','place:10000','place:9000','resident:20000','wake:w9000']


@pytest.mark.parametrize('filename,number', [
    ('SR-0135-2080-01-01.md', 135), ('W135-2080-01-01.md', 135),
    ('LOG-0135.md', 135), ('0135-2080-01-01.md', -1), ('SR-no-number.md', -1),
])
def test_log_number_accepts_letter_prefixes_and_rejects_unprefixed_dates(filename, number):
    assert c._log_number({'origin': {'file': 'invented/' + filename}}) == number


def test_wake_fallback_ignores_removed_logs(tmp_path):
    source = tmp_path / 'source'
    shutil.copytree(FIXTURE, source)
    migrated = c.migrate(source, source / 'naming.json', CITY, output_root=source / 'memory')
    store = c.CardStore(source / 'memory', CITY)
    newest = max((card for card in migrated if card['kind'] == 'log'), key=c._log_number)
    store.append_version(dict(newest, status='done', status_reason='removed from source file'))
    doing = bodies(c.read_wake_card(source, source / 'naming.json', config_path=ROOT / 'config/cards.json'))['What I was doing']
    live = max((card for card in migrated if card['kind'] == 'log' and card['id'] != newest['id']), key=c._log_number)
    assert live['text'] in doing and newest['text'] not in doing
