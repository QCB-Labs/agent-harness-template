"""Brief 24: archived thread entries are cards that recall returns with history."""
from naming_support import fixture_name
import datetime
import json
from pathlib import Path

import pytest
from tools import cards as c, harness_mcp as h, threads as t

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.datetime(2026, 10, 12, 12, tzinfo=datetime.timezone.utc)
LONG = 'y' * 180


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode('utf-8'))


def entry(title, place, eol):
    lines = ['- **' + title + '**:', '  - Context: resident #260 at Place ' + str(place) + ', café 日本.',
             '  - Status: Done.', '']
    return eol.join(line.encode('utf-8') for line in lines) + eol


def heading(date, eol):
    text = t.settings(ROOT)['archive_heading'].format(date=date)
    return text.replace('\n', eol.decode()).encode('utf-8')


def archive_bytes(eol=b'\n'):
    first = [entry('Settled amber', 9001, eol), entry('Café 日本 closed', 9002, eol), entry(LONG, 9003, eol)]
    return heading('2026-10-11', eol) + b''.join(first) + heading('2026-10-12', eol) + entry('Later one', 9004, eol)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(h, 'REPO', tmp_path)
    monkeypatch.setattr(h, '_cards_patterns', lambda: tmp_path / 'config/tag-patterns.city.json')
    monkeypatch.setattr(h, '_utc_now', lambda: NOW)
    naming = json.loads((ROOT / 'naming.json').read_bytes())
    # Only invented sources are configured, and no real source is consulted.
    naming['paths'] = {key: value for key, value in naming['paths'].items()
                      if key in {'identity', 'governance', 'threads', 'threads_archive', 'open_threads',
                                 'goal_files', 'projects', 'journal'}}
    naming['indexes'] = {}
    write(tmp_path / 'naming.json', json.dumps(naming))
    for name in ['cards.json', 'tag-patterns.city.json','tag-patterns.generic.json']:
        write(tmp_path / 'config' / name, (ROOT / 'config' / name).read_bytes())
    write(tmp_path / fixture_name('PERSONNEL-FILE.md'), '# Invented identity\n')
    write(tmp_path / fixture_name('STANDING-ORDERS.md'), '# Invented governance\n')
    write(tmp_path / 'budget.json', b'{"wake_count":151}')
    write(tmp_path / fixture_name('records/shift-reports/SR-0151.md'), fixture_name(b'# Invented log SR-0151\n\n## What I plan next\nCount spoons.\n'))
    return tmp_path


def store(repo):
    return c.CardStore(repo / fixture_name('records'), repo / 'config/tag-patterns.city.json')


def recut(repo, logical='threads_archive'):
    return c.recut_all(repo, repo / 'naming.json', repo / 'config/tag-patterns.city.json', logical)


def archived(repo):
    return [card for card in store(repo)._current_state().values()
            if (card.get('origin') or {}).get('logical') == 'threads_archive']


def recall(repo, **kwargs):
    return c.recall(repo / fixture_name('records'), config_path=repo / 'config/cards.json', record_read=False, **kwargs)


def reads(repo):
    wake = c.read_wake_card(repo, repo / 'naming.json', config_path=repo / 'config/cards.json')
    return wake['text'], h.read_threads(), h.read_open_threads()


@pytest.mark.parametrize('eol', [b'\n', b'\r\n'])
def test_each_entry_is_one_finished_card_with_exact_bytes(repo, eol):
    data = archive_bytes(eol)
    path = repo / fixture_name('records/open-matters-archive.md')
    write(path, data)
    counts = recut(repo)
    assert counts['files'] == 1 and counts['closed'] == 0 and counts['changed'] == 0
    entries = [card for card in archived(repo) if card['status'] == 'done']
    assert [card['text'].encode('utf-8') for card in sorted(entries, key=lambda card: card['origin']['order'])] \
        == [item['data'] for item in t.entries(data)]
    assert [card['title'] for card in sorted(entries, key=lambda card: card['origin']['order'])] \
        == ['Settled amber', 'Café 日本 closed', LONG, 'Later one']
    assert all(card['kind'] == 'thread' for card in entries)
    others = [card for card in archived(repo) if card['status'] != 'done']
    assert others and all(card['status'] == 'structure' and '## Moved by the operator' in card['text']
                          or not card['text'].strip() for card in others)
    assert not any(card['status'] in {'live', 'recheck'} for card in archived(repo))
    generated = repo / 'generated'
    c.generate_views(store(repo).cards_path, generated)
    assert (generated / fixture_name('records/open-matters-archive.md')).read_bytes() == data


def test_recall_returns_archived_entries_only_with_history(repo):
    asked = dict(tags=['place:9001'], kinds=['thread'])
    without_archive = recall(repo, **asked)
    with_history_before = recall(repo, history=True, **asked)
    write(repo / fixture_name('records/open-matters-archive.md'), archive_bytes())
    recut(repo)
    assert recall(repo, **asked) == without_archive
    assert not without_archive['cards'] and not with_history_before['cards']
    found = recall(repo, history=True, **asked)['cards']
    assert len(found) == 1 and found[0]['title'] == 'Settled amber' and found[0]['status'] == 'done'
    assert found[0]['text'].encode('utf-8') == entry('Settled amber', 9001, b'\n')
    assert c.read_card(repo / fixture_name('records'), found[0]['id'])['card'] == found[0]
    # Without a kind, an ordinary recall still leaves the archive out.
    assert not [card for card in recall(repo, tags=['place:9001'])['cards']
                if (card.get('origin') or {}).get('logical') == 'threads_archive']
    # The pieces that hold the file's shape are never returned.
    everything = recall(repo, tags=[], kinds=['thread'], history=True, limit=50)['cards']
    assert all(card['status'] == 'done' for card in everything)


def test_archived_entry_and_closed_thread_file_are_both_returned(repo):
    write(repo / fixture_name('records/open-matters-archive.md'), archive_bytes())
    recut(repo)
    h.open_thread(title='Amber again', stands='Resident #260 at Place 9001.')
    h.close_thread(title='Amber again', how_it_closed='Settled.')
    found = recall(repo, tags=['place:9001'], kinds=['thread'], history=True)['cards']
    logicals = sorted((card['origin']['logical'] for card in found))
    assert logicals == ['threads', 'threads_archive']
    assert not recall(repo, tags=['place:9001'], kinds=['thread'])['cards']


def test_other_reads_are_unchanged_by_the_archive(repo):
    write(repo / fixture_name('records/open-matters.md'), fixture_name(b'# Open Matters\n\n## Living threads\n\n- **Visit amber**\n  - Status: Pending at Place 823.\n\n'))
    recut(repo, 'open_threads')
    before_legacy = reads(repo)
    write(repo / fixture_name('records/open-matters-archive.md'), archive_bytes())
    recut(repo)
    assert reads(repo) == before_legacy
    (repo / fixture_name('records/open-matters-archive.md')).unlink()
    recut(repo)
    h.open_thread(title='Question nine', stands='Resident #260 at Place 823.')
    h.open_thread(title='Question ten', stands='Still open.')
    h.close_thread(title='Question ten', how_it_closed='Answered.')
    before = reads(repo) + (h.read_thread(title='Question nine'), h.read_thread(title='Question ten'))
    write(repo / fixture_name('records/open-matters-archive.md'), archive_bytes())
    recut(repo)
    assert reads(repo) + (h.read_thread(title='Question nine'), h.read_thread(title='Question ten')) == before
    assert 'Closed threads: 1.' in h.read_threads()
    assert 'no open or closed thread has that title' in h.read_thread(title='Settled amber')


def test_recut_twice_then_append_then_no_file(repo):
    assert recut(repo) == {'files': 0, 'made': 0, 'closed': 0, 'changed': 0, 'left_alone': 0}
    assert not store(repo).cards_path.exists() or not archived(repo)
    path = repo / fixture_name('records/open-matters-archive.md')
    write(path, archive_bytes())
    first = recut(repo)
    stable = store(repo).cards_path.read_bytes()
    ids = {card['id']: card for card in archived(repo)}
    second = recut(repo)
    assert second['made'] == second['changed'] == second['closed'] == 0 and second['left_alone'] == first['made']
    assert store(repo).cards_path.read_bytes() == stable
    write(path, archive_bytes() + entry('Appended later', 9005, b'\n'))
    third = recut(repo)
    assert third['made'] == 1 and third['changed'] == 0 and third['closed'] == 0
    now = {card['id']: card for card in archived(repo)}
    assert all(now[key] == value for key, value in ids.items())
    assert len(recall(repo, tags=['place:9005'], kinds=['thread'], history=True)['cards']) == 1
    path.unlink()
    gone = recut(repo)
    assert gone['closed'] == len(now)
    assert not recall(repo, tags=['place:9001'], kinds=['thread'], history=True)['cards']
