"""Named-file commits use invented state and disposable git repositories."""
import ast
import json
from pathlib import Path
import subprocess
import time

import pytest

from tools import commit, harness_mcp as h, release


def git(root, *arguments):
    result = subprocess.run(['git', *arguments], cwd=root, capture_output=True)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    return result.stdout


@pytest.fixture(autouse=True)
def operator_logs(tmp_path_factory, monkeypatch):
    # Timing lines and git traces from these tests stay out of the real log.
    logs = tmp_path_factory.mktemp('operator') / 'logs'
    monkeypatch.setattr(commit.paths, 'operator_log_dir', lambda: logs)
    return logs


@pytest.fixture
def repo(tmp_path, monkeypatch):
    git(tmp_path, 'init')
    git(tmp_path, 'config', 'user.name', 'Test Agent')
    git(tmp_path, 'config', 'user.email', 'test@example.com')
    git(tmp_path, 'config', 'core.autocrlf', 'false')
    for name in ('changed.txt', 'deleted.txt', 'other.txt', 'unchanged.txt'):
        (tmp_path / name).write_text('initial\n')
    (tmp_path / '.gitignore').write_text('ignored.txt\nrecords/private/\n')
    git(tmp_path, 'add', '--', '.')
    git(tmp_path, 'commit', '-m', 'Initial')
    (tmp_path / 'changed.txt').write_text('changed\n')
    (tmp_path / 'budget.json').write_text('{"wake_count":150}\n')
    monkeypatch.setattr(h, 'REPO', tmp_path)
    monkeypatch.setattr(release, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(h, '_load_budget', lambda: json.loads((tmp_path / 'budget.json').read_text()))
    hook(tmp_path, 'exit 0')
    yield tmp_path
    # Finish every worker before tmp_path can be removed.
    deadline = time.monotonic() + 10
    while commit._state(tmp_path)['running'] and time.monotonic() < deadline:
        time.sleep(.01)
    assert not commit._state(tmp_path)['running']


def hook(root, body):
    path = root / '.git/hooks/pre-commit'
    path.write_text('#!/bin/sh\n' + body + '\n', encoding='utf-8', newline='\n')
    path.chmod(0o755)


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob('*') if path.is_file()}


def test_modified_new_deleted_and_head(repo):
    (repo / 'nested').mkdir()
    (repo / 'nested/new.txt').write_text('new\n')
    (repo / 'deleted.txt').unlink()
    (repo / 'other.txt').write_text('leave outside\n')
    result = h.commit_wake(files=['changed.txt', 'nested/new.txt', 'deleted.txt'], summary='Record wake')
    assert result['status'] == 'COMMITTED', result
    assert result['message'] == 'SR-0150: Record wake'
    assert result['commit'] == git(repo, 'rev-parse', '--short', 'HEAD').decode().strip()
    assert git(repo, 'log', '-1', '--format=%s').decode().strip() == result['message']
    committed = set(git(repo, 'diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD').decode().splitlines())
    assert committed == set(result['files']) | set(result.get('harness_files', []))
    assert git(repo, 'status', '--porcelain', '--', 'other.txt')


@pytest.mark.parametrize('files,summary,rule', [
    ([], 'Summary', 'non-empty'),
    ('changed.txt', 'Summary', 'list'),
    ([1], 'Summary', 'strings'),
    (['changed.txt'], '  ', 'blank'),
    (['/absolute.txt'], 'Summary', 'relative'),
    (['C:/absolute.txt'], 'Summary', 'relative'),
    (['-bad.txt'], 'Summary', 'dash'),
    (['*.txt'], 'Summary', 'file-search'),
    (['?.txt'], 'Summary', 'file-search'),
    (['[a].txt'], 'Summary', 'file-search'),
    (['.'], 'Summary', 'dot'),
    (['sub/../changed.txt'], 'Summary', 'parent'),
    (['..\\changed.txt'], 'Summary', 'parent'),
    (['unchanged.txt'], 'Summary', 'changed'),
    (['missing.txt'], 'Summary', 'changed'),
    (['changed.txt'], 'two\nlines', 'one line'),
    (['changed.txt'], 'two\rlines', 'one line'),
    (['changed.txt'], 'SR-0150: wrong', 'wake number'),
])
def test_refusals_preserve_every_byte(repo, files, summary, rule):
    before = snapshot(repo)
    result = h.commit_wake(files=files, summary=summary)
    assert result['status'] == 'REFUSED'
    assert rule in result['reason']
    assert snapshot(repo) == before


def test_containment_symlink(repo, tmp_path):
    outside = tmp_path.parent / (tmp_path.name + '-outside.txt')
    outside.write_text('outside')
    try:
        (repo / 'escape.txt').symlink_to(outside)
    except OSError:
        pytest.skip('Symlinks unavailable')
    before = snapshot(repo)
    result = h.commit_wake(files=['escape.txt'], summary='Summary')
    assert result['status'] == 'REFUSED' and 'inside' in result['reason']
    assert snapshot(repo) == before


def test_ignored_file(repo):
    (repo / 'ignored.txt').write_text('ignored')
    before = snapshot(repo)
    result = h.commit_wake(files=['ignored.txt'], summary='Summary')
    assert result['status'] == 'REFUSED' and 'ignored.txt' in result['reason']
    assert snapshot(repo) == before


def test_private_tracked_file(repo):
    path = repo / 'records/private/invented.txt'
    path.parent.mkdir(parents=True)
    path.write_text('initial')
    git(repo, 'add', '-f', '--', 'records/private/invented.txt')
    git(repo, 'commit', '-m', 'Invented private fixture')
    path.write_text('changed')
    before = snapshot(repo)
    result = h.commit_wake(files=['records/private/invented.txt'], summary='Summary')
    assert result['status'] == 'REFUSED' and 'private' in result['reason']
    assert snapshot(repo) == before


def test_folder(repo):
    (repo / 'changed.txt').unlink()
    (repo / 'changed.txt').mkdir()
    (repo / 'changed.txt/file.txt').write_text('new')
    before = snapshot(repo)
    result = h.commit_wake(files=['changed.txt'], summary='Summary')
    assert result['status'] == 'REFUSED' and 'names a folder' in result['reason']
    assert snapshot(repo) == before


def test_hook_runs_in_a_plain_git_environment(repo, monkeypatch):
    # The real hook runs the suite, which calls `git check-ignore`; that
    # command refuses literal pathspecs. The hook must not inherit them, nor
    # a repository override that would aim the suite at the real files.
    monkeypatch.setenv('AGENT_HARNESS_REPO', str(repo))
    monkeypatch.setenv('GIT_LITERAL_PATHSPECS', '1')  # inherited by the harness itself
    hook(repo, 'git check-ignore -q ignored.txt || exit 1\n'
               'test -z "$GIT_LITERAL_PATHSPECS" || exit 1\n'
               'test -z "$AGENT_HARNESS_REPO" || exit 1\n'
               'exit 0')
    result = h.commit_wake(files=['changed.txt'], summary='Record wake')
    assert result['status'] == 'COMMITTED', result


def test_unnamed_staged_file(repo):
    (repo / 'other.txt').write_text('other')
    git(repo, 'add', '--', 'other.txt')
    before = snapshot(repo)
    result = h.commit_wake(files=['changed.txt'], summary='Summary')
    assert result['status'] == 'REFUSED' and 'other.txt' in result['reason'] and 'staged' in result['reason']
    assert snapshot(repo) == before


def test_hook_refusal_is_last_line_and_unstages(repo):
    hook(repo, "echo first\necho second >&2\nprintf '%s\\n\\n' '" + 'x' * 250 + "'\nexit 1")
    head = git(repo, 'rev-parse', 'HEAD')
    result = h.commit_wake(files=['changed.txt'], summary='Summary')
    assert result == {'status': 'HOOK_REFUSED', 'hook_line': 'x' * 200}
    assert git(repo, 'diff', '--cached') == b''
    assert git(repo, 'rev-parse', 'HEAD') == head
    assert (repo / 'changed.txt').read_text() == 'changed\n'
    assert h.commit_status() == result
    sentence = 'The previous wake commit was refused by its hook.'
    assert sentence in h.wake_status()
    assert sentence not in h.wake_status()


@pytest.mark.asyncio
async def test_slow_hook_and_second_call(repo):
    config = repo / 'config'
    config.mkdir()
    (config / 'harness.json').write_text('{"commit_wait_seconds":0.01}')
    hook(repo, 'echo ready > .git/commit-test-ready\nsleep 3\nexit 0')
    started = time.monotonic()
    response = await h.mcp.call_tool('commit_wake', {'files': ['changed.txt'], 'summary': 'Summary'})
    returned_seconds = time.monotonic() - started
    assert 'STILL_RUNNING' in str(response)
    assert h.commit_status() == {'status': 'STILL_RUNNING'}
    assert returned_seconds < .9
    deadline = time.monotonic() + 5
    while not (repo / '.git/commit-test-ready').exists() and time.monotonic() < deadline:
        time.sleep(.01)
    assert (repo / '.git/commit-test-ready').exists()
    before = snapshot(repo)
    assert h.commit_wake(files=['other.txt'], summary='Summary')['reason'] == 'commit_wake is already running.'
    assert snapshot(repo) == before
    deadline = time.monotonic() + 10
    while h.commit_status()['status'] == 'STILL_RUNNING' and time.monotonic() < deadline:
        time.sleep(.02)
    assert h.commit_status()['status'] == 'COMMITTED'
    saved = json.loads((repo / commit.RESULT_PATH).read_text())
    assert saved['result'] == h.commit_status()
    print('BRIEF22_TIMING=' + json.dumps({'wait_seconds': 0.01, 'hook_sleep_seconds': 3,
          'mcp_call_seconds': returned_seconds, 'completion_seconds': time.monotonic() - started,
          'initial_status': 'STILL_RUNNING', 'final_status': h.commit_status()['status']}))


def test_stop_marker(repo):
    (repo / 'STOP.md').write_text('Standing')
    before = snapshot(repo)
    assert h.commit_wake(files=['changed.txt'], summary='Summary')['status'] == 'REFUSED'
    assert h.commit_status() == {'status': 'NONE'}
    assert snapshot(repo) == before


def test_log_first_and_since_previous_call(repo):
    (repo / 'budget.json').write_text('{"wake_count":0}')
    before = snapshot(repo)
    assert 'report comes first' in h.commit_wake(files=['changed.txt'], summary='Summary')['reason']
    assert snapshot(repo) == before
    # A wake opens (count 149), writes its log (150) and commits.
    commit.notice(repo, 'conversation-a', 149)
    (repo / 'budget.json').write_text('{"wake_count":150}')
    assert h.commit_wake(files=['changed.txt'], summary='Summary')['status'] == 'COMMITTED'
    assert h.commit_wake(files=['changed.txt'], summary='Summary') == {'status': 'NOTHING_TO_COMMIT', 'files': ['changed.txt']}
    # The same wake may commit again: a retry or a forgotten file, also
    # after a second status call and after the harness restarts.
    (repo / 'changed.txt').write_text('another change')
    assert h.commit_wake(files=['changed.txt'], summary='Forgotten')['message'] == 'SR-0150: Forgotten'
    commit.notice(repo, 'conversation-a', 150)
    commit._states.pop(str(repo.resolve()), None)
    (repo / 'changed.txt').write_text('a third change')
    assert h.commit_wake(files=['changed.txt'], summary='Again')['message'] == 'SR-0150: Again'
    # The next wake is a new conversation; it must write its log first.
    commit.notice(repo, 'conversation-b', 150)
    (repo / 'changed.txt').write_text('a fourth change')
    before = snapshot(repo)
    assert 'report comes first' in h.commit_wake(files=['changed.txt'], summary='Summary')['reason']
    assert snapshot(repo) == before
    (repo / 'budget.json').write_text('{"wake_count":151}')
    assert h.commit_wake(files=['changed.txt'], summary='Next')['message'] == 'SR-0151: Next'
    # A wake that wrote its log and never committed does not lend its number
    # to the wake after it.
    (repo / 'budget.json').write_text('{"wake_count":152}')
    commit.notice(repo, 'conversation-c', 152)
    (repo / 'changed.txt').write_text('a fifth change')
    assert 'report comes first' in h.commit_wake(files=['changed.txt'], summary='Summary')['reason']
    # A conversation that cannot be named counts as a new wake.
    commit.notice(repo, None, 152)
    assert 'report comes first' in h.commit_wake(files=['changed.txt'], summary='Summary')['reason']
    (repo / 'budget.json').write_text('{"wake_count":153}')
    assert h.commit_wake(files=['changed.txt'], summary='Later')['message'] == 'SR-0153: Later'


def test_persisted_result_and_unfinished_notice(repo):
    directory = repo / commit.RESULT_PATH.parent
    directory.mkdir(parents=True)
    (repo / commit.RESULT_PATH).write_text(json.dumps({'result': {'status':'STILL_RUNNING'}, 'wake':149, 'notice':True}))
    commit._states.pop(str(repo.resolve()), None)
    assert h.commit_status() == {'status': 'STILL_RUNNING'}
    assert commit.notice(repo) == 'The previous wake commit has no recorded completion.'
    assert commit.notice(repo) == ''


@pytest.mark.asyncio
async def test_fastmcp_and_help(repo):
    result = await h.mcp.call_tool('commit_wake', {'files':['changed.txt'], 'summary':'Summary'})
    assert 'COMMITTED' in str(result)
    assert 'NONE' not in str(await h.mcp.call_tool('commit_status', {}))
    example = h.help(tool='commit_wake').split('Example: ')[1]
    expression = ast.parse(example, mode='eval').body
    assert {keyword.arg for keyword in expression.keywords} == {'files','summary'}
    assert ast.literal_eval(expression.keywords[0].value) == ['DIRECTIVES.md']


def test_only_five_git_commands_in_source():
    tree = ast.parse(Path(commit.__file__).read_text(encoding='utf-8'))
    commands = [node for node in ast.walk(tree) if isinstance(node, ast.List)
                and node.elts and isinstance(node.elts[0], ast.Constant) and node.elts[0].value == 'git']
    allowed = {('git','status','--porcelain=v1','-z'), ('git','add','--'),
               ('git','commit','-m'), ('git','rev-parse','--short','HEAD'), ('git','reset','-q','--')}
    found = set()
    for node in commands:
        words = tuple(item.value for item in node.elts if isinstance(item, ast.Constant))
        assert words in allowed
        found.add(words)
    assert found == allowed
    assert not any(isinstance(node, ast.keyword) and node.arg == 'shell' and isinstance(node.value, ast.Constant)
                   and node.value.value is True for node in ast.walk(tree))


# --- Brief 30: names he can't see, files the harness keeps, timings ----------

def _committed(repo):
    return set(git(repo, 'diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD').decode().splitlines())


def test_a_wrong_name_lists_the_files_that_changed(repo):
    (repo / 'records/threads').mkdir(parents=True)
    (repo / 'records/threads/a-short-na.md').write_text('thread\n')
    (repo / 'records/private').mkdir(parents=True)
    (repo / 'records/private/secret.txt').write_text('private\n')
    result = h.commit_wake(files=['records/threads/a-short-name-in-full.md'], summary='Record wake')
    assert result['status'] == 'REFUSED'
    assert result['reason'] == ("'records/threads/a-short-name-in-full.md' is not reported as a changed "
                                "file by git. Changed files: changed.txt, records/threads/a-short-na.md.")
    assert 'secret' not in result['reason'] and 'budget.json' not in result['reason']


def test_a_long_list_of_changes_is_cut_with_a_count(repo):
    for n in range(35):
        (repo / f'new{n:02d}.txt').write_text('x\n')
    reason = h.commit_wake(files=['absent.txt'], summary='Record wake')['reason']
    assert reason.endswith(', and 6 more.')
    assert reason.count(', ') == 30


def test_nothing_else_changed_says_so(repo):
    git(repo, 'add', '--', 'changed.txt', 'budget.json')
    git(repo, 'commit', '-m', 'clean up')
    reason = h.commit_wake(files=['absent.txt'], summary='Record wake')['reason']
    assert reason.endswith('No other files have changed.')


def test_harness_kept_files_go_with_his_commit(repo):
    (repo / 'records/cards').mkdir(parents=True)
    (repo / 'records/cards/index.json').write_text('{}\n')
    (repo / 'records/seen.json').write_text('{}\n')
    result = h.commit_wake(files=['changed.txt'], summary='Record wake')
    assert result['status'] == 'COMMITTED'
    assert result['files'] == ['changed.txt']
    assert result['harness_files'] == ['budget.json', 'records/cards/index.json', 'records/seen.json']
    assert _committed(repo) == {'changed.txt', 'budget.json', 'records/cards/index.json', 'records/seen.json'}


def test_unchanged_harness_files_are_not_reported(repo):
    git(repo, 'add', '--', 'budget.json')
    git(repo, 'commit', '-m', 'budget')
    result = h.commit_wake(files=['changed.txt'], summary='Record wake')
    assert result['status'] == 'COMMITTED' and 'harness_files' not in result
    assert _committed(repo) == {'changed.txt'}


def test_naming_a_harness_file_himself_still_works(repo):
    result = h.commit_wake(files=['changed.txt', 'budget.json'], summary='Record wake')
    assert result['status'] == 'COMMITTED'
    assert result['files'] == ['changed.txt', 'budget.json'] and 'harness_files' not in result


def test_a_private_harness_file_is_never_added(repo, monkeypatch):
    monkeypatch.setattr(release, 'is_private', lambda name: name == 'budget.json')
    result = h.commit_wake(files=['changed.txt'], summary='Record wake')
    assert result['status'] == 'COMMITTED' and 'harness_files' not in result
    assert 'budget.json' not in _committed(repo)


def test_a_failed_commit_unstages_the_harness_files_too(repo):
    hook(repo, 'exit 1')
    result = h.commit_wake(files=['changed.txt'], summary='Record wake')
    deadline = time.monotonic() + 10
    while result['status'] == 'STILL_RUNNING' and time.monotonic() < deadline:
        time.sleep(.05)
        result = h.commit_status()
    assert result['status'] == 'HOOK_REFUSED'
    assert not git(repo, 'diff', '--cached', '--name-only').strip()


def test_every_call_writes_a_timing_line_without_names(repo, monkeypatch):
    # The writer runs in the background; here its lines are caught directly,
    # so the test reads what was written without racing that thread.
    caught = []
    monkeypatch.setattr(commit, '_write_timing', lambda root, line: caught.append(line))
    h.commit_wake(files=['absent.txt'], summary='Secret summary words')
    h.commit_wake(files=['changed.txt'], summary='Secret summary words')
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and (commit._state(repo)['running']
                                           or not any('call=commit_worker' in line for line in caught)):
        time.sleep(.05)
    text = ''.join(caught)
    lines = text.splitlines()
    assert sum('call=commit_wake' in line for line in lines) == 2
    refused = next(line for line in lines if 'status=REFUSED' in line)
    assert 'checks=' in refused and 'lock=' in refused and 'returned_after=' in refused
    worker = next(line for line in lines if 'call=commit_worker' in line)
    call_id = worker.split('id=')[1].split()[0]
    assert any('call=commit_wake' in line and f'id={call_id}' in line for line in lines)
    assert any('call=commit_worker' in line and 'commit_and_hooks=' in line and 'status=COMMITTED' in line
               for line in lines)
    assert 'changed.txt' not in text and 'Secret' not in text and 'absent' not in text


def test_the_timing_writer_appends_to_the_operator_log(tmp_path, monkeypatch):
    logs = tmp_path / 'logs'
    monkeypatch.setattr(commit.paths, 'operator_log_dir', lambda: logs)
    commit._write_timing(tmp_path / 'repo', 'first\n')
    commit._write_timing(tmp_path / 'repo', 'second\n')
    assert (logs / 'commit-timing.log').read_text(encoding='utf-8') == 'first\nsecond\n'


def test_an_unwritable_timing_log_changes_nothing(repo, tmp_path, monkeypatch):
    blocker = tmp_path.parent / (tmp_path.name + '-blocked')
    blocker.write_text('a file where the folder should be')
    monkeypatch.setattr(commit.paths, 'operator_log_dir', lambda: blocker / 'logs')
    result = h.commit_wake(files=['changed.txt'], summary='Record wake')
    assert result['status'] in ('COMMITTED', 'STILL_RUNNING')


def test_no_timing_log_inside_the_repository(repo, monkeypatch):
    monkeypatch.setattr(commit.paths, 'operator_log_dir', lambda: repo / 'logs')
    h.commit_wake(files=['changed.txt'], summary='Record wake')
    deadline = time.monotonic() + 10
    while commit._state(repo)['running'] and time.monotonic() < deadline:
        time.sleep(.01)
    assert not (repo / 'logs').exists()


def test_a_folder_in_place_of_a_harness_file_is_never_swept_in(repo, monkeypatch):
    # Astra, 15 Oct: budget.json replaced by a folder would have added the
    # folder's whole contents, a private file among them.
    git(repo, 'add', '--', 'budget.json')
    git(repo, 'commit', '-m', 'budget')
    (repo / 'budget.json').unlink()
    (repo / 'budget.json').mkdir()
    (repo / 'budget.json/inside.txt').write_text('swept\n')
    (repo / 'budget.json/private.txt').write_text('private\n')
    monkeypatch.setattr(release, 'is_private', lambda name: name.endswith('private.txt'))
    monkeypatch.setattr(h, '_load_budget', lambda: {'wake_count': 150})
    result = h.commit_wake(files=['changed.txt'], summary='Record wake')
    assert result['status'] == 'COMMITTED' and 'harness_files' not in result
    assert _committed(repo) == {'changed.txt'}


def test_a_deleted_harness_file_still_goes_along(repo, monkeypatch):
    (repo / 'records').mkdir()
    (repo / 'records/seen.json').write_text('{}\n')
    git(repo, 'add', '--', 'records/seen.json', 'budget.json')
    git(repo, 'commit', '-m', 'seen')
    (repo / 'records/seen.json').unlink()
    monkeypatch.setattr(h, '_load_budget', lambda: {'wake_count': 150})
    result = h.commit_wake(files=['changed.txt'], summary='Record wake')
    assert result['status'] == 'COMMITTED' and result['harness_files'] == ['records/seen.json']


def test_a_slow_timing_log_never_holds_up_the_call(repo, monkeypatch):
    import threading as _threading
    gate = _threading.Event()
    monkeypatch.setattr(commit, '_write_timing', lambda root, line: gate.wait(30))
    started = time.monotonic()
    result = h.commit_wake(files=['absent.txt'], summary='Record wake')
    assert result['status'] == 'REFUSED' and time.monotonic() - started < 5
    gate.set()


def test_git_never_inherits_the_servers_stdin(repo, monkeypatch):
    seen = []
    real = subprocess.run

    def spy(*arguments, **options):
        seen.append(options.get('stdin'))
        return real(*arguments, **options)
    monkeypatch.setattr(commit.subprocess, 'run', spy)
    commit._run(repo, ['git', 'status', '--porcelain=v1', '-z'])
    commit._run(repo, ['git', 'commit', '--dry-run', '-m', 'x'])
    assert seen == [subprocess.DEVNULL, subprocess.DEVNULL]


def test_each_status_check_leaves_a_git_trace_outside_the_repository(repo, operator_logs, monkeypatch):
    caught = []
    monkeypatch.setattr(commit, '_write_timing', lambda root, line: caught.append(line))
    h.commit_wake(files=['absent.txt'], summary='Record wake')
    wait_for(caught)
    call_id = caught[0].split('id=')[1].split()[0]
    trace = operator_logs / 'git-trace' / f'status-{call_id}.txt'
    assert trace.is_file() and trace.stat().st_size > 0
    assert not list(repo.rglob('status-*.txt'))


def test_no_git_trace_inside_the_repository(repo, monkeypatch):
    monkeypatch.setattr(commit.paths, 'operator_log_dir', lambda: repo / 'logs')
    result = h.commit_wake(files=['absent.txt'], summary='Record wake')
    assert result['status'] == 'REFUSED'
    assert not (repo / 'logs').exists()


def test_old_git_traces_are_removed(operator_logs, repo):
    folder = operator_logs / 'git-trace'
    folder.mkdir(parents=True)
    for number in range(commit.TRACE_KEEP + 5):
        (folder / f'status-old{number:02d}.txt').write_text('x')
    target = commit._trace_target(repo, 'new')
    assert target == folder / 'status-new.txt'
    assert len(list(folder.glob('status-*.txt'))) == commit.TRACE_KEEP - 1


def test_the_timing_line_names_git_variables_without_values(repo, monkeypatch):
    caught = []
    monkeypatch.setattr(commit, '_write_timing', lambda root, line: caught.append(line))
    monkeypatch.setenv('GIT_EXAMPLE_SETTING', 'secret-value-here')
    h.commit_wake(files=['absent.txt'], summary='Record wake')
    wait_for(caught)
    assert 'GIT_EXAMPLE_SETTING' in caught[0] and 'secret-value-here' not in caught[0]


def wait_for(caught):
    deadline = time.monotonic() + 10
    while not caught and time.monotonic() < deadline:
        time.sleep(.01)


def test_a_planted_trace_file_is_never_written_through(operator_logs, repo):
    folder = operator_logs / 'git-trace'
    folder.mkdir(parents=True)
    (folder / 'status-taken.txt').write_text('already here')
    assert commit._trace_target(repo, 'taken') is None
    assert (folder / 'status-taken.txt').read_text() == 'already here'


def test_the_trace_never_copies_variable_or_config_values(repo, monkeypatch):
    seen = []
    real = commit._run

    def spy(root, arguments, extra=None):
        seen.append(extra)
        return real(root, arguments, extra)
    monkeypatch.setattr(commit, '_run', spy)
    h.commit_wake(files=['absent.txt'], summary='Record wake')
    assert seen[0]['GIT_TRACE2_ENV_VARS'] == '' and seen[0]['GIT_TRACE2_CONFIG_PARAMS'] == ''


def test_status_warning_does_not_hide_first_changed_file(repo, monkeypatch):
    actual = commit.subprocess.run

    def warning(arguments, **kwargs):
        result = actual(arguments, **kwargs)
        if arguments[:2] == ["git", "status"]:
            notice = b"warning: fixture ignore configuration unavailable\n"
            if kwargs.get("stderr") == subprocess.STDOUT:
                result.stdout = notice + result.stdout
            else:
                result.stderr = notice
        return result

    monkeypatch.setattr(commit.subprocess, "run", warning)
    result = h.commit_wake(files=["changed.txt"], summary="Store fixture change")
    assert result["status"] == "COMMITTED", result
    assert "changed.txt" in result["files"]
