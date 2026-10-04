"""Spending integration edges, with invented keys and mocked service boundaries."""
import base64
import datetime as dt
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tools import authority as a, harness_mcp as h, market_mcp as m, wallet as w, names, profile

NOW = dt.datetime(2080, 1, 1, 10, tzinfo=dt.timezone.utc)
TREASURY = '0x' + '1'*40
ASSET = '0x' + '2'*40
PAYER = '0x' + '3'*40
TX = '0x' + '4'*64

@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = dict(enabled=True, mode='autonomous-approved', caps_verified=True,
               public_address=PAYER, site_scope=['1F3EA'], pay_armed=True, caps_usdc={'per_tx':2})
    box = SimpleNamespace(root=tmp_path, cfg=cfg, session=True, mail=[], calls=[], paid=set(),
          login='VALID', status_rc=0, status_raw=None, transfer_error=None, transfer_raw=None,
          transfer_rc=0, official=dict(treasury=TREASURY, usdc_contract=ASSET, network='base', listing_fee_usdc=1),
          official_error=None, http=[])
    monkeypatch.setattr(h, 'REPO', tmp_path)
    monkeypatch.setattr(m, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(a, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(w, 'WALLET_CONFIG', tmp_path/'wallet-config.json')
    for module, clock in ((h,'_utc_now'),(m,'_utc_now'),(a,'_now')):
        monkeypatch.setattr(module, clock, lambda: NOW)
    monkeypatch.setattr(h, 'session_active', lambda touch=True: box.session)
    monkeypatch.setattr(a, 'session_active', lambda touch=True: box.session)
    monkeypatch.setattr(h, '_send_email', lambda subject,body: box.mail.append((subject,body)) or True)
    monkeypatch.setattr(w, 'circle_command', lambda:'invented-circle')
    def circle(cmd, **kw):
        box.calls.append((list(cmd),kw))
        if cmd[2]=='status':
            raw=box.status_raw if box.status_raw is not None else json.dumps({'data':{'mainnet':{'tokenStatus':box.login}}})
            return subprocess.CompletedProcess(cmd,box.status_rc,raw,'')
        assert cmd[2]=='transfer'
        key=cmd[cmd.index('--idempotency-key')+1]
        box.paid.add(key)  # The fake remote wallet charges once per idempotency key.
        if box.transfer_error:raise box.transfer_error
        raw=box.transfer_raw if box.transfer_raw is not None else json.dumps({'data':{'state':'COMPLETE','txHash':TX,'id':'invented-transfer'}})
        return subprocess.CompletedProcess(cmd,box.transfer_rc,raw,'invented wallet error')
    monkeypatch.setattr(w.subprocess, 'run', circle)
    def official(req, **kw):
        box.http.append(req)
        assert req.full_url.endswith('/api/official') and req.get_method()=='GET'
        if box.official_error:raise box.official_error
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self):return json.dumps(box.official).encode()
        return Response()
    monkeypatch.setattr(m.urllib.request,'urlopen',official)
    (tmp_path/'wallet-config.json').write_text(json.dumps(cfg),encoding='utf-8')
    box.spend=tmp_path / names.memory(*h.SPEND_LEDGER)
    box.fee=tmp_path / names.memory(*m.FEE_LEDGER)
    return box

def config(env):
    (env.root/'wallet-config.json').write_text(json.dumps(env.cfg),encoding='utf-8')
def transfers(env):return [cmd for cmd,kw in env.calls if cmd[2]=='transfer']
def ledger(path,text):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf-8')

@pytest.mark.parametrize('session,armed',[(False,False),(False,True),(True,False),(True,True)])
@pytest.mark.parametrize('tool',['city','fee'])
def test_both_gates_are_required(env,session,armed,tool):
    env.session=session;env.cfg['pay_armed']=armed;config(env)
    result=h.authorise_spend('invent_kind','Paper kettle') if tool=='city' else m.pay_listing_fee('Paper kettle')
    approved=session and armed
    assert result['status']==(('GATES_PASSED' if tool=='city' else 'PAID') if approved else 'REFUSED')
    assert len(transfers(env))==(1 if approved and tool=='fee' else 0)
    if tool=='city':
        assert env.spend.exists()==approved
        if approved:assert json.loads(env.spend.read_text())['issued'][0]['city_credit_request_id']==result['city_credit_request_id']

@pytest.mark.parametrize('value',['true',1,None,'missing',False])
@pytest.mark.parametrize('tool',['city','fee'])
def test_arming_requires_boolean_true(env,value,tool):
    if value=='missing':env.cfg.pop('pay_armed')
    else:env.cfg['pay_armed']=value
    config(env)
    assert w.pay_armed() is False
    result=h.authorise_spend('invent_kind') if tool=='city' else m.pay_listing_fee('Paper kettle')
    assert result['status']=='REFUSED' and not transfers(env)

@pytest.mark.parametrize('shape',['missing','malformed','unreadable'])
@pytest.mark.parametrize('tool',['city','fee'])
def test_bad_wallet_config_refuses(env,monkeypatch,shape,tool):
    path=env.root/'wallet-config.json'
    if shape=='missing':path.unlink()
    elif shape=='malformed':path.write_text('{broken')
    else:
        original=Path.open
        def bad(self,*args,**kw):
            if self==path:raise OSError('invented unreadable configuration')
            return original(self,*args,**kw)
        monkeypatch.setattr(Path,'open',bad)
    result=h.authorise_spend('invent_kind') if tool=='city' else m.pay_listing_fee('Paper kettle')
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
    assert m.pay_listing_fee('Paper kettle')['error']=='NO_SESSION'
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


@pytest.mark.parametrize('field,value',[('treasury','not-an-address'),('usdc_contract','not-an-asset'),('network','other'),('network','aaa'),('listing_fee_usdc',0),('listing_fee_usdc','bad')])
def test_official_invalid_facts_refuse_before_transfer(env,field,value):
    env.official[field]=value
    assert m.pay_listing_fee('Paper kettle')['error']=='OFFICIAL_UNEXPECTED'
    assert len(env.http)==1 and not transfers(env) and not env.fee.exists()

@pytest.mark.parametrize('amount',[1.999999,2,2.000001])
def test_official_fee_cap_boundary(env,amount):
    env.official['listing_fee_usdc']=amount
    result=m.pay_listing_fee('Paper kettle')
    assert result['status']==('PAID' if amount<=2 else 'REFUSED')
    if amount<=2:
        cmd,kw=next((cmd,kw) for cmd,kw in env.calls if cmd[2]=='transfer')
        assert cmd[3]==TREASURY and cmd[cmd.index('--token')+1]==ASSET
        assert cmd[cmd.index('--amount')+1]==str(float(amount))
        assert kw['capture_output'] is True and kw['text'] is True
    else:assert result['error']=='OVER_CAP' and not transfers(env)


def test_failed_official_read_never_pays(env):
    env.official_error=OSError('invented failed official read')
    assert m.pay_listing_fee('Paper kettle')['error']=='OFFICIAL_UNREADABLE'
    assert not transfers(env) and not env.fee.exists()

@pytest.mark.parametrize('failure',['exception','timeout','nonzero','nonjson'])
def test_uncertain_transfer_retries_same_payment_key(env,failure):
    if failure=='exception':env.transfer_error=OSError('invented lost response')
    if failure=='timeout':env.transfer_error=subprocess.TimeoutExpired('invented-circle',180)
    if failure=='nonzero':env.transfer_rc=1;env.transfer_raw='unreadable'
    if failure=='nonjson':env.transfer_raw='unreadable'
    first=m.pay_listing_fee('Paper kettle')
    assert first['status']==('PENDING' if failure=='nonjson' else 'UNCERTAIN')
    assert 'Do NOT pay' in first['message']
    entry=json.loads(env.fee.read_text())[0];key=entry['idempotency_key']
    env.transfer_error=None;env.transfer_rc=0;env.transfer_raw=None
    second=m.pay_listing_fee('Paper kettle')
    assert second['status']=='PAID'
    assert all(cmd[cmd.index('--idempotency-key')+1]==key for cmd in transfers(env))
    assert len(env.paid)==1 and len(json.loads(env.fee.read_text()))==1
    assert m.pay_listing_fee('Paper kettle')['status']=='ALREADY_PAID'
    assert len(transfers(env))==2

@pytest.mark.parametrize('status,rc,raw',[('EXPIRED',0,None),('ZVALID',0,None),('VALID',1,None),('VALID',-1,None),('VALID',0,'not-json'),('VALID',0,'[]')])
def test_wallet_status_failures_never_transfer(env,status,rc,raw):
    env.login=status;env.status_rc=rc;env.status_raw=raw
    assert m.pay_listing_fee('Paper kettle')['error']=='CIRCLE_LOGGED_OUT'
    assert not transfers(env) and not env.fee.exists()
    assert all(kw==dict(capture_output=True,text=True,timeout=30) for cmd,kw in env.calls)

@pytest.mark.parametrize('field,value',[('enabled',1),('caps_verified',1),('mode','z-autonomous-approved'),('mode','aaa')])
def test_wallet_approval_truth_is_strict(env,field,value):
    env.cfg[field]=value;config(env)
    assert m.pay_listing_fee('Paper kettle')['error']=='WALLET_UNAPPROVED' and not transfers(env)

@pytest.mark.parametrize('raw',[None,'[]','{broken'])
def test_fee_ledger_missing_empty_or_corrupt(env,raw):
    if raw is not None:ledger(env.fee,raw)
    result=m.pay_listing_fee('Paper kettle')
    assert result['status']==('REFUSED' if raw=='{broken' else 'PAID')
    if raw=='{broken':assert result['error']=='LEDGER_UNREADABLE' and env.fee.read_text()==raw and not transfers(env)
    else:assert len(json.loads(env.fee.read_text()))==1 and len(transfers(env))==1

@pytest.mark.parametrize('raw',['{}','[{"tx_hash":"'+TX+'","used":false}]','["not an entry"]'])
def test_damaged_fee_ledger_cannot_forget_paid_transfer(env,raw):
    ledger(env.fee,raw)
    result=m.pay_listing_fee('Paper kettle')
    assert result['status']=='REFUSED' and result['error']=='LEDGER_UNREADABLE' and not transfers(env)
    assert env.fee.read_text()==raw

@pytest.mark.parametrize('success',[False,True])
def test_listing_consumes_fee_only_after_success(env,monkeypatch,success):
    m.pay_listing_fee('Paper kettle')
    before=env.fee.read_bytes();calls=[]
    def request(*args,**kw):
        calls.append((args,kw));return {'id':9000} if success else {'error':'invented listing failure'}
    monkeypatch.setattr(m,'_market_request',request)
    result=m.create_listing(title='Paper kettle',description='Imaginary cup guide',preview='Paper preview',artifact='Invented folds',price_usdc=0,seller_wallet=PAYER,fee_tx_hash=TX)
    assert bool('error' not in result)==success
    assert len(calls)==1 and calls[0][1]['data']['fee_tx_hash']==TX
    entry=json.loads(env.fee.read_text())[0]
    assert entry['used'] is success
    if not success:assert env.fee.read_bytes()==before
    result=m.pay_listing_fee('Paper kettle')
    assert result['status']==('PAID' if success else 'ALREADY_PAID')
    assert len(transfers(env))==(2 if success else 1)

def test_used_fee_is_refused_before_second_listing(env,monkeypatch):
    m.pay_listing_fee('Paper kettle');calls=[]
    def request(*args,**kw):calls.append(kw);return {'id':9000}
    monkeypatch.setattr(m,'_market_request',request)
    kwargs=dict(title='Paper kettle',description='Imaginary guide',preview='Paper preview',artifact='Invented folds',price_usdc=0,seller_wallet=PAYER,fee_tx_hash=TX)
    m.create_listing(**kwargs);second=m.create_listing(**kwargs)
    assert len(calls)==1 and second['error']=='FEE_ALREADY_USED'
    world=m.activate_world_listing(draft_id=1,city_offer_id=2,fee_tx_hash=TX)
    assert len(calls)==1 and world['error']=='FEE_ALREADY_USED' and len(transfers(env))==1

@pytest.mark.parametrize('fee',[-.000001,None])
def test_negative_or_unparseable_official_fee_refuses(env,fee):
    env.official['listing_fee_usdc']=fee
    assert m.pay_listing_fee('Paper kettle')['error']=='OFFICIAL_UNEXPECTED' and not transfers(env)


def test_market_session_check_does_not_refresh_lifetime(env,monkeypatch):
    touches=[]
    monkeypatch.setattr(a,'session_active',lambda touch=True:touches.append(touch) or True)
    assert m.pay_listing_fee('Paper kettle')['status']=='PAID'
    assert touches==[False]

@pytest.mark.parametrize('state,expected',[('COMPLETE','paid'),('INITIATED','submitted:INITIATED'),('','submitted:unknown')])
def test_transfer_hash_state_and_id_are_recorded(env,state,expected):
    env.transfer_raw=json.dumps({'data':{'transaction':{'transactionId':'paper-transfer','transactionHash':TX,'state':state}}})
    result=m.pay_listing_fee('Paper kettle');entry=json.loads(env.fee.read_text())[0]
    assert result['status']=='PAID' and result['state']==(state or 'unknown')
    assert entry['status']==expected and entry['transaction_id']=='paper-transfer'
    assert entry['tx_hash']==TX

@pytest.mark.parametrize('state',['INITIATED',''])
def test_pending_transfer_state_and_id_are_recorded(env,state):
    env.transfer_raw=json.dumps({'data':{'id':'paper-transfer','state':state}})
    result=m.pay_listing_fee('Paper kettle');entry=json.loads(env.fee.read_text())[0]
    assert result['status']=='PENDING' and result['transaction_id']=='paper-transfer'
    assert result['state']==(state or 'unknown') and entry['status']=='pending:'+(state or 'unknown')
    assert entry['transaction_id']=='paper-transfer' and 'Do NOT pay again' in result['message']

@pytest.mark.parametrize('rc',[-1,1])
def test_nonzero_transfer_without_hash_is_uncertain(env,rc):
    env.transfer_rc=rc;env.transfer_raw='{}'
    result=m.pay_listing_fee('Paper kettle')
    assert result['status']=='UNCERTAIN' and result['error']=='TRANSFER_FAILED'
    assert json.loads(env.fee.read_text())[0]['status']=='uncertain'
    assert 'invented wallet error' in result['message']

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
