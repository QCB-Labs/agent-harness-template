"""Read meter regressions; invented memory only."""
import asyncio
import json

import pytest
from pydantic_core import to_json
from tools import cards as c, harness_mcp as h, names
from tests.test_card_harness import repo, _reads
from tests.test_recall_answer import card, memory

CALLS = [
    ('read_wake_card', {}), ('recall', {'tags': []}),
    ('read_card', {'card_id': 'goal-0001'}),
    ('read_thread', {'title': 'Missing'}), ('read_threads', {}),
    ('read_open_threads', {}), ('read_lessons', {}), ('read_goals', {}),
    ('read_recent_journal', {}), ('read_resident', {}),
    ('read_correspondence', {}), ('read_drafts', {}), ('read_wishlist', {}),
    ('read_city_rules', {}), ('read_goal', {'name': 'Missing'}),
    ('read_project', {'title': 'Missing'}), ('read_works_index', {}),
    ('read_work', {'slug': 'missing'}), ('read_identity', {}), ('read_governance', {}),
    ('read_map', {}), ('read_registry', {}), ('read_place', {}),
    ('read_almanac_status', {}), ('list_continents', {}),
    ('read_last_seen', {}), ('recover_unrecorded', {}),
    ('query_almanac', {}), ('compute_route', {'from_place_id': 1, 'to_place_id': 2}),
]

@pytest.mark.parametrize('name,args', CALLS)
def test_memory_tool_logs_exact_transport_bytes(repo, monkeypatch, name, args):
    monkeypatch.setattr(h, '_collect_unread_mail', lambda: '')
    h._ensure_cards()
    conversation = '00000000-0000-4000-8000-00000000000a'
    content = asyncio.run(h.mcp.call_tool(names.tool(name), dict(args, _conversationId=conversation)))
    blocks = content[0] if isinstance(content, tuple) else content
    event = _reads(repo)[-1]
    assert event['tool'] == name
    assert event['conversation'] == conversation
    assert event['bytes'] == sum(len(block.text.encode('utf-8')) for block in blocks)
    assert {'time', 'wake', 'conversation', 'session', 'refused', 'ids'} <= event.keys()


def test_recall_known_non_ascii_answer(tmp_path):
    mem, config = memory(tmp_path, [card(1, size=6)])
    result = c.recall(mem, tags=[], config_path=config)
    expected = to_json(result, fallback=str, indent=2)
    event = json.loads((mem / 'private/card-reads.jsonl').read_text().splitlines()[-1])
    assert result['cards'][0]['text'] == 'ééé'
    assert event['bytes'] == len(expected) > len(expected.decode('utf-8'))
    assert event['bytes'] != result['bytes']


def test_missing_card_is_a_logged_refusal(repo):
    result = h.read_card('missing')
    assert result == {'error': 'NOT_FOUND', 'id': 'missing'}
    event = _reads(repo)[-1]
    assert event['tool'] == 'read_card' and event['refused'] is True
    assert event['ids'] == []
    assert event['bytes'] == len(to_json(result, fallback=str, indent=2))


def test_sealed_reads_follow_completed_wake(repo, monkeypatch):
    monkeypatch.setattr(h, '_load_budget', lambda: {'wake_count': 260})
    (repo / h.SEALED_MARKER).parent.mkdir(parents=True, exist_ok=True)
    (repo / h.SEALED_MARKER).touch()
    h.read_wake_card()
    assert _reads(repo)[-1]['wake'] == names.wake_id(260)
    h.read_lessons()
    assert _reads(repo)[-1]['wake'] == names.wake_id(260)
    (repo / h.SEALED_MARKER).unlink()
    h.read_lessons()
    assert _reads(repo)[-1]['wake'] == names.wake_id(261)


@pytest.mark.parametrize('name,args', [('recall', {'tags': []}), ('read_card', {'card_id': 'goal-0001'}), ('read_wake_card', {}), ('read_lessons', {})])
def test_logging_failure_preserves_answer(repo, monkeypatch, name, args):
    h._ensure_cards()
    expected = getattr(h, name)(**args)
    def fail(*a, **k):
        raise OSError('invented log failure')
    monkeypatch.setattr(c, '_append_read', fail)
    assert getattr(h, name)(**args) == expected


def test_square_tags_in_legacy_thread_index():
    item = dict(id='thread-0001', title='Invented Square thread', tags=['kind:thread', 'name:rin', 'place:9', 'post:42', 'resident:7'])
    result = c._wake_added_section('Open threads (index)', [item], 5000)
    assert '[name:rin, place:9, post:42, resident:7]' in result['text']


def test_square_tags_in_file_thread_index(repo):
    from tools import threads
    h._ensure_cards()
    path = threads.open_thread(repo, 'Invented Square thread', 'Post #42 with Rin.')
    h._recut_after_write('threads', path)
    store = c.CardStore(repo / names.dir_name('memory'), repo / 'config/tag-patterns.city.json')
    header = next(x for x in store._current_state().values() if (x.get('origin') or {}).get('file') == path.relative_to(repo).as_posix() and x['origin']['key'] == 'header')
    # Tags are deterministic input to rendering; no production content needed.
    header['tags'] = ['name:rin', 'post:42']
    with store.cards_path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(header) + '\n')
    result = h.read_wake_card()
    assert '[name:rin, post:42]' in result['text']


def test_plain_refusal_logs_returned_bytes(repo):
    result = h.read_thread('Missing')
    event = _reads(repo)[-1]
    assert event['refused'] is True
    assert event['bytes'] == len(result.encode('utf-8'))


def test_updating_seen_does_not_meter_an_internal_read(repo):
    h.update_last_seen('notes', 123)
    assert not (repo / names.memory('private', 'card-reads.jsonl')).exists()
