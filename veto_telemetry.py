"""Read-only H1 veto paper observations. No delivery or live state writes."""
import copy
import csv
import io
import json
import math
import os

import pandas as pd
import xau_scalp as sc

DECISION_COLS = ['time', 'source_sha', 'source', 'final_reason', 'live_reason',
                 'live_ok', 'h1_veto', 'other_blockers', 'paper_blocker', 'setup_id',
                 'news_mode', 'news_event', 'gates', 'coverage']
CANDIDATE_COLS = ['id', 'time', 'source_sha', 'source', 'direction', 'grade',
                  'entry', 'sl', 'tp1', 'tp2', 'rr1', 'blocker', 'other_gates',
                  'horizon_hours', 'fees', 'outcome', 'gross_r', 'closed_at', 'coverage']


def atomic(path, text):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(text)
    tmp.replace(path)


def save(home, data):
    # JSON is authoritative; CSVs are rebuildable views of same transaction.
    home.mkdir(parents=True, exist_ok=True)
    atomic(home / 'veto_state.json', json.dumps(data, default=str))
    for name, cols, rows in [('scalp_decisions.csv', DECISION_COLS, data['decisions']),
                              ('veto_candidates.csv', CANDIDATE_COLS, data['candidates'].values())]:
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=cols)
        writer.writeheader()
        writer.writerows(rows)
        atomic(home / name, out.getvalue())


def unavailable(home, now, reason, detail):
    """Record pre-signal coverage failures without creating paper entries."""
    path = home / 'veto_state.json'
    data = json.loads(path.read_text()) if path.exists() else {
        'schema': 1, 'paper_state': {}, 'candidates': {}, 'decisions': []}
    row = dict.fromkeys(DECISION_COLS, '')
    row.update(time=now.isoformat(), source_sha=os.getenv('GITHUB_SHA', 'local-unversioned'),
               final_reason=reason, live_reason=detail, live_ok=False, coverage='not evaluated')
    data['decisions'].append(row)
    save(home, data)


def observe(home, h1, m15, m5, now, source, news_mode, news_event,
            live, live_state, live_ok, live_reason):
    path = home / 'veto_state.json'
    data = json.loads(path.read_text()) if path.exists() else {
        'schema': 1, 'paper_state': {}, 'candidates': {}, 'decisions': []}
    paper_state = data['paper_state']
    sc.reset_setup_if_inside_range(paper_state, m15)
    _, structure = sc.structure_direction(h1, m15, m5 if news_mode == 'aggressive' else None)
    veto = 'diveto tren H1 ekstrem' in structure.note
    candidate = sc.build_scalp_signal(h1, m15, m5, now, source, news_mode,
                                      news_event, audit_bypass_h1=True) if veto else live
    others = []
    if now.hour < sc.ASIA_BLOCK_UTC_UNTIL:
        others.append('ASIA')
    elif candidate.direction == 'NO-TRADE':
        others.append('STRUCTURE' if not structure.passed and not veto else 'SCORE')
    if news_mode in ('blackout', 'quiet'):
        others.append('NEWS_' + news_mode.upper())
    if candidate.direction != 'NO-TRADE' and not sc.should_send(candidate, copy.deepcopy(live_state), now)[0]:
        others.append('DEDUP')
    paper_blocker = ''
    if candidate.direction != 'NO-TRADE' and (
        candidate.setup_id in data['candidates'] or not sc.should_send(candidate, paper_state, now)[0]):
        paper_blocker = 'DEDUP'
    if candidate.direction != 'NO-TRADE' and (
        not all(math.isfinite(v) for v in [candidate.price, candidate.stop_loss, *candidate.targets])
        or abs(candidate.price - candidate.stop_loss) <= 0):
        paper_blocker = 'INVALID_PAPER_LEVELS'
    source = source.split(' (')[0]
    sha = os.getenv('GITHUB_SHA', 'local-unversioned')
    gates = json.dumps([vars(t) for t in candidate.triggers])
    if veto and not others and not paper_blocker:
        data['candidates'][candidate.setup_id] = dict(zip(CANDIDATE_COLS, [
            candidate.setup_id, now.isoformat(), sha, source, candidate.direction,
            candidate.grade, candidate.price, candidate.stop_loss, *candidate.targets[:2],
            candidate.rr[0], structure.note, gates, 2, 'gross; fees/slippage not modeled',
            'PENDING', '', '', 'awaiting closed M5 bars']))
        paper_state['last'] = {'direction': candidate.direction, 'time': now.isoformat()}
        paper_state['active_setup'] = {'id': candidate.setup_id, 'direction': candidate.direction,
                                       'level': candidate.breakout_level}
    final = ('ASIA' if 'ASIA' in others else 'H1_VETO' if veto else
             next((b for b in others if b.startswith('NEWS_')), None) or
             (others[0] if others else 'ELIGIBLE' if live_ok else 'STRUCTURE'))
    data['decisions'].append(dict(zip(DECISION_COLS, [now.isoformat(), sha, source,
        final, live_reason, live_ok, veto, '|'.join(others), paper_blocker,
        candidate.setup_id, news_mode, json.dumps(news_event, default=str), gates,
        json.dumps({k: {'bars': len(f), 'last_open': f.index[-1].isoformat()}
                    for k, f in [('h1', h1), ('m15', m15), ('m5', m5)]})])))
    for row in data['candidates'].values():
        if row['outcome'] == 'PENDING':
            resolve(row, m5, source)
    save(home, data)


def resolve(row, m5, source):
    """2h close-labelled M5 observation, complete contiguous coverage required."""
    import journal
    start = pd.Timestamp(row['time'])
    # Entry proxy is latest observed close at evaluation time; use only full
    # candles opening at/after evaluation, never pre-entry high/low.
    first = start.ceil('5min')
    end = first + pd.Timedelta(hours=2)
    bars = m5.loc[(m5.index >= first) & (m5.index < end)].copy()
    if source != row['source']:
        row['coverage'] = 'source mismatch; pending'
        return
    expected = pd.date_range(first, periods=len(bars), freq='5min')
    if bars.empty or not bars.index.equals(expected) or bars.isna().any().any():
        row['coverage'] = 'missing/noncontiguous bars; pending'
        return
    bars.index = bars.index + pd.Timedelta(minutes=5)
    sig = pd.Series(dict(time=first, entry=row['entry'], sl=row['sl'], tp1=row['tp1'],
                         rr1=row['rr1'], direction=row['direction']))
    result, r, closed, note = journal.resolve(sig, bars, horizon_h=2)
    row.update(outcome=result, gross_r=r if result != 'PENDING' else '', closed_at=closed,
               coverage=f'{len(bars)}/24 closed M5 bars; entry lag {(first-start).total_seconds():g}s; {note}')
