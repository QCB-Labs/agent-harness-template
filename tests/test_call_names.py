"""Argument-name hardening on invented fixtures, with exact byte writes."""
import ast
import asyncio
import inspect
import json
from pathlib import Path
import shutil

import pytest
from tools import harness_mcp as harness, names

ROOT = Path(__file__).resolve().parents[1]
FROM = ['from_place_id', 'current_id', 'from_id', 'from_place', 'origin_id']
TO = ['to_place_id', 'target_id', 'to_id', 'to_place', 'destination_id']
NEEDS_FROM = 'compute_route needs both ends. Call compute_route(from_place_id=<the place you are in>, to_place_id=<the place you want>).'
UPDATE_GOALS, READ_GOALS, HELP = names.tool('update_goals'), names.tool('read_goals'), names.tool('help')
REFUSED = (f'{UPDATE_GOALS} REFUSED: nothing was changed. It takes the whole of {names.rel("goals")} as one string. '
           f'Call {READ_GOALS}(), edit the text, then call {UPDATE_GOALS}(new_text="<the whole file>").')
# fixture file -> the name this scheme gives it
SOURCES = {
    'goals.md': names.rel('goals'),
    'memory/lessons.md': names.rel('lessons'),
    'memory/city-rules.md': names.rel('city_rules'),
    'memory/open-threads.md': names.rel('open_threads'),
    'memory/journal': names.rel('journal'),
    'memory/residents': names.rel('residents'),
}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, 'REPO', tmp_path)
    memory = tmp_path / names.dir_name('memory')
    memory.mkdir()
    places = {
        '195': {'id': 195, 'name': 'Invented world', 'parent_id': None},
        '901': {'id': 901, 'name': 'Glass continent', 'parent_id': 195, 'continent_id': 901, 'owner': 'fixture', 'open_to_building': True},
        '902': {'id': 902, 'name': 'Paper continent', 'parent_id': 195, 'continent_id': 902},
        '903': {'id': 903, 'name': 'Glass shelf', 'parent_id': 901, 'continent_id': 901},
    }
    (memory / 'almanac.json').write_bytes(json.dumps({'places': places}).encode('utf-8'))
    (memory / 'map.json').write_bytes(json.dumps({'places': {'901': {'visited': True}, '902': {'visited': False}}}).encode('utf-8'))
    return tmp_path


def mcp_result(name, arguments):
    result = asyncio.run(harness.mcp.call_tool(name, arguments))
    content = result[0] if isinstance(result, tuple) else result
    text = ''.join(item.text for item in content if item.type == 'text')
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


@pytest.mark.parametrize('from_name', FROM)
@pytest.mark.parametrize('to_name', TO)
def test_all_route_pairs_and_mixed_spellings_reach_mcp(repo, from_name, to_name):
    expected = harness.compute_route(current_id=903, target_id=902)
    assert expected['route'] == [903, 901, 195, 902]
    arguments = {from_name: 903, to_name: 902}
    assert harness.compute_route(**arguments) == expected
    assert mcp_result('compute_route', arguments) == expected


@pytest.mark.parametrize('to_name', TO)
def test_destination_only_returns_exact_normal_answer(repo, to_name):
    expected = {'status': 'NEEDS_FROM', 'message': NEEDS_FROM}
    assert harness.compute_route(**{to_name: 902}) == expected
    assert mcp_result('compute_route', {to_name: 902}) == expected


def test_missing_ends_does_not_read_or_guess_a_map(repo, monkeypatch):
    def unexpected():
        raise AssertionError('missing ends must not read a map')
    monkeypatch.setattr(harness, '_load_map', unexpected)
    monkeypatch.setattr(harness, '_load_almanac', unexpected)
    assert mcp_result('compute_route', {}) == {'status': 'NEEDS_FROM', 'message': NEEDS_FROM}
    assert harness.compute_route(from_place_id=903) == {'status': 'NEEDS_FROM', 'message': NEEDS_FROM}


@pytest.mark.parametrize('arguments, name', [({'from_place_id': 999, 'to_place_id': 902}, 'from_place_id'), ({'from_place_id': 903, 'to_place_id': 999}, 'to_place_id')])
def test_unknown_place_uses_documented_names(repo, arguments, name):
    result = harness.compute_route(**arguments)
    assert result['status'] == 'UNKNOWN_PLACE'
    assert result['message'] == name + ' not in the almanac. Update with update_almanac first.'


@pytest.mark.parametrize('history', [False, True])
def test_read_card_id_alias_uses_same_real_store(repo, history):
    fixture = ROOT / 'tests/fixtures/cards'
    source = repo / 'fixture'
    for fixture_path, scheme_path in SOURCES.items():
        target = source / scheme_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if (fixture / fixture_path).is_dir():
            shutil.copytree(fixture / fixture_path, target)
        else:
            shutil.copyfile(fixture / fixture_path, target)
    shutil.copyfile(ROOT / 'naming.json', source / 'naming.json')
    shutil.copytree(ROOT / 'config', source / 'config')
    # Only this test's invented memory is used; no repository memory is read.
    harness.REPO = source
    expected = harness.read_card(card_id='goal-0001', history=history)
    assert harness.read_card(id='goal-0001', history=history) == expected
    assert mcp_result('read_card', {'id': 'goal-0001', 'history': history}) == expected


@pytest.mark.parametrize('arguments', [{}, {'now': ['Invented goal']}, {'later': ['Invented next goal']}, {'unknown': 'Invented text'}])
def test_missing_goals_text_refuses_without_writes_or_backups(repo, monkeypatch, arguments):
    original = '# Goals\r\n\r\nInvented café goal without final newline'.encode('utf-8')
    (repo / names.rel('goals')).write_bytes(original)
    before = sorted(path.relative_to(repo).as_posix() for path in repo.rglob('*'))
    def unexpected(*args, **kwargs):
        raise AssertionError('refusal must not reach the writer or card sync')
    monkeypatch.setattr(harness, 'safe_replace_file', unexpected)
    monkeypatch.setattr(harness, '_recut_after_write', unexpected)
    assert harness.update_goals(**arguments) == REFUSED
    assert mcp_result(UPDATE_GOALS, arguments) == REFUSED
    assert (repo / names.rel('goals')).read_bytes() == original
    assert sorted(path.relative_to(repo).as_posix() for path in repo.rglob('*')) == before


def test_continents_only_add_visited_and_agree_with_query(repo):
    result = harness.list_continents()
    assert result == {'continents': [
        {'id': 901, 'name': 'Glass continent', 'owner': 'fixture', 'subplaces_count': 1, 'open_to_building': True, 'open_to_things': False, 'open_to_notes': False, 'visited': True},
        {'id': 902, 'name': 'Paper continent', 'owner': None, 'subplaces_count': 0, 'open_to_building': False, 'open_to_things': False, 'open_to_notes': False, 'visited': False},
    ], 'total_continents': 2}
    assert [p['id'] for p in harness.query_almanac(parent_id=195, unvisited_only=True)['matches']] == [902]
    # Main's fields and sorting are unchanged, with just one boolean added.
    for entry in result['continents']:
        assert isinstance(entry.pop('visited'), bool)
        assert set(entry) == {'id', 'name', 'owner', 'subplaces_count', 'open_to_building', 'open_to_things', 'open_to_notes'}


def test_help_lists_every_real_registered_tool_once_within_budget(repo):
    output = harness.help()
    registered = asyncio.run(harness.mcp.list_tools())
    names = [line.split(': ', 1)[0] for line in output.splitlines() if ': ' in line]
    assert sorted(names) == sorted(tool.name for tool in registered)
    assert len(names) == len(set(names))
    assert len(output.encode('utf-8')) <= 6000
    assert mcp_result(HELP, {}) == output


def test_help_reflects_tool_registered_after_import(repo):
    def invented_probe(place_id: int, count: int = 2) -> str:
        """Return an invented probe result."""
        return 'fixture'
    harness.mcp.add_tool(invented_probe)
    try:
        output = harness.help()
        assert output.count('invented_probe: Return an invented probe result.') == 1
        assert len(output.encode('utf-8')) <= 6000
        assert 'count = 2' in harness.help(tool='invented_probe')
        _bind_example(harness.mcp._tool_manager.get_tool('invented_probe'))
    finally:
        harness.mcp.remove_tool('invented_probe')


def _bind_example(tool):
    output = harness.help(tool=tool.name)
    assert output.startswith(tool.description.strip())
    expression = ast.parse(output.rsplit('\nExample: ', 1)[1], mode='eval').body
    assert isinstance(expression, ast.Call) and expression.func.id == tool.name
    assert not expression.args
    arguments = {kw.arg: ast.literal_eval(kw.value) for kw in expression.keywords}
    assert set(arguments) <= set(tool.parameters.get('properties', {}))
    tool.fn_metadata.arg_model.model_validate(arguments)
    # Required aliases are absent from examples for optional injected context.
    inspect.signature(tool.fn).bind(**arguments)
    for name, schema in tool.parameters.get('properties', {}).items():
        assert name in output.split('\nArguments:\n', 1)[1]
        if 'default' in schema:
            assert name + ' = ' + repr(schema['default']) in output


@pytest.mark.parametrize('tool_name', [tool.name for tool in harness.mcp._tool_manager.list_tools()])
def test_every_help_example_binds_real_signature_and_schema(repo, tool_name):
    _bind_example(harness.mcp._tool_manager.get_tool(tool_name))


def test_help_search_matches_every_word_case_insensitively(repo):
    assert 'query_almanac: ' in harness.help(about='VISITED places')
    assert 'query_almanac: ' in harness.help(about='visited')
    assert 'compute_route: ' not in harness.help(about='visited')
    assert harness.help(about='nonexistentword') == f'No harness tool matches those words. {HELP}() lists them all.'
    assert harness.help(tool='nonexistent_tool') == f'No harness tool has that name. {HELP}() lists them all.'


def test_help_is_read_only_and_works_under_stop_marker(repo):
    (repo / 'STOP.md').write_bytes(b'Invented stop marker\n')
    before = {path.relative_to(repo): path.read_bytes() for path in repo.rglob('*') if path.is_file()}
    assert HELP + ': ' in mcp_result(HELP, {})
    assert 'Example: compute_route(' in mcp_result(HELP, {'tool': 'compute_route'})
    assert 'query_almanac: ' in mcp_result(HELP, {'about': 'visited'})
    assert {path.relative_to(repo): path.read_bytes() for path in repo.rglob('*') if path.is_file()} == before


def test_new_sentence_calls_bind_real_signatures(repo):
    calls = [
        ('compute_route', {'from_place_id': 901, 'to_place_id': 902}),
        ('read_card', {'card_id': '<id>'}),
        ('read_goals', {}), ('update_goals', {'new_text': '<the whole file>'}),
        ('query_almanac', {'parent_id': 195, 'unvisited_only': True}),
        ('help', {}), ('help', {'tool': 'query_almanac'}), ('help', {'about': 'visited'}),
    ]
    for name, arguments in calls:
        tool = harness.mcp._tool_manager.get_tool(names.tool(name))
        inspect.signature(tool.fn).bind(**arguments)
        tool.fn_metadata.arg_model.model_validate(arguments)
