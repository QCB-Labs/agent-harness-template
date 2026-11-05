"""Brief 21: invented small files, exact bytes and bounded readers."""
from naming_support import fixture_name
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
from tools import cards as c, goals as g, harness_mcp as h, names as naming

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.datetime(2026, 10, 8, 12, tzinfo=datetime.timezone.utc)


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode('utf-8'))


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(h, 'REPO', tmp_path)
    monkeypatch.setattr(h, '_utc_now', lambda: NOW)
    write(tmp_path / fixture_name('DIRECTIVES.md'), '# Goals\n\n## Ambition\n\nKeep learning.\n')
    write(tmp_path / fixture_name('PERSONNEL-FILE.md'), '# Fixture identity\n')
    write(tmp_path / fixture_name('STANDING-ORDERS.md'), '# Fixture governance\n')
    write(tmp_path / 'budget.json', b'{"wake_count":41}')
    naming = {
        'dirs': {'memory': fixture_name('records')},
        'paths': {'identity': fixture_name('PERSONNEL-FILE.md'), 'governance': fixture_name('STANDING-ORDERS.md'), 'goals': fixture_name('DIRECTIVES.md'),
                  'goal_files': fixture_name('records/goals/'), 'projects': fixture_name('records/projects/')},
        'indexes': {},
    }
    write(tmp_path / 'naming.json', json.dumps(naming))
    write(tmp_path / 'config/tag-patterns.city.json', (ROOT / 'config/tag-patterns.city.json').read_bytes())
    write(tmp_path / 'config/tag-patterns.generic.json', (ROOT / 'config/tag-patterns.generic.json').read_bytes())
    write(tmp_path / 'config/cards.json', (ROOT / 'config/cards.json').read_bytes())
    return tmp_path


def snapshot(repo):
    # Reads may append research telemetry; all source bytes must stay identical.
    return {p.relative_to(repo).as_posix(): p.read_bytes() for p in repo.rglob('*')
            if p.is_file() and p.relative_to(repo).as_posix() != naming.memory('private', 'card-reads.jsonl')}


def store(repo):
    return c.CardStore(repo / fixture_name('records'), repo / 'config/tag-patterns.city.json')


def recut(repo, logical):
    return c.recut_all(repo, repo / 'naming.json', repo / 'config/tag-patterns.city.json', logical)


def open_fixture(title='Model', **kwargs):
    return h.open_project(title=title, target=kwargs.pop('target', 'Working model'),
                          next_step=kwargs.pop('next_step', 'Sketch'), **kwargs)


def test_goal_shape_replace_drop_archive_bytes(repo):
    how = 'Écrire café.\r\n第二行 🚀\r\n'
    assert h.set_goal(name='Learn', how=how) == 'Goal "Learn" stored.'
    path = repo / fixture_name('records/goals/learn.md')
    assert path.read_bytes() == b'# Learn\n\n' + how.encode('utf-8')
    assert h.read_goal(name='Learn').encode() == path.read_bytes()
    how2 = 'Réviser\r\nexactly'
    h.set_goal(name='Learn', how=how2)
    whole = b'# Learn\n\n' + how2.encode()
    assert path.read_bytes() == whole
    reason = 'Changed direction.\r\n理由'
    assert h.drop_goal(name='Learn', reason=reason) == 'Goal "Learn" archived and removed.'
    assert not path.exists()
    assert (repo / fixture_name('COMPLETED-DIRECTIVES.md')).read_bytes() == b'\n## 2026-10-08-120000\n\n' + whole + b'\n\n' + reason.encode() + b'\n'
    assert [card['status'] for card in store(repo)._current_state().values()
            if card['origin']['logical'] == 'goal_files'] == ['done']


def test_project_shape_and_verbatim_target(repo):
    target = 'A café model\r\n完成 🚀'
    assert open_fixture(target=target, steps=['Draw', 'Build']) == 'Project "Model" opened.'
    expected = ('# Model\n\n- **Status:** open\n- **Serves:** none\n- **Target:** ' + target +
                '\n\n## Steps\n\n- [ ] Draw\n- [ ] Build\n\n## Next step\n\nSketch').encode()
    assert (repo / fixture_name('records/projects/model.md')).read_bytes() == expected
    assert h.read_project(title='Model').encode() == expected


@pytest.mark.parametrize('before,done,added,expected_steps', [
    (['Draw', 'Build'], 'Draw', ['Test'], '- [x] Draw\n- [ ] Build\n- [ ] Test\n'),
    (['Draw'], 'Inspect', [], '- [ ] Draw\n- [x] Inspect\n'),
    (['Draw'], '', ['Build'], '- [ ] Draw\n- [ ] Build\n'),
    ([], 'Inspect', ['Build'], '- [x] Inspect\n- [ ] Build\n'),
    (['Draw'], 'draw', [], '- [ ] Draw\n- [x] draw\n'),
])
def test_advance_exact_spans(repo, before, done, added, expected_steps):
    open_fixture(steps=before, target='Café\r\n二')
    path = repo / fixture_name('records/projects/model.md')
    original = path.read_bytes()
    assert h.advance_project(title='Model', next_step='Measure', done_step=done, add_steps=added) == 'Project "Model" advanced.'
    prefix = original.split(b'## Steps\n\n', 1)[0]
    assert path.read_bytes() == prefix + b'## Steps\n\n' + expected_steps.encode() + b'\n## Next step\n\nMeasure'


def test_advance_preserves_crlf_fixture_and_duplicate_step_order(repo):
    open_fixture(steps=['Draw', 'Draw'])
    path = repo / fixture_name('records/projects/model.md')
    write(path, path.read_bytes().replace(b'\n', b'\r\n') + b'\r\n')
    original = path.read_bytes()
    h.advance_project(title='Model', next_step='Réviser', done_step='Draw', add_steps=['測る'])
    expected = original.replace(b'- [ ] Draw', b'- [x] Draw', 1).replace(b'\r\n\r\n## Next step',
              '\r\n- [ ] 測る\r\n\r\n## Next step'.encode()).replace(b'Sketch', 'Réviser'.encode())
    assert path.read_bytes() == expected


@pytest.mark.parametrize('outcome,reason', [('completed', ''), ('abandoned', 'Changed\r\n理由')])
def test_close_changes_only_status_and_uses_harness_wake(repo, outcome, reason):
    open_fixture(steps=['Draw', 'Build'])
    path = repo / fixture_name('records/projects/model.md')
    old = path.read_bytes()
    assert h.close_project(title='Model', outcome=outcome, reason=reason) == f'Project "Model" closed as {outcome}.'
    closed = repo / fixture_name('records/projects/closed/model-SR-0042.md')
    status = fixture_name(f'- **Status:** {outcome} in SR-0042, 2026-10-08') + (f': {reason}' if outcome == 'abandoned' else '')
    assert closed.read_bytes() == old.replace(b'- **Status:** open', status.encode(), 1)
    assert not path.exists()
    assert h.read_project(title='Model').encode() == closed.read_bytes()
    assert (repo / 'budget.json').read_bytes() == b'{"wake_count":41}'


@pytest.mark.parametrize('name,first,second', [
    ('goal', 'Café', 'Cafe'), ('goal', 'a' * 60 + 'x', 'a' * 60 + 'y'),
    ('project', 'Café', 'Cafe'), ('project', 'A B', 'A-B'),
])
def test_slug_collision_changes_nothing(repo, name, first, second):
    if name == 'goal':
        h.set_goal(name=first, how='Keep')
        call = lambda: h.set_goal(name=second, how='Overwrite')
    else:
        open_fixture(first)
        call = lambda: open_fixture(second)
    before = snapshot(repo)
    answer = call()
    assert 'REFUSED: nothing was changed.' in answer and 'belongs to' in answer
    assert snapshot(repo) == before
    assert len(g.slug(first)) <= 60 and re.fullmatch('[a-z0-9-]+', g.slug(first))


def test_unknown_serves_names_goals_and_has_no_effect(repo):
    h.set_goal(name='Learn', how='Read')
    h.set_goal(name='Make café', how='Practice')
    before = snapshot(repo)
    result = open_fixture(serves='Missing')
    assert 'Existing goals: ["Learn", "Make café"].' in result
    assert result.endswith('Call ' + g.CALLS['open_project'] + '.')
    assert snapshot(repo) == before


@pytest.mark.parametrize('name,kwargs', [
    ('drop_goal', dict(name='Missing', reason='Reason')),
    ('drop_goal', dict(name='Learn', reason='')),
    ('open_project', dict(title='Model', target='T', next_step='N')),
    ('advance_project', dict(title='Missing', next_step='N')),
    ('advance_project', dict(title='Model', next_step='two\nlines')),
    ('close_project', dict(title='Model', outcome='abandoned')),
    ('close_project', dict(title='Model', outcome='other')),
    ('read_goal', dict(name='Missing')),
    ('read_project', dict(title='Missing')),
    ('set_goal', dict(name='???', how='Text')),
])
def test_refusals_change_nothing_and_end_in_bindable_call(repo, name, kwargs):
    h.set_goal(name='Learn', how='Read')
    open_fixture()
    before = snapshot(repo)
    result = getattr(h, name)(**kwargs)
    assert 'REFUSED: nothing was changed.' in result
    assert result.endswith('Call ' + g.CALLS[name] + '.')
    assert snapshot(repo) == before


def test_stop_marker_guards_writes_and_leaves_reads_available(repo):
    h.set_goal(name='Learn', how='Read\r\ncafé')
    open_fixture()
    goal = h.read_goal(name='Learn')
    project = h.read_project(title='Model')
    goals = h.read_goals()
    write(repo / 'STOP.md', b'Invented stop')
    before = snapshot(repo)
    calls = {
        'set_goal': dict(name='New', how='Read'),
        'drop_goal': dict(name='Learn', reason='Changed'),
        'open_project': dict(title='New', target='T', next_step='N'),
        'advance_project': dict(title='Model', next_step='N'),
        'close_project': dict(title='Model', outcome='completed'),
    }
    for name, kwargs in calls.items():
        assert getattr(h, name)(**kwargs) == g.refusal(name, 'STOP.md is set.')
        assert snapshot(repo) == before
    assert h.read_goal(name='Learn') == goal
    assert h.read_project(title='Model') == project
    assert h.read_goals() == goals


def test_update_goals_old_behavior_then_exact_refusal(repo):
    original = '# Ambition\n\n' + 'Invented standing ambition. ' * 20
    assert 'updated' in h.update_goals(new_text=original)
    folder = repo / fixture_name('records/goals')
    folder.mkdir()
    assert 'updated' in h.update_goals(new_text=original)
    h.set_goal(name='Learn', how='Read')
    before = snapshot(repo)
    assert h.update_goals(new_text='Wrong shape') == g.UPDATE_REFUSAL
    assert h.update_goals() == g.UPDATE_REFUSAL
    assert snapshot(repo) == before


def test_read_goals_full_answer_closed_only_counted(repo):
    h.set_goal(name='Learn', how='Read\r\ncafé')
    open_fixture(serves='Learn', steps=['Draw'])
    open_fixture('Hidden')
    h.close_project(title='Hidden', outcome='completed')
    answer = h.read_goals()
    assert answer == ('# Goals\n\n## Ambition\n\nKeep learning.\n\n\n# Learn\n\nRead\r\ncafé\n\n' +
                      h.read_project(title='Model') + '\n\nClosed projects: 1.')
    assert 'Hidden' not in answer and 'Shortening' not in answer


@pytest.mark.parametrize('stage', [1, 2, 3, 4])
def test_read_goals_all_overflow_stages(repo, stage):
    if stage == 1:
        h.set_goal(name='Learn', how='Read')
        open_fixture(target='é' * 6000)
    elif stage == 2:
        h.set_goal(name='Learn', how='é' * 6000)
        open_fixture()
    elif stage == 3:
        # Many small files force summary-line omission without limiting stored text.
        for i in range(170):
            g.set_goal(repo, f'Goal {i:03}', 'é' * 20)
        open_fixture(next_step='n' * 3000)
    else:
        write(repo / fixture_name('DIRECTIVES.md'), ('Whole ambition. ' * 600).encode())
        h.set_goal(name='Learn', how='Read')
        open_fixture()
    answer = h.read_goals()
    assert g.NOTICES[stage] in answer
    assert answer.endswith(g.FULL_CALLS)
    if stage < 4:
        assert len(answer.encode()) <= 8000
    else:
        assert answer.startswith((repo / fixture_name('DIRECTIVES.md')).read_bytes().decode())
        assert 'Goals: 1; open projects: 1; closed projects: 0.' in answer
    if stage == 1:
        assert '# Learn\n\nRead' in answer and 'é' * 20 not in answer
    if stage == 2:
        assert 'Goal: Learn; how: 12000 UTF-8 bytes.' in answer
    if stage == 3:
        shown = re.findall(r'Goal: Goal (\d+);', answer)
        assert shown and shown == [f'{i:03}' for i in range(len(shown))]
        assert 'Project: Model;' not in answer
        assert f'Goals not shown: {170 - len(shown)}; open projects not shown: 1.' in answer


def test_cards_one_each_tags_closed_identity_and_idempotence(repo):
    h.set_goal(name='Learn café', how='Practice')
    open_fixture(serves='Learn café')
    state = store(repo)._current_state()
    goal = next(card for card in state.values() if card['origin']['logical'] == 'goal_files')
    project = next(card for card in state.values() if card['kind'] == 'project')
    assert (goal['kind'], goal['role'], goal['origin']['key'], goal['title']) == ('goal', 'current', 'learn-cafe', 'Learn café')
    assert (project['role'], project['origin']['key'], project['title']) == ('current', 'model', 'Model')
    assert 'goal:learn-cafe' in project['tags']
    h.advance_project(title='Model', next_step='Build')
    h.close_project(title='Model', outcome='completed')
    current = store(repo)._current_state()[project['id']]
    assert current['status'] == 'done' and current['origin']['file'] == fixture_name('records/projects/closed/model-SR-0042.md')
    assert c.read_card(repo / fixture_name('records'), project['id'])['card'] == current
    args = dict(tags=[], kinds=['project'], config_path=repo / 'config/cards.json', record_read=False)
    assert not c.recall(repo / fixture_name('records'), **args)['cards']
    assert c.recall(repo / fixture_name('records'), history=True, **args)['cards'][0]['id'] == project['id']
    before = store(repo).cards_path.read_bytes()
    for logical in ['goal_files', 'projects']:
        counts = recut(repo, logical)
        assert counts['made'] == counts['changed'] == counts['closed'] == 0
    assert store(repo).cards_path.read_bytes() == before
    assert '# Model' not in c.read_wake_card(repo, repo / 'naming.json',
                                              config_path=repo / 'config/cards.json')['text']


def test_wake_order_and_all_project_summaries_within_both_budgets(repo):
    for i in range(3):
        h.set_goal(name=f'Goal {i}', how=f'How {i}')
        open_fixture(f'Project {i}', target='Large target ' * 400, next_step=f'Next {i}', serves=f'Goal {i}')
    store(repo).write({'kind': 'log', 'text': '# Latest log\n\nPlan next: fixture action.'},
                      wake='w041', source='self', channel='mission',
                      origin={'logical': 'journal', 'file': fixture_name('records/shift-reports/SR-0041-2026-10-08.md'), 'position': 0, 'order': 1})
    cfg = json.loads((repo / 'config/cards.json').read_bytes())
    cfg['wake_sections']['what_i_was_doing'] = 650
    cfg['wake_card_bytes'] = 1400
    write(repo / 'config/cards.json', json.dumps(cfg))
    wake = c.read_wake_card(repo, repo / 'naming.json', config_path=repo / 'config/cards.json')
    body = wake['text'].split('## What I was doing\n\n', 1)[1].split('\n\n## What I owe', 1)[0]
    assert body.index('# Latest log') < body.index('# Ambition') < body.index('# Goal 0') < body.index('# Project 0')
    for i in range(3):
        assert f'# Project {i}\n\n## Next step\n\nNext {i}' in body
    assert 'Large target' not in body and 'left out' not in body
    assert len(body.encode()) <= 650 and wake['total_bytes'] <= 1400


@pytest.mark.parametrize('eol', [b'\n', b'\r\n'])
def test_operator_move_byte_exact_heading_idempotence_and_recut(repo, capsys, monkeypatch, eol):
    ambition = b'# Goals' + eol + eol + b'## Ambition' + eol + eol + 'Café 🚀'.encode() + eol + eol
    moved = b'## Now' + eol + b'- Old fixture goal' + eol + '历史'.encode() + eol
    write(repo / fixture_name('DIRECTIVES.md'), ambition + moved)
    write(repo / fixture_name('COMPLETED-DIRECTIVES.md'), b'Previous archive\r\n')
    recut(repo, 'goals')
    before = [card for card in store(repo)._current_state().values() if b'Old fixture goal' in card['text'].encode()]
    assert before
    assert g.move_old_list(repo, '2026-10-08') == len(moved)
    heading = b'\n## Moved by the operator on 2026-10-08: the goals list as it stood before goals and projects became separate files\n\n'
    assert (repo / fixture_name('DIRECTIVES.md')).read_bytes() == ambition
    assert (repo / fixture_name('COMPLETED-DIRECTIVES.md')).read_bytes() == b'Previous archive\r\n' + heading + moved
    stable = snapshot(repo)
    assert g.move_old_list(repo, '2026-10-08') == 0
    assert snapshot(repo) == stable
    recut(repo, 'goals')
    assert all(store(repo)._current_state()[card['id']]['status'] == 'done' for card in before)
    assert not (repo / fixture_name('records/goals')).exists() and not (repo / fixture_name('records/projects')).exists()
    monkeypatch.setattr(sys, 'argv', ['goals', 'move-old-list', '--repo-root', str(repo)])
    g.main()
    assert capsys.readouterr().out == 'Moved 0 bytes: no ## Now heading; nothing was changed.\n'


def test_no_now_heading_or_file_changes_nothing(repo):
    before = snapshot(repo)
    assert g.move_old_list(repo, '2026-10-08') == 0
    assert snapshot(repo) == before
    (repo / fixture_name('DIRECTIVES.md')).unlink()
    assert g.move_old_list(repo, '2026-10-08') == 0


def test_operator_module_command_on_fixture(repo):
    write(repo / fixture_name('DIRECTIVES.md'), b'# Ambition\n\n## Now\n- Invented old goal\n')
    import os
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    result = subprocess.run([sys.executable, '-m', 'tools.goals', 'move-old-list'],
                            cwd=repo, env=env, capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout == 'Moved 27 bytes.\n'
    result = subprocess.run([sys.executable, '-m', 'tools.goals', 'move-old-list'],
                            cwd=repo, env=env, capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout == 'Moved 0 bytes: no ## Now heading; nothing was changed.\n'


def test_help_and_all_new_calls_bind_to_real_signatures(repo):
    names = {naming.tool(n) for n in set(g.CALLS) | {'read_goals', 'update_goals', 'archive_completed_goals'}}
    help_text = h.help()
    group = help_text.split('Goals and projects\n', 1)[1].split('\n\n', 1)[0]
    for name in names:
        assert name + ':' in group
        assert 'Example: ' + name + '(' in h.help(tool=name)
    registry = {tool.name: tool for tool in asyncio.run(h.mcp.list_tools())}
    assert names <= registry.keys()
    calls = list(g.CALLS.values()) + [g.UPDATE_REFUSAL, g.FULL_CALLS]
    for string in calls:
        for match in re.finditer(r'(\w+)\(([^()]*)\)', string):
            call = ast.parse(match.group(), mode='eval').body
            assert not call.args
            kwargs = {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords}
            inspect.signature(getattr(h, next((key for key, value in naming.load()["tools"].items() if value == match.group(1)), match.group(1)))).bind(**kwargs)


def test_mcp_tools_execute_as_advertised(repo):
    async def run():
        for name, arguments in [('set_goal', {'name': 'Learn', 'how': 'Read\r\ncafé'}),
                                ('read_goal', {'name': 'Learn'}),
                                ('open_project', {'title': 'Model', 'target': 'Working', 'next_step': 'Sketch'}),
                                ('advance_project', {'title': 'Model', 'next_step': 'Build'}),
                                ('close_project', {'title': 'Model', 'outcome': 'completed'}),
                                ('read_project', {'title': 'Model'})]:
            result = await h.mcp.call_tool(name, arguments)
            content = result[0] if isinstance(result, tuple) else result
            assert content and 'REFUSED' not in ''.join(item.text for item in content if item.type == 'text')
    asyncio.run(run())


def test_target_headings_are_verbatim_and_never_mistaken_for_project_sections(repo):
    target = 'Café\r\n## Steps\r\n\r\n- [ ] Fake\r\n\r\n## Next step\r\n\r\nFake next'
    open_fixture(target=target, steps=['Real'])
    old = (repo / fixture_name('records/projects/model.md')).read_bytes()
    h.advance_project(title='Model', next_step='Real next', done_step='Real')
    expected = old.replace(b'- [ ] Real', b'- [x] Real').replace(b'\n\nSketch', b'\n\nReal next')
    assert (repo / fixture_name('records/projects/model.md')).read_bytes() == expected
    assert g.project_parts(h.read_project(title='Model'))['next_step'] == 'Real next'


def test_stage_three_includes_later_lines_that_fit_in_file_order(repo):
    open_fixture('A huge project', next_step='n' * 10000)
    open_fixture('B small project', next_step='Small next')
    answer = h.read_goals()
    assert g.NOTICES[3] in answer
    assert 'Project: B small project;' in answer
    assert 'Project: A huge project;' not in answer
    assert 'open projects not shown: 1.' in answer and len(answer.encode()) <= 8000


def test_ambition_only_overflow_without_new_folders(repo):
    write(repo / fixture_name('DIRECTIVES.md'), 'é' * 5000)
    answer = h.read_goals()
    assert answer.startswith('é' * 5000)
    assert g.NOTICES[4] in answer and 'Goals: 0; open projects: 0; closed projects: 0.' in answer


@pytest.mark.parametrize('ambition', ['a' * 7999, 'é' * 5000], ids=['7999-bytes', '10000-bytes'])
def test_ambition_only_required_overhead_is_exact_and_never_dropped(repo, ambition):
    write(repo / fixture_name('DIRECTIVES.md'), ambition)
    h.set_goal(name='Learn', how='Whole how')
    open_fixture('Open', next_step='Next open')
    open_fixture('Closed')
    h.close_project(title='Closed', outcome='completed')
    expected = '\n\n'.join((ambition, 'Goals: 1; open projects: 1; closed projects: 1.',
                             g.NOTICES[4], g.FULL_CALLS))
    answer = h.read_goals()
    assert answer == expected
    assert len(answer.encode()) > 8000
    assert answer.encode().startswith(ambition.encode())
    assert '# Learn' not in answer and '# Open' not in answer and '# Closed' not in answer
    assert len(answer.encode()) - len(ambition.encode()) == 188


def test_stage_three_notice_overhead_never_breaks_limit(repo):
    # Its longer omitted-count wording may not fit although the compact answer does.
    ambition = 'a' * (8000 - 188)
    write(repo / fixture_name('DIRECTIVES.md'), ambition)
    h.set_goal(name='Learn', how='x' * 9000)
    answer = h.read_goals()
    assert len(answer.encode()) <= 8000
    assert answer.startswith(ambition) and answer.endswith(g.FULL_CALLS)


OLD_LIST = '# Goals\n\n## Ambition\n\nKeep learning.\n\n## Now\n\n- An invented goal still in the old list.\n\n## Later\n\n- Another.\n'


def test_read_goals_shows_the_old_list_whole_until_a_file_replaces_it(repo):
    write(repo / fixture_name('DIRECTIVES.md'), OLD_LIST)
    assert h.read_goals() == OLD_LIST
    h.open_project(title='Invented project', target='A page.', next_step='Draft it.')
    answer = h.read_goals()
    # A project file exists, the old list has not been moved: both are shown, the list whole.
    assert answer.startswith(OLD_LIST + '\n\n# Invented project\n')
    assert answer.endswith('Closed projects: 0.')
    h.set_goal(name='Invented goal', how='By one step a wake.')
    answer = h.read_goals()
    assert answer.startswith(OLD_LIST) and '# Invented goal' in answer and '# Invented project' in answer


def test_read_goals_after_the_move_shows_only_the_ambition_and_the_files(repo):
    write(repo / fixture_name('DIRECTIVES.md'), OLD_LIST)
    h.set_goal(name='Invented goal', how='By one step a wake.')
    assert g.move_old_list(repo, '2026-10-08') > 0
    answer = h.read_goals()
    assert answer.startswith('# Goals\n\n## Ambition\n\nKeep learning.\n')
    assert '## Now' not in answer and '# Invented goal' in answer


def test_read_goals_old_list_is_whole_even_over_the_limit(repo):
    long_list = OLD_LIST + '- ' + 'x' * 9000 + '\n'
    write(repo / fixture_name('DIRECTIVES.md'), long_list)
    assert h.read_goals() == long_list


# Brief 21b regressions: all state lives in repo's invented tmp_path.
@pytest.mark.parametrize('second_wake', [42, 43], ids=['same-wake', 'later-wake'])
def test_title_reuse_preserves_two_closed_files_and_two_cards(repo, second_wake):
    open_fixture('Weekly audit', target='First target')
    first_id = next(card['id'] for card in store(repo)._current_state().values() if card['kind'] == 'project')
    h.close_project(title='Weekly audit', outcome='completed')
    first = repo / fixture_name('records/projects/closed/weekly-audit-SR-0042.md')
    first_bytes = first.read_bytes()
    first_card = store(repo)._current_state()[first_id]
    assert first_card['origin']['key'] == first.stem
    write(repo / 'budget.json', json.dumps({'wake_count': second_wake - 1}))
    assert open_fixture('Weekly audit', target='Second target') == 'Project "Weekly audit" opened.'
    assert h.read_project(title='Weekly audit') == h.read_project(title='Weekly audit', closed_in='') == (repo / fixture_name('records/projects/weekly-audit.md')).read_bytes().decode()
    assert h.read_project(title='Weekly audit', closed_in=fixture_name('SR-0042')).encode() == first_bytes
    h.close_project(title='Weekly audit', outcome='abandoned', reason='Finished this version.')
    second = repo / fixture_name('records/projects/closed') / (fixture_name('weekly-audit-SR-0042-2.md') if second_wake == 42 else fixture_name('weekly-audit-SR-0043.md'))
    assert first.read_bytes() == first_bytes
    assert h.read_project(title='Weekly audit') == second.read_bytes().decode() + fixture_name('\n\nEarlier closings: SR-0042.')
    assert h.read_project(title='Weekly audit', closed_in=fixture_name(f'SR-{second_wake:04d}')).encode() == second.read_bytes()
    if second_wake != 42:
        assert h.read_project(title='Weekly audit', closed_in=fixture_name('SR-0042')).encode() == first_bytes
    projects = [card for card in store(repo)._current_state().values() if card['kind'] == 'project']
    assert len(projects) == 2 and {card['status'] for card in projects} == {'done'}
    assert store(repo)._current_state()[first_id] == first_card
    assert {card['origin']['key'] for card in projects} == {first.stem, second.stem}
    for card in projects:
        assert c.read_card(repo / fixture_name('records'), card['id'])['card'] == card
    args = dict(tags=[], kinds=['project'], config_path=repo / 'config/cards.json', record_read=False)
    assert len(c.recall(repo / fixture_name('records'), history=True, **args)['cards']) == 2
    before = store(repo).cards_path.read_bytes()
    counts = recut(repo, 'projects')
    assert counts['made'] == counts['changed'] == counts['closed'] == 0
    assert store(repo).cards_path.read_bytes() == before


def test_third_closing_in_one_wake_uses_next_suffix(repo):
    for number in range(3):
        open_fixture('Weekly audit', target=f'Target {number}')
        h.close_project(title='Weekly audit', outcome='completed')
    folder = repo / fixture_name('records/projects/closed')
    assert {p.name for p in folder.glob('*.md')} == {
        fixture_name('weekly-audit-SR-0042.md'), fixture_name('weekly-audit-SR-0042-2.md'), fixture_name('weekly-audit-SR-0042-3.md')}
    assert h.read_project(title='Weekly audit') == (folder / fixture_name('weekly-audit-SR-0042-3.md')).read_bytes().decode() + fixture_name('\n\nEarlier closings: SR-0042, SR-0042.')
    assert len([card for card in store(repo)._current_state().values() if card['kind'] == 'project']) == 3


def test_old_and_new_closed_namings_coexist_and_open_does_not_overwrite_legacy_card(repo):
    open_fixture('Weekly audit', target='Legacy target')
    path = repo / fixture_name('records/projects/weekly-audit.md')
    legacy = repo / fixture_name('records/projects/closed/weekly-audit.md')
    write(legacy, path.read_bytes().replace(b'- **Status:** open', fixture_name(b'- **Status:** completed in SR-0040, 2026-10-06')))
    path.unlink()
    recut(repo, 'projects')
    legacy_bytes = legacy.read_bytes()
    legacy_card = next(card for card in store(repo)._current_state().values()
                       if card['origin']['file'] == fixture_name('records/projects/closed/weekly-audit.md'))
    assert h.read_project(title='Weekly audit').encode() == legacy_bytes
    open_fixture('Weekly audit', target='New target')
    h.close_project(title='Weekly audit', outcome='completed')
    latest = repo / fixture_name('records/projects/closed/weekly-audit-SR-0042.md')
    assert h.read_project(title='Weekly audit') == latest.read_bytes().decode() + fixture_name('\n\nEarlier closings: SR-0040.')
    assert h.read_project(title='Weekly audit', closed_in=fixture_name('SR-0040')).encode() == legacy_bytes
    assert h.read_project(title='Weekly audit', closed_in=fixture_name('SR-0042')).encode() == latest.read_bytes()
    assert legacy.read_bytes() == legacy_bytes and store(repo)._current_state()[legacy_card['id']] == legacy_card
    assert len([card for card in store(repo)._current_state().values() if card['kind'] == 'project']) == 2


@pytest.mark.parametrize('closed_in', [fixture_name('SR-0999'), '42', fixture_name('../SR-0042')])
def test_read_project_missing_or_invalid_wake_refuses_without_changes(repo, closed_in):
    open_fixture()
    h.close_project(title='Model', outcome='completed')
    before = snapshot(repo)
    answer = h.read_project(title='Model', closed_in=closed_in)
    assert 'REFUSED: nothing was changed.' in answer
    assert answer.endswith('Call ' + g.CALLS['read_project'] + '.')
    assert snapshot(repo) == before


@pytest.mark.parametrize('count', [0, 1, 2])
def test_drop_goal_names_serving_projects_exactly_without_editing_them(repo, count):
    h.set_goal(name='Learn', how='Read')
    open_fixture('Not serving')
    for i in range(count):
        open_fixture(f'Audit {i}', serves='Learn')
    open_fixture('Finished audit', serves='Learn')
    h.close_project(title='Finished audit', outcome='completed')
    folder = repo / fixture_name('records/projects')
    before = {p.relative_to(folder): p.read_bytes() for p in folder.rglob('*.md')}
    answer = h.drop_goal(name='Learn', reason='Changed direction.')
    expected = 'Goal "Learn" archived and removed.'
    if count:
        titles = ', '.join(f'"Audit {i}"' for i in range(count))
        expected += f' Open projects that named it under Serves: {titles}. Change one with serve_project(title="<title>", serves="<goal name or none>").'
    assert answer == expected
    assert {p.relative_to(folder): p.read_bytes() for p in folder.rglob('*.md')} == before
    if count:
        assert '- **Serves:** Learn' in h.read_project(title='Audit 0')
        assert '- **Serves:** Learn' in h.read_goals()
        wake = c.read_wake_card(repo, repo / 'naming.json', config_path=repo / 'config/cards.json')
        assert '- **Serves:** Learn' in wake['text']


@pytest.mark.parametrize('serves', ['Make café', 'none'])
def test_serve_project_preserves_all_other_bytes_and_updates_tag(repo, serves):
    h.set_goal(name='Learn', how='Read')
    h.set_goal(name='Make café', how='Practice')
    open_fixture(serves='Learn', target='Café\r\n目标')
    path = repo / fixture_name('records/projects/model.md')
    write(path, path.read_bytes().replace(b'\n', b'\r\n').replace(b'\r\r\n', b'\r\n'))
    original = path.read_bytes()
    recut(repo, 'projects')
    old_id = next(card['id'] for card in store(repo)._current_state().values() if card['kind'] == 'project')
    assert h.serve_project(title='Model', serves=serves) == 'Project "Model" Serves updated.'
    assert path.read_bytes() == original.replace(b'- **Serves:** Learn', ('- **Serves:** ' + serves).encode(), 1)
    card = store(repo)._current_state()[old_id]
    assert 'goal:learn' not in card['tags']
    assert ('goal:make-cafe' in card['tags']) == (serves != 'none')
    assert h.read_project(title='Model').encode() == path.read_bytes()


def test_serve_project_unknown_goal_or_closed_project_refuses_without_changes(repo):
    h.set_goal(name='Learn', how='Read')
    open_fixture()
    before = snapshot(repo)
    assert h.serve_project(title='Model', serves='Missing') == g.refusal('serve_project', 'Serves is not an existing goal. Existing goals: ["Learn"].')
    assert snapshot(repo) == before
    h.close_project(title='Model', outcome='completed')
    before = snapshot(repo)
    assert h.serve_project(title='Model', serves='none') == g.refusal('serve_project', 'No open project has that title.')
    assert snapshot(repo) == before


@pytest.mark.parametrize('text', ['Keep learning.', 'Écrire café.\r\n第二行 🚀\r\n', 'é' * 10000], ids=['sentence', 'crlf-unicode', 'large'])
def test_set_ambition_exact_bytes_backup_and_card_with_no_shrink_or_size_guard(repo, monkeypatch, text):
    h.set_goal(name='Learn', how='Read')
    old = (repo / fixture_name('DIRECTIVES.md')).read_bytes()
    monkeypatch.setattr(h, 'session_active', lambda **kwargs: pytest.fail('No content guard or admin override is needed'))
    assert h.set_ambition(text=text) == 'Standing ambition updated.'
    # The fixture file opens with two heading lines; they are kept, his text follows byte for byte.
    assert (repo / fixture_name('DIRECTIVES.md')).read_bytes() == b'# Goals\n\n## Ambition\n\n' + text.encode()
    backups = list(repo.glob(fixture_name('DIRECTIVES.md.*.bak')))
    assert len(backups) == 1 and backups[0].read_bytes() == old
    active = [card for card in store(repo)._current_state().values()
              if card['origin']['logical'] == 'goals' and card['status'] == 'live']
    assert active and text in ''.join(card['text'] for card in active)
    assert h.update_goals(new_text='Other') == g.UPDATE_REFUSAL
    assert 'set_ambition(text="<text>")' in g.UPDATE_REFUSAL


@pytest.mark.parametrize('old', [OLD_LIST, OLD_LIST.replace('\n', '\r\n')])
def test_set_ambition_unmoved_old_list_refuses_exactly_without_changes(repo, old):
    write(repo / fixture_name('DIRECTIVES.md'), old)
    before = snapshot(repo)
    assert h.set_ambition(text='New ambition.') == g.AMBITION_OLD_LIST_REFUSAL
    assert snapshot(repo) == before
    assert g.AMBITION_OLD_LIST_REFUSAL.endswith(fixture_name('amend_directives(new_text="<the whole file>").'))


@pytest.mark.parametrize('text', ['', ' \r\n\t '])
def test_set_ambition_blank_refuses_without_backup_or_other_changes(repo, text):
    before = snapshot(repo)
    assert h.set_ambition(text=text) == g.refusal('set_ambition', 'Text must be a non-blank string.')
    assert snapshot(repo) == before


def test_follow_up_writers_refuse_under_stop_marker(repo):
    open_fixture()
    write(repo / 'STOP.md', b'Invented stop')
    before = snapshot(repo)
    assert h.serve_project(title='Model', serves='none') == g.refusal('serve_project', 'STOP.md is set.')
    assert h.set_ambition(text='New ambition.') == g.refusal('set_ambition', 'STOP.md is set.')
    assert snapshot(repo) == before


def test_follow_up_tools_help_and_mcp_execution(repo):
    group = h.help().split('Goals and projects\n', 1)[1].split('\n\n', 1)[0]
    for name in ['set_ambition', 'serve_project']:
        assert name + ':' in group and 'Example: ' + name + '(' in h.help(tool=name)
    async def run():
        for name, kwargs in [
            ('set_ambition', {'text': 'Écrire.\r\nContinue.'}),
            ('set_goal', {'name': 'Learn', 'how': 'Read'}),
            ('open_project', {'title': 'Model', 'target': 'Working', 'next_step': 'Sketch'}),
            ('serve_project', {'title': 'Model', 'serves': 'Learn'}),
        ]:
            result = await h.mcp.call_tool(name, kwargs)
            content = result[0] if isinstance(result, tuple) else result
            assert content and 'REFUSED' not in ''.join(item.text for item in content if item.type == 'text')
    asyncio.run(run())


def test_set_ambition_keeps_the_heading_unless_he_gives_one(repo):
    write(repo / fixture_name('DIRECTIVES.md'), b'## The standing ambition\r\n\r\nOld text.\r\n')
    assert h.set_ambition(text='New text.\r\n') == 'Standing ambition updated.'
    assert (repo / fixture_name('DIRECTIVES.md')).read_bytes() == b'## The standing ambition\r\n\r\nNew text.\r\n'
    assert h.set_ambition(text='# My own heading\n\nOther text.\n') == 'Standing ambition updated.'
    assert (repo / fixture_name('DIRECTIVES.md')).read_bytes() == b'# My own heading\n\nOther text.\n'


def test_set_ambition_on_a_file_with_no_heading_replaces_it_whole(repo):
    write(repo / fixture_name('DIRECTIVES.md'), b'Just a sentence.\n')
    assert h.set_ambition(text='Another sentence.') == 'Standing ambition updated.'
    assert (repo / fixture_name('DIRECTIVES.md')).read_bytes() == b'Another sentence.'
