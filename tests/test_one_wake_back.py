"""Brief 19: invented newest-log sections and exact Due blocks."""
import hashlib
import json
from pathlib import Path

import pytest
from tools import cards as c

CONFIG = Path(__file__).resolve().parents[1] / 'config/cards.json'
FIRST = '## Waiting, Due, Deferred and Done\r\n'
PLAN = '## What I Plan Next\r\nVisit the invented glass shelf.\r\n'
OBS = '## Observations\r\nAn invented blue kite.\r\n'
TITLE = '# Mission Log — Wake W90000'
FIRST_TEXT = FIRST + '- **Waiting:** A fictional reply.\r\n- **Due:** Return the invented cup.\r\n  Keep its label.\r\n- **Deferred:** Paint the fictional box.\r\n- **Done:** Counted imaginary pebbles.\r\n\r\n'
GOLDEN = {'plan': 'f8bdaa71599cb573fd5a6ca353729f3b0d54d08a2929c5707e92ab1154090d42', 'fallback': '21a5fabd2096b2251a2324327f6ace0a941b38a47c9424659af7b270da500e7d', 'uncut': '945725d35194a564eeac2caa3d40bda58cc0b589f939e279c87b205724e15736'}


def log_text(first=FIRST_TEXT, observations=OBS, plan=PLAN, order=None):
    parts = {'first': first, 'happened': '## What Happened, and Why\r\nCounted imaginary clouds.\r\n',
             'observations': observations, 'plan': plan}
    return TITLE + '\r\n\r\n' + ''.join(parts[name] for name in (order or parts) if parts[name])


def setup(tmp_path, text=None, goals=(), commitments=(), uncut=False, config=None):
    (tmp_path / 'memory/cards').mkdir(parents=True)
    # Written as bytes: write_text would turn \n into \r\n on Windows only, and the golden hashes below would differ by platform.
    (tmp_path / 'identity.md').write_bytes(b'# Invented identity\r\n')
    (tmp_path / 'governance.md').write_bytes(b'# Invented governance\r\n')
    naming = tmp_path / 'naming.json'
    naming.write_text(json.dumps({'dirs': {'memory': 'memory'}, 'paths': {'identity': 'identity.md', 'governance': 'governance.md'}}))
    cfg = tmp_path / 'config.json'
    cfg.write_text(json.dumps(config or json.loads(CONFIG.read_text(encoding='utf-8'))))
    records = []
    text = log_text() if text is None else text
    pieces = [(0, text)] if uncut else c._pieces_for('journal', text, Path('W90000.md'))
    for i, (_, part) in enumerate(pieces):
        section = part.splitlines()[0][3:] if part.startswith('## ') else 'header'
        origin = dict(file='memory/mission_log/W90000-2081-10-07.md', logical='journal', order=i)
        if not uncut:
            origin['key'] = section.lower().replace(' ', '-')
        records.append(dict(id=f'log-{i:04d}', kind='log', role='history', title=f'W90000 · {section}',
                            text=part, origin=origin))
    records += [dict(id=f'goal-{i:04d}', kind='goal', text=text) for i, text in enumerate(goals)]
    records += [dict(id=f'commitment-{i:04d}', kind='commitment', text=text) for i, text in enumerate(commitments)]
    records = [dict(status='live', tags=[], weight=0, created_at='2081-10-07', updated_at='2081-10-07', **record) for record in records]
    (tmp_path / 'memory/cards/cards.jsonl').write_text(''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in records), encoding='utf-8')
    return naming, cfg


def wake(tmp_path, **kwargs):
    naming, cfg = setup(tmp_path, **kwargs)
    return c.read_wake_card(tmp_path, naming, config_path=cfg)


def section(result, name):
    marker = f'## {name}\n\n'
    text = result['text'].split(marker, 1)[1]
    names = [entry['name'] for entry in result['sections']]
    index = names.index(name)
    return text.split(f"\n\n## {names[index+1]}\n\n", 1)[0] if index+1 < len(names) else text


def padded(text, size):
    remaining = size - len(text.encode('utf-8'))
    assert remaining >= 2
    return text + 'x' * (remaining - 2) + '\r\n'


def test_four_sections_whole_log_order_title_once(tmp_path):
    result = wake(tmp_path)
    body = section(result, 'What I was doing')
    assert body == TITLE + '\n\n' + FIRST_TEXT + OBS + PLAN
    assert result['text'].count(TITLE) == 1
    assert 'Counted imaginary clouds.' not in body
    assert result['total_bytes'] == len(result['text'].encode('utf-8')) <= 44000


@pytest.mark.parametrize('order', [('plan', 'happened', 'first', 'observations'), ('observations', 'first', 'happened', 'plan')])
def test_sections_follow_source_order(tmp_path, order):
    text = log_text(order=order)
    result = wake(tmp_path, text=text)
    expected = {'first': FIRST_TEXT, 'observations': OBS, 'plan': PLAN}
    assert section(result, 'What I was doing') == TITLE + '\n\n' + ''.join(expected[name] for name in order if name in expected)


@pytest.mark.parametrize('extra,observed', [(0, True), (1, False)])
def test_observations_budget_exact_utf8_boundary(tmp_path, extra, observed):
    first, obs, plan = padded(FIRST_TEXT, 1500), padded(OBS, 2000 + extra), padded(PLAN, 1500)
    result = wake(tmp_path, text=log_text(first=first, observations=obs, plan=plan))
    body = section(result, 'What I was doing')
    assert body == TITLE + '\n\n' + first + (obs if observed else '') + plan
    assert first in body and plan in body


@pytest.mark.parametrize('mode', ['plan', 'fallback', 'uncut'])
def test_without_first_is_byte_exact_main_answer(tmp_path, mode):
    text = log_text(first='', plan='' if mode == 'fallback' else PLAN)
    result = wake(tmp_path, text=text, goals=['# Invented goal\nKeep the fictional shelf.\n'], uncut=mode == 'uncut')
    raw = json.dumps(result, ensure_ascii=False, sort_keys=True).encode('utf-8')
    assert hashlib.sha256(raw).hexdigest() == GOLDEN[mode]


@pytest.mark.parametrize('opening', ['- **Due:**', '### Due', 'Due:', '* _Due:_', '1. **Due:**'])
@pytest.mark.parametrize('stop', ['Waiting', 'Deferred', 'Done'])
def test_due_formats_stop_at_next_named_list(tmp_path, opening, stop):
    due = opening + ' Return the fictional cup.\r\n  More fictional detail.\r\n\r\n'
    first = FIRST + '- **Waiting:** Before.\r\n' + due + f'- **{stop}:** After.\r\n'
    result = wake(tmp_path, text=log_text(first=first))
    assert section(result, 'What I owe') == due
    assert first in section(result, 'What I was doing')


def test_due_last_list_keeps_exact_end_and_missing_newline(tmp_path):
    due = 'Due: Return the fictional cup.\r\n\r\nLast invented line. é'
    first = FIRST + '- Waiting: Before.\r\n' + due
    result = wake(tmp_path, text=TITLE + '\r\n\r\n' + first)
    assert section(result, 'What I owe') == due
    assert section(result, 'What I was doing') == TITLE + '\n\n' + first


def test_due_before_live_commitments(tmp_path):
    result = wake(tmp_path, commitments=['# Invented commitment\nReturn the fictional pencil.\n'])
    due = '- **Due:** Return the invented cup.\r\n  Keep its label.\r\n'
    assert section(result, 'What I owe') == due + '\n\n# Invented commitment\nReturn the fictional pencil.\n'


@pytest.mark.parametrize('commitments,expected', [((), 'none'), (('Invented commitment.',), 'Invented commitment.')])
def test_no_due_keeps_previous_owing_behavior(tmp_path, commitments, expected):
    first = FIRST + '- Waiting: A fictional reply.\r\n- Done: Fictional task.\r\n'
    result = wake(tmp_path, text=log_text(first=first), commitments=commitments)
    assert section(result, 'What I owe') == expected


def test_full_log_allowance_preserves_previously_shown_goals(tmp_path):
    first, obs, plan = padded(FIRST_TEXT, 1500), padded(OBS, 2000), padded(PLAN, 1500)
    goals = [padded(f'# Invented goal {i}\r\n', 1200) for i in range(6)]
    result = wake(tmp_path, text=log_text(first=first, observations=obs, plan=plan), goals=goals)
    body = section(result, 'What I was doing')
    assert sum(len(x.encode('utf-8')) for x in (first, obs, plan)) == 5000
    # All six are present in the captured main answer (before the change).
    assert sum(goal in body for goal in goals) == 6 == len(goals)
    assert len(body.encode('utf-8')) <= 13000
    assert result['total_bytes'] <= 44000


def test_mandatory_log_and_due_survive_section_limits(tmp_path):
    cfg = json.loads(CONFIG.read_text(encoding='utf-8'))
    cfg['wake_sections']['what_i_was_doing'] = cfg['wake_sections']['what_i_owe'] = 1
    result = wake(tmp_path, config=cfg)
    assert FIRST_TEXT in section(result, 'What I was doing') and PLAN in section(result, 'What I was doing')
    assert '- **Due:** Return the invented cup.' in section(result, 'What I owe')


def test_no_false_due_inside_prose(tmp_path):
    first = FIRST + 'The word Due is not first.\r\n- **Waiting:** A due date.\r\n'
    result = wake(tmp_path, text=log_text(first=first))
    assert section(result, 'What I owe') == 'none'


def test_newest_log_rule_is_unchanged(tmp_path):
    naming, cfg = setup(tmp_path)
    path = tmp_path / 'memory/cards/cards.jsonl'
    records = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    older = dict(records[-1], id='older', text='## What I Plan Next\nAn older fictional plan.', updated_at='2099-01-01',
                 origin=dict(file='memory/mission_log/W80000.md', logical='journal', key='plan', order=2))
    path.write_text(''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in records + [older]), encoding='utf-8')
    result = c.read_wake_card(tmp_path, naming, config_path=cfg)
    assert 'older fictional plan' not in result['text'] and PLAN in result['text']


def test_custom_heading_patterns_select_source_text(tmp_path):
    cfg = json.loads(CONFIG.read_text(encoding='utf-8'))
    cfg['log_first_heading'] = r'(?im)^(#{1,6}) Tasks\s*$'
    cfg['log_observations_heading'] = r'(?im)^(#{1,6}) Notes\s*$'
    first = FIRST_TEXT.replace(FIRST, '## Tasks\r\n')
    obs = OBS.replace('## Observations', '## Notes')
    result = wake(tmp_path, text=log_text(first=first, observations=obs), config=cfg)
    assert first in result['text'] and obs in result['text']


def test_first_section_found_under_resolved_heading(tmp_path):
    first = FIRST_TEXT.replace('Deferred and Done', 'Deferred and Resolved').replace('- **Done:**', '- **Resolved:**')
    result = wake(tmp_path, text=log_text(first=first))
    assert section(result, 'What I was doing') == TITLE + '\n\n' + first + OBS + PLAN
    assert section(result, 'What I owe') == '- **Due:** Return the invented cup.\r\n  Keep its label.\r\n'
