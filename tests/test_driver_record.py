"""The model footer: counted from an invented driver record, never from the model's own word."""
from naming_support import fixture_name
from contextlib import closing
import datetime
import json
import os
from pathlib import Path
import sqlite3

import pytest
from tools import driver_record as d, harness_mcp as h, names

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.datetime(2026, 10, 12, 12, tzinfo=datetime.timezone.utc)


def call(name, noise=b''):
    # One model call as the driver stores it: other fields, then the name
    # with its length before it, then the next field.
    return b'\x0a\x05abcde' + noise + b'\x9a\x01' + bytes([len(name)]) + name.encode() + b'\xa2\x01\x14\x0a\x0bused_tokens'


def record(path, folder_uri, names, noise=b''):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute('create table trajectory_metadata_blob (id text, data blob)')
    con.execute('create table gen_metadata (idx integer, data blob, size integer)')
    uri = folder_uri.encode()
    con.execute('insert into trajectory_metadata_blob values (?, ?)',
                ('main', b'\x0a\x03xyz\x12' + bytes([len(uri)]) + uri + b'R\x04more'))
    for i, name in enumerate(names):
        con.execute('insert into gen_metadata values (?, ?, 0)', (i, call(name, noise if i == len(names) - 1 else b'')))
    con.commit()
    con.close()


def uri(repo, encoded=False):
    text = repo.resolve().as_uri()
    return text.replace(':', '%3A').replace('file%3A', 'file:', 1) if encoded else text


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / 'agent'
    root.mkdir()
    monkeypatch.setattr(h, 'REPO', root)
    monkeypatch.setattr(h, '_utc_now', lambda: NOW)
    (root / 'budget.json').write_text(json.dumps({'wake_count': 150}), encoding='utf-8')
    cfg = {"enabled":True,"glob":"*.db","newer_than_minutes":180,"workspace_table":"trajectory_metadata_blob","workspace_column":"data","table":"gen_metadata","column":"data","order":"idx","model_pattern":"\\x9a\\x01(.)([a-zA-Z0-9._-]+)\\xa2\\x01","model_item":"{label} ({calls})","joiner":", ","footer":"\n\nMeasured models: {models}; {total} calls.\n","labels":{"fixture-model-a":"Fixture Model A"}}
    cfg['folder'] = str(tmp_path / 'driver')
    (root / 'config').mkdir()
    (root / d.SETTINGS).write_text(json.dumps(cfg), encoding='utf-8')
    return root


def driver(repo):
    return repo.parent / 'driver'


def written(repo):
    reply = h.write_journal('# Invented log\n\n## What I Plan Next\nCount spoons.\n').splitlines()[0]
    return (repo / fixture_name('records/shift-reports') / reply).read_text(encoding='utf-8')


def test_footer_counts_each_model_in_order_of_first_use(repo):
    record(driver(repo) / 'a.db', uri(repo), ['fixture-model-a'] * 5 + ['fixture-model-b'] * 2)
    assert d.models_used(repo) == [('fixture-model-a', 5), ('fixture-model-b', 2)]
    cfg = json.loads((repo / d.SETTINGS).read_bytes())
    # A name the operator has labelled is shown in her words; another is shown as the driver wrote it.
    assert cfg['labels'] == {'fixture-model-a': 'Fixture Model A'}
    expected = cfg['footer'].format(models='Fixture Model A (5), fixture-model-b (2)', total=7)
    text = written(repo)
    assert text == '# Invented log\n\n## What I Plan Next\nCount spoons.\n' + expected
    assert 'Measured models:' in expected


@pytest.mark.parametrize('encoded', [False, True])
def test_folder_is_matched_in_both_stored_forms_and_any_letter_case(repo, encoded):
    record(driver(repo) / 'a.db', uri(repo, encoded).swapcase().replace('FILE', 'file'), ['fixture-model-a'])
    assert d.models_used(repo) == [('fixture-model-a', 1)]


@pytest.mark.parametrize('length', range(60, 96, 5))
def test_a_stored_length_that_is_also_a_letter_still_matches(tmp_path, length):
    # A folder whose stored length byte is a capital letter (65 to 90) was
    # missed when the whole record was lowered before the comparison.
    base = tmp_path.resolve().as_uri() + '/'
    root = tmp_path / ('x' * max(1, length - len(base)))
    root.mkdir()
    blob = b'\x12' + bytes([len(root.resolve().as_uri())]) + root.resolve().as_uri().encode() + b'R'
    assert d._names_folder(blob, d._workspace_forms(root))
    assert not d._names_folder(blob, d._workspace_forms(root.parent))


def test_another_folder_is_not_this_one_even_with_the_same_start(repo):
    record(driver(repo) / 'a.db', uri(repo) + '-private-history', ['fixture-model-c'] * 3)
    record(driver(repo) / 'b.db', uri(repo.parent / 'someone-else'), ['fixture-model-c'] * 3)
    assert d.models_used(repo) == []
    assert written(repo) == '# Invented log\n\n## What I Plan Next\nCount spoons.\n'


def test_newest_conversation_for_the_folder_is_the_one_read(repo):
    record(driver(repo) / 'old.db', uri(repo), ['fixture-model-d'] * 4)
    record(driver(repo) / 'other.db', uri(repo.parent / 'someone-else'), ['fixture-model-c'])
    record(driver(repo) / 'new.db', uri(repo), ['fixture-model-a'] * 2)
    stamp = NOW.timestamp()
    os.utime(driver(repo) / 'old.db', (stamp - 3600, stamp - 3600))
    os.utime(driver(repo) / 'new.db', (stamp - 60, stamp - 60))
    os.utime(driver(repo) / 'other.db', (stamp, stamp))
    assert d.models_used(repo, now=stamp) == [('fixture-model-a', 2)]
    # A record older than the setting allows is not this wake's.
    assert d.models_used(repo, now=stamp + 60 * 60 * 24) == []


def test_a_name_in_the_text_of_a_call_is_not_counted(repo):
    said = b'\x12\x30He said fixture-model-c and \x9a\x01\x03gpt-5-codex\xa2\x01 in a letter.'
    record(driver(repo) / 'a.db', uri(repo), ['fixture-model-a'] * 3, noise=said)
    with closing(sqlite3.connect(driver(repo) / 'a.db')) as con:
        con.execute('update gen_metadata set data = ? where idx = 2',
                    (call('fixture-model-a', said) + said,))
        con.execute('insert into gen_metadata values (?, ?, 0)', (3, said))
        con.commit()
    assert d.models_used(repo) == [('fixture-model-a', 3)]


@pytest.mark.parametrize('damage', ['garbage', 'no_tables', 'no_folder', 'no_settings', 'disabled', 'bad_pattern'])
def test_nothing_readable_means_no_footer_and_the_log_is_still_written(repo, damage):
    target = driver(repo) / 'a.db'
    record(target, uri(repo), ['fixture-model-a'])
    cfg = json.loads((repo / d.SETTINGS).read_bytes())
    if damage == 'garbage':
        target.write_bytes(b'not a database at all')
    elif damage == 'no_tables':
        target.unlink()
        sqlite3.connect(target).close()
    elif damage == 'no_folder':
        cfg['folder'] = str(repo.parent / 'nowhere')
    elif damage == 'no_settings':
        (repo / d.SETTINGS).unlink()
        cfg = None
    elif damage == 'disabled':
        cfg['enabled'] = False
    elif damage == 'bad_pattern':
        cfg['model_pattern'] = '(['
    if cfg is not None:
        (repo / d.SETTINGS).write_text(json.dumps(cfg), encoding='utf-8')
    assert d.footer(repo) == ''
    assert written(repo) == '# Invented log\n\n## What I Plan Next\nCount spoons.\n'
    assert json.loads((repo / 'budget.json').read_bytes())['wake_count'] == 151


@pytest.mark.parametrize('wal', [False, True])
def test_the_record_is_only_read(repo, monkeypatch, wal):
    target = driver(repo) / 'a.db'
    record(target, uri(repo), ['fixture-model-a'] * 2)
    with closing(sqlite3.connect(target)) as writer:
        if wal:
            writer.execute('pragma journal_mode=wal')
            writer.execute('insert into gen_metadata values (?, ?, 0)', (2, call('fixture-model-a')))
            writer.commit()
        before = {p.name: p.read_bytes() for p in driver(repo).iterdir()}
        opened = []
        original = d._open

        def copied_only(path):
            assert path.parent != target.parent
            assert path.read_bytes() == target.read_bytes()
            for suffix in ('-wal', '-shm'):
                live = Path(str(target) + suffix)
                if live.exists():
                    assert Path(str(path) + suffix).read_bytes() == live.read_bytes()
            opened.append(path)
            return original(path)

        monkeypatch.setattr(d, '_open', copied_only)
        assert d.models_used(repo) == [('fixture-model-a', 3 if wal else 2)]
        assert {p.name: p.read_bytes() for p in driver(repo).iterdir()} == before
        assert opened and all(not p.parent.exists() for p in opened)


def test_copy_failure_means_no_footer(repo, monkeypatch):
    import shutil

    record(driver(repo) / 'a.db', uri(repo), ['fixture-model-a'])

    def unavailable(*args, **kwargs):
        raise OSError('copy unavailable')

    monkeypatch.setattr(shutil, 'copy2', unavailable)
    monkeypatch.setattr(d, '_open', lambda path: pytest.fail('must not open after copy failure'))
    assert d.footer(repo) == ''


@pytest.mark.parametrize('outcome', ['success', 'other_folder', 'query_error'])
def test_connections_are_closed_on_every_exit(repo, monkeypatch, outcome):
    record(driver(repo) / 'a.db', uri(repo if outcome != 'other_folder' else repo.parent), ['fixture-model-a'])
    connections = []
    original = d._open

    def tracked(path):
        con = original(path)
        connections.append(con)
        return con

    monkeypatch.setattr(d, '_open', tracked)
    cfg = d.settings(repo)
    if outcome == 'query_error':
        cfg['table'] = 'missing'
    d.models_used(repo, cfg)
    assert connections
    for con in connections:
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            con.execute('select 1')


def test_without_labels_every_name_is_shown_as_the_driver_wrote_it(repo):
    cfg = json.loads((repo / d.SETTINGS).read_bytes())
    del cfg['labels']
    (repo / d.SETTINGS).write_text(json.dumps(cfg), encoding='utf-8')
    record(driver(repo) / 'a.db', uri(repo), ['fixture-model-a'] * 3)
    assert 'fixture-model-a (3)' in d.footer(repo)



@pytest.mark.parametrize('copied', [
    '\n\n---\nMeasured models: Fixture Model A (98); 98 calls.\n',
    '\r\n\r\n---\r\nMeasured models: Fixture Model A; 98 calls.\r\n',
])
def test_a_footer_the_agent_copied_is_replaced_by_the_measured_one(repo, copied):
    # A copied footer must not keep the previous count.
    record(driver(repo) / 'a.db', uri(repo), ['fixture-model-a'] * 5)
    reply = h.write_journal('# Invented log\n\n## What I Plan Next\nCount spoons.' + copied).splitlines()[0]
    text = (repo / (names.rel("journal")) / reply).read_text(encoding='utf-8')
    assert text.count('Measured models:') == 1
    assert '(5)' in text and '(98)' not in text
    assert text.startswith('# Invented log\n\n## What I Plan Next\nCount spoons.')


def test_his_own_mention_of_model_calls_is_kept(repo):
    record(driver(repo) / 'a.db', uri(repo), ['fixture-model-a'] * 5)
    line = 'I wondered how many model calls this wake took.\n'
    reply = h.write_journal('# Invented log\n\n' + line).splitlines()[0]
    assert line in (repo / (names.rel("journal")) / reply).read_text(encoding='utf-8')
