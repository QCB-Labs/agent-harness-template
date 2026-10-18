"""Interrupted writes, frozen clocks, and crash recovery under tmp_path."""
import datetime as dt
import json
from pathlib import Path

import pytest
from tools import harness_mcp as h, names

NOW=dt.datetime(2080,1,31,10,tzinfo=dt.timezone.utc)

@pytest.fixture
def disk(tmp_path,monkeypatch):
    box={'root':tmp_path,'now':NOW,'mail':[]}
    monkeypatch.setattr(h,'REPO',tmp_path)
    monkeypatch.setattr(h,'_utc_now',lambda:box['now'])
    monkeypatch.setattr(h,'session_active',lambda touch=True:False)
    monkeypatch.setattr(h,'_send_email',lambda subject,body:box['mail'].append((subject,body)) or True)
    monkeypatch.setattr(h,'_recut_after_write',lambda *args:None)
    (tmp_path/'budget.json').write_text(json.dumps({'wake_count':100}))
    return box

@pytest.mark.parametrize('step',['temp','backup','replace'])
def test_replace_failure_preserves_original_and_reports_error(disk,monkeypatch,step):
    path=disk['root']/'paper.md';original=b'# Paper\r\nOriginal invented text.\r\n';path.write_bytes(original)
    if step=='temp':
        old=Path.write_text
        def bad(self,*args,**kw):
            if self.name.endswith('.tmp'):
                self.write_bytes(b'partial invented write');raise OSError('invented temp failure')
            return old(self,*args,**kw)
        monkeypatch.setattr(Path,'write_text',bad)
    elif step=='backup':
        def bad(source,target,*args,**kw):
            Path(target).write_bytes(b'partial backup');raise OSError('invented backup failure')
        monkeypatch.setattr(h.shutil,'copy2',bad)
    else:
        def bad(*args):raise OSError('invented replace failure')
        monkeypatch.setattr(h.os,'replace',bad)
    with pytest.raises(OSError,match='invented'):
        h.safe_replace_file(path,'# Paper\nReplacement invented text.\n',0,1)
    assert path.read_bytes()==original and path.read_text(encoding='utf-8').startswith('# Paper')
    assert not list(path.parent.glob('.*.tmp'))

@pytest.mark.parametrize('size',[79,80,81])
def test_replace_shrink_boundary(disk,size):
    path=disk['root']/'paper.md';path.write_text('a'*100)
    result=h.safe_replace_file(path,'b'*size,min_chars=0,max_shrink=.2)
    assert result[0] is (size>=80)
    if size<80:assert 'shrink' in result[1] and path.read_text()=='a'*100 and result[2] is None
    else:assert path.read_text()=='b'*size and result[2].read_text()=='a'*100

@pytest.mark.parametrize('size',[19,20,21])
def test_replace_minimum_boundary(disk,size):
    path=disk['root']/'paper.md'
    result=h.safe_replace_file(path,'a'*size,min_chars=20,max_shrink=1)
    assert result[0] is (size>=20)
    if size<20:assert 'shorter' in result[1] and not path.exists()
    else:assert path.read_text()=='a'*size

@pytest.mark.parametrize('text,allowed',[('# Pape',False),('# Paper',True),('# Paper extra',True)])
def test_required_heading_is_a_prefix(disk,text,allowed):
    path=disk['root']/'paper.md'
    result=h.safe_replace_file(path,text,0,1,required_first_line='# Paper')
    assert result[0] is allowed
    if allowed:assert path.read_text()==text
    else:assert 'must start' in result[1] and not path.exists()


def test_successful_replaces_keep_distinct_same_second_backups(disk):
    path=disk['root']/'paper.md';path.write_bytes(b'# Paper\r\nFirst content.\r\n')
    first=h.safe_replace_file(path,'# Paper\nSecond content.\n',0,1)
    first_bytes=first[2].read_bytes()
    second_bytes=path.read_bytes()
    second=h.safe_replace_file(path,'# Paper\nThird content.\n',0,1)
    assert first[0] and second[0] and first[2]!=second[2]
    assert first[2].read_bytes()==first_bytes==b'# Paper\r\nFirst content.\r\n'
    assert second[2].read_bytes()==second_bytes
    assert len(list(path.parent.glob('paper.md.*.bak')))==2
    assert path.read_text()=='# Paper\nThird content.\n'

@pytest.mark.parametrize('shape',['gap','malformed','month','same-second'])
def test_wake_numbers_follow_budget_without_reusing_or_skipping(disk,shape):
    root=disk['root'];logs=root/names.rel('journal');logs.mkdir(parents=True)
    if shape=='gap':
        (logs/(names.wake_id(90)+'-2080-01-01.md')).write_text('Older paper entry')
        (logs/(names.wake_id(99)+'-2080-01-02.md')).write_text('Later paper entry')
    elif shape=='malformed':(logs/(names.load()['wake_id']['prefix']+'not-a-number.md')).write_text('Malformed invented name')
    elif shape=='month':disk['now']=dt.datetime(2080,2,1,0,tzinfo=dt.timezone.utc)
    first=h.write_journal('# Invented journal\n\nFirst paper visit.')
    first_bytes=(logs/first).read_bytes()
    if shape=='month':disk['now']=dt.datetime(2080,3,1,0,tzinfo=dt.timezone.utc)
    second=h.write_journal('# Invented journal\n\nSecond paper visit.')
    assert Path(first).name.startswith(names.wake_id(101)+'-') and Path(second).name.startswith(names.wake_id(102)+'-')
    assert first!=second and (logs/first).read_bytes()==first_bytes
    # The template preserves supplied journal text; it does not rewrite its title.
    assert (logs/first).read_text() == '# Invented journal\n\nFirst paper visit.'
    assert (logs/second).read_text() == '# Invented journal\n\nSecond paper visit.'
    assert json.loads((root/'budget.json').read_text())['wake_count']==102
    assert not list(logs.glob('_UNRECORDED-*.md'))
    if shape=='month':assert first.startswith('2080-02/') and second.startswith('2080-03/')


def test_failed_log_write_leaves_dump_and_does_not_burn_number(disk,monkeypatch):
    root=disk['root'];old=Path.write_text
    def bad(self,*args,**kw):
        if self.name.startswith(names.wake_id(101)+'-'):
            self.write_bytes(b'partial invented log');raise OSError('invented interrupted journal write')
        return old(self,*args,**kw)
    monkeypatch.setattr(Path,'write_text',bad)
    with pytest.raises(OSError,match='interrupted'):h.write_journal('Invented lost-session text')
    assert json.loads((root/'budget.json').read_text())['wake_count']==100
    assert 'Invented lost-session text' in h.recover_unrecorded()
    monkeypatch.setattr(Path,'write_text',old)
    filename=next((root/names.rel('journal')).glob('_UNRECORDED-*.md')).name
    filed=h.file_unrecorded(filename)
    assert (root/names.rel('journal')/filed).read_text()=='Invented lost-session text'
    assert 'not found' in h.file_unrecorded(filename)
    assert h.recover_unrecorded()=='(no unrecorded entries)'
    assert len(list((root/names.rel('journal')).rglob('*recovered*.md')))==1
    next_path=h.write_journal('Invented next attempt')
    assert Path(next_path).name.startswith(names.wake_id(101)+'-')
    assert json.loads((root/'budget.json').read_text())['wake_count']==101

@pytest.mark.parametrize('filename',['../_UNRECORDED-paper.md','sub/_UNRECORDED-paper.md','C:/outside/_UNRECORDED-paper.md'])
def test_recovery_refuses_paths_outside_dump_folder(disk,filename):
    outside=disk['root']/'_UNRECORDED-paper.md';outside.write_text('Outside invented content')
    before=outside.read_bytes()
    assert h.file_unrecorded(filename).startswith('REFUSED')
    assert outside.read_bytes()==before
    assert not list((disk['root']/names.rel('journal')).rglob('*recovered*.md'))


def test_mail_false_keeps_log_written_before_send(disk,monkeypatch):
    inspected=[]
    def fail_mail(subject,body):
        files=list((disk['root']/names.rel('journal')).rglob('*.md'))
        assert len(files)==1 and files[0].read_text()==body
        inspected.append(files[0].read_bytes());return False
    monkeypatch.setattr(h,'_send_email',fail_mail)
    result=h.write_journal('Invented after-action report')
    path=disk['root']/names.rel('journal')/result.splitlines()[0]
    assert path.read_bytes()==inspected[0]
    assert json.loads((disk['root']/'budget.json').read_text())['wake_count']==101

def test_mail_false_is_reported_in_log_result(disk,monkeypatch):
    monkeypatch.setattr(h,'_send_email',lambda **kw:False)
    result=h.write_journal('Invented after-action report')
    assert 'mail' in result.lower() and 'not sent' in result.lower()
    assert (disk['root']/names.rel('journal')/result.splitlines()[0]).is_file()

def test_mail_sent_returns_only_the_path(disk):
    result=h.write_journal('Invented after-action report')
    assert '\n' not in result and (disk['root']/names.rel('journal')/result).is_file()


def test_mail_exception_keeps_log_and_persisted_number(disk,monkeypatch):
    def fail_mail(**kw):raise OSError('invented SMTP failure')
    monkeypatch.setattr(h,'_send_email',fail_mail)
    with pytest.raises(OSError,match='SMTP'):h.write_journal('Invented after-action report')
    path=next((disk['root']/names.rel('journal')).rglob('*.md'))
    assert path.read_text()=='Invented after-action report'
    assert json.loads((disk['root']/'budget.json').read_text())['wake_count']==101
    assert h.recover_unrecorded()=='(no unrecorded entries)'

@pytest.mark.xfail(strict=True,reason='Journal is written and dump removed before budget persistence; a failed save can reuse the wake number.')
def test_interrupted_budget_save_cannot_reuse_a_filed_wake_number(disk,monkeypatch):
    save=h._save_budget
    def fail_save(data):raise OSError('invented interrupted budget save')
    monkeypatch.setattr(h,'_save_budget',fail_save)
    with pytest.raises(OSError,match='budget'):h.write_journal('First invented filed log')
    monkeypatch.setattr(h,'_save_budget',save)
    second=h.write_journal('Second invented filed log')
    assert Path(second).name.startswith(names.wake_id(102)+'-')
