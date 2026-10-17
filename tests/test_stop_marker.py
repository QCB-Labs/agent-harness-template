"""Stop enforcement, crash boundaries and operator-only removal; invented state."""
import ast
from concurrent.futures import ThreadPoolExecutor
import datetime
import inspect
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools import harness_mcp as h, square_mcp as square, market_mcp as market
from tools import authority, names as scheme, phase, stop_marker as stop, stop_check, lift_stop

NOW = datetime.datetime(2026, 10, 4, 12, tzinfo=datetime.timezone.utc)
CID = "11111111-1111-4111-8111-111111111111"
MODULES = (h, square, market)
WRITES = {
    h: {'check_inbox','reply_to_message','send_new_thread','request_new_correspondent',
        'update_credit_balance','update_quotas','update_last_seen','file_unrecorded','update_goals',
        'update_identity_evolving','archive_completed_goals','append_lesson','append_wishlist','propose_city_rule',
        'write_operator_message','authorise_spend','update_map','upsert_resident',
        'notify_crash','update_almanac','record_place','stage_draft','archive_draft','update_open_threads',
        'upsert_work','update_works_index','set_goal','drop_goal','open_project','advance_project','close_project','serve_project','set_ambition'},
    square: {'post_dispatch','post_comment','vote_post','bind_signing_key'},
    market: {'acquire_free_artifact','purchase_listing','pay_listing_fee','create_world_draft',
        'cancel_world_draft','activate_world_listing','create_listing','write_store_line','withdraw_listing'},
}
WRITE_CASES = [(m, name) for m, names in WRITES.items() for name in sorted(names)]


def fail(*args, **kwargs):
    raise AssertionError("a blocked dependency ran")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'tools').mkdir()
    (tmp_path / 'tools/harness_mcp.py').write_text('# invented harness\n', encoding='utf-8')
    (tmp_path / 'GEMINI.md').write_text('# invented root\n', encoding='utf-8')
    (tmp_path / 'STOP_LOG.md').write_text(stop.LOG_HEADER, encoding='utf-8')
    for module, name in ((h, 'REPO'), (square, 'REPO_ROOT'), (market, 'REPO_ROOT')):
        monkeypatch.setattr(module, name, tmp_path)
    monkeypatch.setattr(h, '_utc_now', lambda: NOW)
    monkeypatch.setattr(h, '_send_email', lambda **kwargs: False)
    monkeypatch.setattr(h, '_recut_after_write', lambda *args: None)
    monkeypatch.setattr(h, 'canary_summary', lambda *args: 'Invented canary result.')
    return tmp_path


def raise_marker(repo, reason='Invented reason'):
    h.raise_stop(reason=reason)
    return (repo / 'STOP.md').read_bytes().decode('utf-8')


def snapshot(repo):
    return {p.relative_to(repo): p.read_bytes() for p in repo.rglob('*') if p.is_file()}


@pytest.mark.parametrize('state,expected,output', [
    ('clear', 0, 'clear to wake'),
    ('wrong', 2, 'NOT THE REPO ROOT - DO NOT WAKE'),
    ('marker', 1, 'STOP MARKER PRESENT - DO NOT WAKE'),
])
def test_preflight_outcomes(repo, capsys, state, expected, output):
    if state == 'wrong':
        (repo / 'GEMINI.md').unlink()
    if state == 'marker':
        raise_marker(repo)
    assert stop_check.main() == expected
    assert capsys.readouterr().out == output + '\n'
    if state == 'marker':
        assert ' | honoured | ' in (repo / 'STOP_LOG.md').read_text(encoding='utf-8')
        assert 'stop_check.py' in (repo / 'STOP_LOG.md').read_text(encoding='utf-8')


@pytest.mark.parametrize('failure', ['folder', 'marker', 'log'])
def test_preflight_fails_closed(repo, monkeypatch, capsys, failure):
    if failure == 'folder':
        monkeypatch.setattr(Path, 'cwd', fail)
    elif failure == 'marker':
        monkeypatch.setattr(stop_check, 'marker_present', fail)
    else:
        raise_marker(repo)
        (repo / 'STOP_LOG.md').unlink()
        (repo / 'STOP_LOG.md').mkdir()
    assert stop_check.main() == 2
    assert capsys.readouterr().out == 'STOP CHECK FAILED - DO NOT WAKE\n'


def test_preflight_standalone_without_harness_import(repo):
    (repo / 'tools/harness_mcp.py').write_text('raise AssertionError("broken harness")\n')
    result = subprocess.run([sys.executable, str(Path(stop_check.__file__))], cwd=repo,
                            capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout == 'clear to wake\n'
    tree = ast.parse(Path(stop_check.__file__).read_text(encoding='utf-8'))
    assert not any(isinstance(n, ast.ImportFrom) and ('harness' in (n.module or '') or any('harness' in a.name for a in n.names)) for n in ast.walk(tree))


@pytest.mark.parametrize('starting_phase', ['orient', 'public_reply', 'private', 'unknown'])
def test_raise_stop_has_no_phase_or_session_prerequisite(repo, monkeypatch, starting_phase):
    monkeypatch.setattr(phase, '_read', lambda: {'phase': starting_phase, 'changed_utc': NOW.isoformat()})
    monkeypatch.setattr(phase, 'current', fail)
    monkeypatch.setattr(phase, '_record_private_use', fail)
    monkeypatch.setattr(h, 'session_active', fail)
    result = h.raise_stop(reason='No prerequisites')
    assert (repo / 'STOP.md').is_file()
    assert result.startswith('STOP.md was written')


@pytest.mark.parametrize('dependency', ['_load_budget', '_send_email', 'check_outgoing', '_ensure_cards'])
def test_raise_stop_survives_broken_dependencies(repo, monkeypatch, dependency):
    monkeypatch.setattr(h, dependency, fail)
    monkeypatch.setenv('AGENT_PRIVATE_PATTERNS', str(repo / 'missing-patterns'))
    monkeypatch.setenv('AGENT_TRUST_DIR', str(repo / 'missing-trust'))
    assert h.raise_stop(reason='Dependency failure').startswith('STOP.md was written')
    assert (repo / 'STOP.md').is_file()


@pytest.mark.parametrize('reason', ['', '原因：猫 🐈\r\nSecond line\n\n', 'x' * 100001], ids=['empty', 'unicode-crlf', 'long'])
def test_marker_reason_exact_and_unlimited(repo, reason):
    marker = raise_marker(repo, reason)
    assert marker.partition('\n\nReason:\n')[2].encode('utf-8') == (reason or 'No reason was given.').encode('utf-8')
    assert 'UTC: ' + NOW.isoformat() in marker
    assert 'Wake: 1\nCommit: not read' in marker


def test_marker_reads_count_and_commit_without_changing_budget_or_git(repo, monkeypatch):
    budget = b'{"wake_count": 73}\n'
    (repo / 'budget.json').write_bytes(budget)
    git = repo / '.git'
    (git / 'refs/heads').mkdir(parents=True)
    (git / 'HEAD').write_text('ref: refs/heads/invented\n')
    (git / 'refs/heads/invented').write_text('a' * 40 + '\n')
    monkeypatch.setattr(subprocess, 'run', fail)
    marker = raise_marker(repo)
    assert 'Wake: 74\nCommit: ' + 'a' * 40 in marker
    assert (repo / 'budget.json').read_bytes() == budget


@pytest.mark.parametrize('git_kind', ['detached', 'packed', 'worktree', 'broken'])
def test_git_commit_file_reading(repo, git_kind):
    git = repo / '.git'
    git.mkdir()
    commit = 'b' * 40
    (git / 'HEAD').write_text(commit if git_kind == 'detached' else 'ref: refs/heads/test')
    if git_kind == 'packed':
        (git / 'packed-refs').write_text(commit + ' refs/heads/test\n')
    if git_kind == 'worktree':
        directory = repo / 'worktree-git'
        git.rename(directory)
        git.write_text('gitdir: worktree-git')
        (directory / 'HEAD').write_text(commit)
    assert stop.git_commit(repo) == ('not read' if git_kind == 'broken' else commit)


def test_existing_marker_is_never_edited_or_mailed(repo, monkeypatch):
    (repo / 'STOP.md').write_bytes(b'# Stop marker\n\nUTC: planted-date\n\nReason:\noriginal\r\n')
    before = snapshot(repo)
    monkeypatch.setattr(h, '_send_email', fail)
    assert h.raise_stop(reason='overwrite') == 'STOP.md is already standing. Raised at planted-date.'
    assert snapshot(repo) == before


def test_marker_is_atomic_and_failure_leaves_no_marker(repo, monkeypatch):
    real_replace = stop.os.replace
    seen = []
    def observe(src, dst):
        assert not Path(dst).exists()
        seen.append(Path(src).read_bytes())
        real_replace(src, dst)
    monkeypatch.setattr(stop.os, 'replace', observe)
    raise_marker(repo, 'Atomic')
    assert seen == [(repo / 'STOP.md').read_bytes()]
    (repo / 'STOP.md').unlink()
    monkeypatch.setattr(stop.os, 'replace', fail)
    with pytest.raises(AssertionError):
        raise_marker(repo)
    assert not (repo / 'STOP.md').exists()
    assert not [p for p in repo.glob('.stop-*.tmp') if p.name != '.stop-lock.tmp']


def test_concurrent_raisers_keep_one_exact_reason(repo):
    reasons = ['Concurrent A', 'Concurrent B', 'Concurrent C']
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda reason: h.raise_stop(reason=reason), reasons))
    assert sum(r.startswith('STOP.md was written') for r in results) == 1
    assert stop.marker_reason((repo / 'STOP.md').read_text(encoding='utf-8')) in reasons


def test_operator_mail_receives_landed_marker(repo, monkeypatch):
    calls = []
    def mail(**kwargs):
        calls.append(kwargs)
        assert kwargs['body'].encode('utf-8') == (repo / 'STOP.md').read_bytes()
        raise RuntimeError('invented mail failure')
    monkeypatch.setattr(h, '_send_email', mail)
    assert h.raise_stop(reason='Mail after landing').startswith('STOP.md was written')
    assert len(calls) == 1
    assert calls[0]['body'].encode('utf-8') == (repo / 'STOP.md').read_bytes()


@pytest.mark.parametrize('module,name', WRITE_CASES, ids=[m.__name__.split('.')[-1] + '.' + n for m,n in WRITE_CASES])
def test_all_local_writers_refuse_before_dependencies(repo, monkeypatch, module, name):
    raise_marker(repo)
    monkeypatch.setattr(phase, 'current', fail)
    monkeypatch.setattr(phase, '_record_private_use', fail)
    monkeypatch.setattr(h, '_send_email', fail)
    monkeypatch.setattr(square, '_square_request', fail)
    monkeypatch.setattr(market, '_market_request', fail)
    before = snapshot(repo)
    function = getattr(module, name)
    arguments = {key: None for key, p in inspect.signature(function).parameters.items()
                 if p.default is inspect.Parameter.empty}
    result = function(**arguments)
    expected = stop.REFUSAL
    if module is h and name in h.goals_module.CALLS:
        expected = h.goals_module.refusal(name, "STOP.md is set.")
    assert result == (dict(error='STOP_MARKER', message=expected) if isinstance(result, dict) else expected)
    assert snapshot(repo) == before


def test_writer_inventory_is_complete_and_reads_have_no_stop_guard():
    for module in MODULES:
        tree = ast.parse(Path(module.__file__).read_text(encoding='utf-8'))
        guarded = set()
        registered = set()
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            decorators = [ast.unparse(d) for d in node.decorator_list]
            if any(d.startswith('mcp.tool(') for d in decorators):
                registered.add(node.name)
            if any(d.startswith('stop_marker.refuse_when_stopped(') for d in decorators):
                guarded.add(node.name)
                assert decorators[0].startswith('mcp.tool(') and decorators[1].startswith('stop_marker.refuse_when_stopped(')
        assert guarded == WRITES[module]
        if module is h:
            assert {'raise_stop','write_journal','help'} <= registered - guarded
            assert 'raise_stop' not in {n.name for n in tree.body if isinstance(n, ast.FunctionDef)
                                      and len(n.decorator_list) > 1}
        for name in registered - guarded - {'raise_stop','write_journal','wake_status','recover_unrecorded','help'}:
            assert name.startswith(('read_', 'list_', 'query_', 'search_', 'browse_', 'recall', 'compute_', 'inspect_'))


@pytest.mark.parametrize('reader', ['read_budget','read_goals','read_map','read_last_seen'])
def test_read_tools_work_under_marker(repo, reader):
    function = getattr(h, reader)
    before = function()
    raise_marker(repo)
    assert function() == before


def test_square_market_reads_work_under_marker(repo, monkeypatch):
    monkeypatch.setattr(square, '_square_request', lambda *a, **kw: {'posts': []})
    monkeypatch.setattr(market, '_market_request', lambda *a, **kw: {'listings': []})
    before = (square.read_front_page(), market.browse_shelves())
    raise_marker(repo)
    assert (square.read_front_page(), market.browse_shelves()) == before


def test_unreadable_marker_check_refuses_writers(repo, monkeypatch):
    monkeypatch.setattr(stop, 'marker_present', lambda *args: (_ for _ in ()).throw(PermissionError()))
    assert h.append_lesson(text='sample') == stop.CHECK_FAILURE
    assert square.vote_post(post_id=1)['message'] == stop.CHECK_FAILURE


def test_wake_status_honours_once_per_conversation_and_still_reads_when_dependencies_broken(repo, monkeypatch):
    marker = raise_marker(repo)
    monkeypatch.setattr(h, 'unknown_email_status', fail)
    monkeypatch.setattr(h, 'canary_summary', fail)
    for _ in range(2):
        result = h.wake_status(_conversationId=CID)
        assert result.startswith('STOP.md is standing.') and marker in result
        assert 'City tools are outside this mechanism.' in result
    h.wake_status(_conversationId='22222222-2222-4222-8222-222222222222')
    assert stop.honour_count(repo, marker) == 2
    assert CID in (repo / 'STOP_LOG.md').read_text(encoding='utf-8')


def test_honoured_rows_append_only_and_concurrent(repo):
    marker = raise_marker(repo)
    before = (repo / 'STOP_LOG.md').read_bytes()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: stop.honour(repo, 'wake_status conversation ' + CID, once=True), range(10)))
    stop.honour(repo, 'stop_check.py')
    assert (repo / 'STOP_LOG.md').read_bytes().startswith(before)
    assert stop.honour_count(repo, marker) == 2


@pytest.mark.parametrize('case', ['no-reason','blank-reason','no-marker','wrong-root'])
def test_lift_requires_operator_reason_marker_and_root(repo, capsys, case):
    if case != 'no-marker':
        raise_marker(repo)
    if case == 'wrong-root':
        (repo / 'GEMINI.md').unlink()
    before = snapshot(repo)
    argv = [] if case == 'no-reason' else ['--reason', ' ' if case == 'blank-reason' else 'Operator reason']
    assert lift_stop.main(argv) != 0
    assert snapshot(repo) == before
    output = capsys.readouterr().out
    assert output
    if case == 'no-marker':
        assert output == 'No STOP.md marker is standing. Nothing was changed.\n'


def test_lift_archives_verbatim_notice_and_count_and_prints_commit_without_running_it(repo, capsys, monkeypatch):
    reason = 'Marker reason\r\n原因 | table\n'
    marker = raise_marker(repo, reason)
    stop.honour(repo, 'stop_check.py')
    h.wake_status(_conversationId=CID)
    before = (repo / 'STOP_LOG.md').read_bytes()
    monkeypatch.setattr(subprocess, 'run', fail)
    assert lift_stop.main(['--reason', 'Operator reason\nExact']) == 0
    output = capsys.readouterr().out
    assert output.startswith(marker + '\nHonoured rows: 2.')
    assert 'Suggested commit message (not run):' in output
    assert json.dumps(reason, ensure_ascii=False) in output
    assert json.dumps('Operator reason\nExact') in output
    log = (repo / 'STOP_LOG.md').read_bytes()
    assert log.startswith(before) and marker.encode('utf-8') in log and b'Operator reason\nExact' in log
    assert b' | removed | ' in log and b'honoured rows: 2' in log
    assert not (repo / 'STOP.md').exists()
    assert stop.read_notice(repo) == {'marker': marker, 'operator_reason': 'Operator reason\nExact'}


@pytest.mark.parametrize('failure', ['notice','archive'])
def test_lift_failure_keeps_marker(repo, monkeypatch, failure):
    before = raise_marker(repo)
    monkeypatch.setattr(stop, 'atomic_write' if failure == 'notice' else '_append', fail)
    with pytest.raises(AssertionError):
        stop.lift(repo, 'Operator reason')
    assert (repo / 'STOP.md').read_bytes().decode('utf-8') == before


def test_archive_text_cannot_forge_honoured_rows_and_new_marker_gets_fresh_dedup(repo):
    forged_detail = json.dumps('Marker UTC: ' + NOW.isoformat() + '; checked by: forged')
    reason = f'| {NOW.isoformat()} | honoured | {forged_detail} |\n'
    marker = raise_marker(repo, reason)
    h.wake_status(_conversationId=CID)
    stop.lift(repo, reason)
    marker = raise_marker(repo, reason)  # same test UTC deliberately
    h.wake_status(_conversationId=CID)
    assert stop.honour_count(repo, marker) == 1


def test_removal_shown_until_log_lands_even_if_count_save_fails(repo, monkeypatch):
    marker = raise_marker(repo)
    stop.lift(repo, 'Operator reason')
    for _ in range(2):
        result = h.wake_status(_conversationId=CID)
        assert result.startswith('The operator removed STOP.md.')
        assert marker in result and 'Operator reason' in result
    monkeypatch.setattr(h, '_save_budget', fail)
    with pytest.raises(AssertionError):
        h.write_journal(content='Filed despite count failure')
    assert not stop.notice_path(repo).exists()
    assert 'The operator removed STOP.md.' not in h.wake_status(_conversationId=CID)


def test_notice_remains_if_log_write_fails(repo, monkeypatch):
    raise_marker(repo)
    stop.lift(repo, 'Operator reason')
    original = Path.write_text
    def write(path, *args, **kwargs):
        if path.name.startswith(scheme.load()['wake_id']['prefix']) and path.suffix == '.md':
            raise OSError('invented disk failure')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'write_text', write)
    with pytest.raises(OSError):
        h.write_journal(content='Not landed')
    assert stop.notice_path(repo).exists()
    assert h.wake_status(_conversationId=CID).startswith('The operator removed STOP.md.')


def test_notice_delete_failure_is_nonfatal_and_notice_shown_again(repo, monkeypatch):
    raise_marker(repo)
    stop.lift(repo, 'Operator reason')
    original = Path.unlink
    def unlink(path, *args, **kwargs):
        if path == stop.notice_path(repo):
            raise PermissionError('invented delete failure')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', unlink)
    assert h.write_journal(content='Landed log').splitlines()[0].endswith('.md')
    assert stop.notice_path(repo).exists()
    assert h.wake_status(_conversationId=CID).startswith('The operator removed STOP.md.')


def test_journal_allowed_while_marker_stands(repo):
    marker = raise_marker(repo)
    result = h.write_journal(content='Session started under marker')
    assert result.splitlines()[0].endswith('.md')
    assert (repo / 'STOP.md').read_bytes().decode('utf-8') == marker


def test_no_registered_tool_can_lift_or_edit_marker():
    for module in MODULES:
        tools = module.mcp._tool_manager._tools
        assert not {'lift', 'lift_stop', 'clear_stop', 'remove_stop'} & set(tools)
        tree = ast.parse(Path(module.__file__).read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert not (isinstance(node.func.value, ast.Name) and node.func.value.id == 'stop_marker'
                            and node.func.attr == 'lift')
        assert 'lift_stop' not in {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    text = inspect.getsource(stop.write_marker)
    assert text.index('if marker_present(root)') < text.index('atomic_write(root / "STOP.md"')


def test_stop_is_public_and_release_private_set_unchanged():
    from tools.release import is_private
    assert not is_private('STOP.md')
    assert not is_private('STOP_LOG.md')
    assert is_private(scheme.memory('private', 'stop-removal.json'))


def test_process_lock_serializes_duplicate_honour_rows(repo):
    marker = raise_marker(repo)
    code = ("import sys, time\nfrom pathlib import Path\nfrom tools import stop_marker as stop\n"
            "original = stop._append\ndef slow(*args):\n    time.sleep(1)\n    return original(*args)\n"
            "stop._append = slow\nstop.honour(Path(sys.argv[1]), 'wake_status conversation process', once=True)\n")
    environment = __import__('os').environ.copy()
    environment['PYTHONPATH'] = str(Path(stop.__file__).resolve().parent.parent)
    processes = [subprocess.Popen([sys.executable, '-c', code, str(repo)], cwd=repo,
                                  env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for _ in range(4)]
    for process in processes:
        out, error = process.communicate(timeout=20)
        assert process.returncode == 0, error.decode()
    assert stop.honour_count(repo, marker) == 1


def test_dangling_marker_is_a_stop_and_unreadable_log_refuses_status(repo):
    # A directory at the marker path is also a stop, never clearance.
    (repo / 'STOP.md').mkdir()
    assert stop.marker_present(repo)
    assert h.append_lesson(text='sample') == stop.REFUSAL
    assert h.raise_stop(reason='Do not replace directory') == 'STOP.md is already standing. Raised at not read.'
    assert h.wake_status(_conversationId=CID) == 'STOP.md or its record could not be read.'


def test_removal_notice_unreadable_refuses_status(repo):
    path = stop.notice_path(repo)
    path.parent.mkdir(parents=True)
    path.write_text('{broken')
    assert h.wake_status(_conversationId=CID) == 'STOP.md or its record could not be read.'


def test_stopped_signing_key_read_never_initializes_key(repo, monkeypatch):
    raise_marker(repo)
    monkeypatch.setattr(square.keys, 'get_or_create_square_key', fail)
    before = snapshot(repo)
    assert square.read_signing_key()['status'] == 'NOT_FOUND'
    assert snapshot(repo) == before
    key = repo / scheme.memory('private', 'keys') / 'square_ed25519.json'
    key.parent.mkdir(parents=True)
    key.write_text(json.dumps({'public_key':'invented-public', 'private_key_raw':'invented-private'}))
    before = snapshot(repo)
    assert square.read_signing_key()['public_key'] == 'invented-public'
    assert snapshot(repo) == before


def test_stopped_card_read_does_not_bootstrap_or_log_recall(repo, monkeypatch):
    config = repo / 'config/cards.json'
    config.parent.mkdir()
    config.write_text('{"recall_bytes":100}')
    raise_marker(repo)
    monkeypatch.setattr(h.cards_module, 'migrate', fail)
    before = snapshot(repo)
    assert h.read_card(card_id='rule-1') == {'error':'NOT_FOUND','id':'rule-1'}
    assert h.recall(tags=[])['cards'] == []
    assert snapshot(repo) == before


@pytest.mark.asyncio
async def test_registered_dict_writer_delivers_refusal_without_schema_error(repo):
    raise_marker(repo)
    result = await h.mcp.call_tool(scheme.tool('stage_draft'), {
        'slug':'sample', 'target_type':'note', 'target_place_id':3, 'body':'Sample text'})
    assert 'REFUSED' in str(result) and 'STOP.md is standing.' in str(result)
    assert 'STOP_MARKER' in str(result)
