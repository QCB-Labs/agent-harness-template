"""LINKS-SPEC v0.2 readers, using invented files only."""
import asyncio
import json
from pathlib import Path

import pytest
from tools import cards, harness_mcp as h, names, threads
from tests.test_card_harness import repo, _reads
from tests.test_links import card


def test_default_note_links_off_and_opt_in_cap():
    settings = json.loads((Path(__file__).resolve().parents[1] / 'config/cards.json').read_text())
    settings['recall_linked_listed'] = 12
    state = [card('seed', ['note:1'])] + [card(f'n{i}', ['note:1']) for i in range(6)]
    assert cards.linked_labels(state[:1], state, {}, settings) == {}
    settings['link_tag_prefixes'].append('note:')
    assert sum(map(len, cards.linked_labels(state[:1], state, {}, settings).values())) == 3


@pytest.fixture
def reader_scene(repo):
    config = repo / 'config/cards.json'
    settings = json.loads(config.read_text())
    settings['recall_linked_listed'] = 12
    config.write_text(json.dumps(settings))
    path = repo / (names.rel('residents') + '/mira.md')
    body = '# Mira (#12)\n\n## Impression\nPlace #9.\n'
    path.write_text(body, encoding='utf-8')
    seed = card('seed', ['resident:12', 'place:9'], 'relationship',
                origin={'file': (names.rel('residents') + '/mira.md'), 'file_tags': ['resident:12', 'name:mira']})
    state = [seed, card('own', ['resident:12'])] + [card(f'link{i}', ['place:9']) for i in range(5)]
    store = repo / names.memory('cards/cards.jsonl')
    store.parent.mkdir(parents=True)
    store.write_text(''.join(json.dumps(c) + '\n' for c in state))
    return repo, config, settings, body, state


def test_resident_links_and_log(reader_scene):
    repo, _, _, body, state = reader_scene
    answer = h.read_resident('mira')
    assert answer.startswith(body + '\nLinked cards:\nthreads:\n')
    assert 'link0 | link0 | thread | 14 bytes | place:9' in answer
    assert 'own |' not in answer and 'seed |' not in answer
    event = _reads(repo)[-1]
    assert event['offered'] == ['link0', 'link1', 'link2']
    assert event['ids'] == ['seed']
    assert event['omitted'] == {'kind_cap': 2, 'total_cap': 0, 'answer_trim': 0}
    assert event['bytes'] == len(answer.encode('utf-8'))


def test_reader_empty_and_indexes(reader_scene):
    repo, _, _, _, _ = reader_scene
    (repo / names.memory('cards/cards.jsonl')).write_text('')
    assert h.read_resident('mira').endswith('Linked cards: empty.')
    index = repo / (names.rel('residents') + '/index.md')
    assert h.read_resident() == index.read_text(encoding='utf-8')
    assert 'offered' not in _reads(repo)[-1]
    assert 'Linked cards' not in h.read_threads()
    assert 'offered' not in _reads(repo)[-1]


def test_reader_trim_preserves_file_and_logs_visible_only(reader_scene):
    repo, config, settings, body, _ = reader_scene
    settings['recall_answer_bytes'] = len(body.encode('utf-8'))
    config.write_text(json.dumps(settings))
    assert h.read_resident('mira') == body
    event = _reads(repo)[-1]
    assert event['offered'] == []
    assert event['omitted']['answer_trim'] == 3


@pytest.mark.parametrize('closed', [False, True])
def test_thread_heading_tags_and_file_selection(reader_scene, closed):
    repo, _, _, _, state = reader_scene
    title = 'Place #9 and Resident #12'
    # The harness reads the configured cutter patterns: configure the city's.
    (repo / names.memory('cards', 'tag-patterns.json')).write_bytes((repo / 'config/tag-patterns.city.json').read_bytes())
    path = threads.open_thread(repo, title, 'Thing #77.')
    if closed:
        _, path = threads.close_thread(repo, title, 'Finished.', 42, '2026-11-10')
    body = path.read_text()
    prepared = cards._prepare_threads(path, body, path.relative_to(repo).as_posix(),
                                      json.loads((repo / 'config/tag-patterns.city.json').read_text()))
    # Cutter file tags name the thread; entity tags in its title are also its own.
    assert prepared[0][3]['file_tags'] == ['thread:place-9-and-resident-12']
    state[0]['origin'] = prepared[0][3]
    state[0]['tags'] = ['resident:12', 'place:9', 'thing:77']
    if closed:
        state[0]['status'] = 'done'
    state += [card('body-link', ['thing:77'])]
    (repo / names.memory('cards/cards.jsonl')).write_text(''.join(json.dumps(c) + '\n' for c in state))
    answer = h.read_thread(title)
    assert answer.startswith(body)
    if closed:
        assert answer.endswith('Linked cards: empty.')  # closed files have no live cards
    else:
        assert 'body-link |' in answer
        assert 'link0 |' not in answer and 'own |' not in answer


def test_links_off_readers_match_main_bytes_and_logs(reader_scene, monkeypatch):
    repo, config, settings, body, _ = reader_scene
    settings['recall_linked_listed'] = 0
    config.write_text(json.dumps(settings))
    monkeypatch.setattr(cards, '_utc_now', lambda: 'fixed-time')
    assert h.read_resident('mira') == body
    event = _reads(repo)[-1]
    assert event == dict(time='fixed-time', wake=h._current_wake_label(), tool='read_resident',
                         bytes=len(body.encode('utf-8')), ids=[], refused=False,
                         conversation=None, session='wake')
    path = threads.open_thread(repo, 'Invented', 'Keep it.')
    assert h.read_thread('Invented') == path.read_text()
    assert set(_reads(repo)[-1]) == set(event)


def test_reader_transport_bytes(reader_scene):
    repo = reader_scene[0]
    content = asyncio.run(h.mcp.call_tool(names.tool('read_resident'), {'handle': 'mira'}))
    blocks = content[0] if isinstance(content, tuple) else content
    assert _reads(repo)[-1]['bytes'] == sum(len(b.text.encode('utf-8')) for b in blocks)


def test_a_fault_in_the_list_leaves_the_readers_and_recall_answering(reader_scene, monkeypatch):
    repo, config, _, body, _ = reader_scene
    def fail(*args, **kwargs):
        raise RuntimeError('made-up list fault')
    monkeypatch.setattr(cards, 'linked_labels', fail)
    assert h.read_resident('mira') == body
    answer = cards.recall(repo / names.dir_name('memory'), tags=['resident:12'], limit=1, config_path=config,
                          record_read=False)
    assert [c['id'] for c in answer['cards']] and 'linked' not in answer
