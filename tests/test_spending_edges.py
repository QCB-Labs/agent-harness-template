"""Spending integration edges, with invented keys and mocked service boundaries."""
import base64
import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tools import authority as a, harness_mcp as h, wallet as w, names, profile

NOW = dt.datetime(2080, 1, 1, 10, tzinfo=dt.timezone.utc)
PAYER = '0x' + '3'*40

@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = dict(enabled=True, mode='autonomous-approved', caps_verified=True,
               public_address=PAYER, site_scope=['1F3EA'], pay_armed=True, caps_usdc={'per_tx':2})
    box = SimpleNamespace(root=tmp_path, cfg=cfg, session=True, mail=[], calls=[])
    monkeypatch.setattr(h, 'REPO', tmp_path)
    monkeypatch.setattr(a, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(w, 'WALLET_CONFIG', tmp_path/'wallet-config.json')
    for module, clock in ((h,'_utc_now'),(a,'_now')):
        monkeypatch.setattr(module, clock, lambda: NOW)
    monkeypatch.setattr(h, 'session_active', lambda touch=True: box.session)
    monkeypatch.setattr(a, 'session_active', lambda touch=True: box.session)
    monkeypatch.setattr(h, '_send_email', lambda subject,body: box.mail.append((subject,body)) or True)
    monkeypatch.setattr(w, 'circle_command', lambda:'invented-circle')
    def circle(cmd, **kw):
        box.calls.append((list(cmd),kw))
        raise AssertionError('City spending gate must not call the wallet CLI')
    monkeypatch.setattr(w.subprocess, 'run', circle)
    (tmp_path/'wallet-config.json').write_text(json.dumps(cfg),encoding='utf-8')
    box.spend=tmp_path / names.memory(*h.SPEND_LEDGER)
    return box

def config(env):
    (env.root/'wallet-config.json').write_text(json.dumps(env.cfg),encoding='utf-8')
def transfers(env):return [cmd for cmd,kw in env.calls if cmd[2]=='transfer']
def ledger(path,text):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf-8')

@pytest.mark.parametrize('session,armed',[(False,False),(False,True),(True,False),(True,True)])
def test_both_gates_are_required(env,session,armed):
    env.session=session;env.cfg['pay_armed']=armed;config(env)
    result=h.authorise_spend('invent_kind','Paper kettle')
    approved=session and armed
    assert result['status']==('GATES_PASSED' if approved else 'REFUSED')
    assert not transfers(env)
    assert env.spend.exists()==approved
    if approved:assert json.loads(env.spend.read_text())['issued'][0]['city_credit_request_id']==result['city_credit_request_id']

@pytest.mark.parametrize('value',['true',1,None,'missing',False])
def test_arming_requires_boolean_true(env,value):
    if value=='missing':env.cfg.pop('pay_armed')
    else:env.cfg['pay_armed']=value
    config(env)
    assert w.pay_armed() is False
    result=h.authorise_spend('invent_kind')
    assert result['status']=='REFUSED' and not transfers(env)

@pytest.mark.parametrize('shape',['missing','malformed','unreadable'])
def test_bad_wallet_config_refuses(env,monkeypatch,shape):
    path=env.root/'wallet-config.json'
    if shape=='missing':path.unlink()
    elif shape=='malformed':path.write_text('{broken')
    else:
        original=Path.open
        def bad(self,*args,**kw):
            if self==path:raise OSError('invented unreadable configuration')
            return original(self,*args,**kw)
        monkeypatch.setattr(Path,'open',bad)
    result=h.authorise_spend('invent_kind')
    assert result['status']=='REFUSED' and not transfers(env)


def test_valid_signed_command_cannot_replace_session(env,monkeypatch):
    env.session=False
    key=Ed25519PrivateKey.generate()
    monkeypatch.setattr(a,'load_operator_public_key',lambda:key.public_key())
    canonical='[NONCE: abcdef123456]\n[TIMESTAMP: 2080-01-01T10:00:00Z]\n[EXPIRES: 2080-01-01T11:00:00Z]\ninvent_kind'
    signed=canonical+'\n[SIGNATURE: '+base64.b64encode(key.sign(canonical.encode())).decode()+']'
    assert a.verify_command(signed,consume=False)[0] is True
    commands=a.commands_dir();commands.mkdir(parents=True);(commands/'invented.md').write_text(signed)
    assert a.find_signed_command('invent_kind',consume=False)[0] is True
    assert h.authorise_spend('invent_kind')['status']=='REFUSED'
    assert not transfers(env) and not env.spend.exists()

@pytest.mark.parametrize('raw,approved',[(None,True),('{"issued":[]}',True),('',False),('{broken',False),('[]',False),('{"issued":{}}',False),('{"issued":[{}]}',True)])
def test_spend_ledger_shape_and_preservation(env,raw,approved):
    if raw is not None:ledger(env.spend,raw)
    result=h.authorise_spend('invent_kind','Paper kettle')
    assert result['status']==('GATES_PASSED' if approved else 'REFUSED')
    assert not transfers(env)
    if approved:
        entries=json.loads(env.spend.read_text())['issued']
        assert entries[-1]['city_credit_request_id']==result['city_credit_request_id']
        assert set(entries[-1])=={'city_credit_request_id','action','detail','issued_utc'}
        if raw=='{"issued":[{}]}':assert entries[0]=={}
    else:assert env.spend.read_text()==raw


@pytest.mark.parametrize('length',[499,500,501])
def test_spend_detail_boundary_and_request_shape(env,length):
    result=h.authorise_spend('invent_kind','p'*length)
    assert result['status']=='GATES_PASSED' and result['detail']=='p'*min(length,500)
    request=result['city_credit_request_id']
    assert request.startswith(profile.handle() + '-invent-kind-20800101-100000-') and len(request.rsplit('-',1)[1])==6
    entry=json.loads(env.spend.read_text())['issued'][0]
    assert entry['detail']==result['detail'] and entry['issued_utc']=='2080-01-01T10:00:00Z'

@pytest.mark.parametrize('label',['','x','invent kind','../paper'])
def test_invalid_action_never_creates_spend_or_payment(env,label):
    result=h.authorise_spend(label)
    assert result['status']=='REFUSED' and 'Invalid action label' in result['reason']
    assert not env.spend.exists() and not transfers(env)
