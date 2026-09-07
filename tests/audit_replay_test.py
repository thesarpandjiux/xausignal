#!/usr/bin/env python3
"""Offline replay regressions: python3 tests/audit_replay_test.py."""
import sys
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import scalp_direction_audit as audit
import xau_scalp as sc


def frame(end, freq, closes):
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame(dict(open=c, high=c + 1, low=c - 1, close=c),
                        index=pd.date_range(end=end, periods=len(c), freq=freq, tz='UTC'))


def fixture():
    h = frame('2026-01-05 11:00', '1h', [100] * 60)
    m = frame('2026-01-05 11:45', '15min', [100] * 59 + [104])
    f = frame('2026-01-05 12:00', '5min', [104] * 61)
    return h, m, f


def test_news_fail_closed_and_production_rr():
    h, m, f = fixture()
    assert hasattr(audit, 'run_live_replay'), 'production-function replay missing'
    assert audit.run_live_replay(h, m, f, horizon=1).empty
    f.loc[f.index[-1], ['high', 'low', 'close']] = [110, 104, 108]
    result = audit.run_live_replay(h, m, f, horizon=1, diagnostic_technical_only=True)
    assert len(result) == 1
    row = result.iloc[0]
    assert row.dir == 'BUY' and row.outcome == 'WIN'
    assert abs(row.r - sc.MIN_RR) < 1e-9
    assert row['label'] == 'diagnostic_technical_replay'
    assert result.attrs['historical_news'] is False


def test_state_order_closed_slices_and_alternating_cooldown():
    h, m, f = fixture()
    m = pd.concat([m, frame('2026-01-05 12:15', '15min', [90, 112])])
    f = frame('2026-01-05 12:30', '5min', [104] * 67)
    calls, decisions = [], []
    build, reset, send = sc.build_scalp_signal, sc.reset_setup_if_inside_range, sc.should_send
    def checked_build(hs, ms, fs, now, **kwargs):
        assert min(map(len, (hs, ms, fs))) >= 60
        for data, minutes in ((hs, 60), (ms, 15), (fs, 5)):
            assert (data.index + pd.Timedelta(minutes=minutes) <= now).all()
        calls.append('build')
        return build(hs, ms, fs, now, **kwargs)
    def checked_reset(state, ms):
        calls.append('reset')
        return reset(state, ms)
    def checked_send(sig, state, now):
        calls.append('send')
        decision = send(sig, state, now)
        decisions.append((sig.direction, decision, dict(state)))
        return decision
    with patch.object(sc, 'build_scalp_signal', checked_build), patch.object(sc, 'reset_setup_if_inside_range', checked_reset), patch.object(sc, 'should_send', checked_send):
        result = audit.run_live_replay(h, m, f, horizon=1, diagnostic_technical_only=True)
    assert result.dir.tolist() == ['BUY', 'SELL', 'BUY'], result
    assert result.t.iloc[-1] - result.t.iloc[0] < pd.Timedelta(minutes=45)
    assert calls == ['build', 'reset', 'send'] * (len(calls) // 3)
    assert any(not ok and 'setup' in reason for _, (ok, reason), _ in decisions)
    now = result.t.iloc[0].to_pydatetime()
    sig = build(h, m.iloc[:60], f.iloc[:60], now)
    state = {'last': {'direction': 'BUY', 'time': now.isoformat()}}
    assert 'cooldown' in send(sig, state, now + pd.Timedelta(minutes=44))[1]
    assert send(sig, state, now + pd.Timedelta(minutes=45))[0]


def test_asia_still_resets_setup():
    h, m, f = fixture()
    shift = pd.Timedelta(hours=11, minutes=45)
    h.index += shift; m.index += shift; f.index += shift
    m = pd.concat([m, frame('2026-01-05 23:45', '15min', [99])])
    f = pd.concat([f, frame('2026-01-06 00:00', '5min', [104] * 3)])
    reset = sc.reset_setup_if_inside_range
    resets = []
    def observe(state, ms):
        changed = reset(state, ms)
        resets.append((ms.index[-1], changed, dict(state)))
        return changed
    with patch.object(sc, 'reset_setup_if_inside_range', observe):
        result = audit.run_live_replay(h, m, f, horizon=1, diagnostic_technical_only=True)
    assert len(result) == 1 and result.t.iloc[0].hour == 23
    assert any(changed and 'active_setup' not in state for _, changed, state in resets)


def test_news_gate_does_not_consume_send_state():
    h, m, f = fixture()
    f = pd.concat([f, frame('2026-01-05 12:05', '5min', [104])])
    event = dict(title='CPI', country='USD', impact='High', time=pd.Timestamp('2026-01-05 12:00', tz='UTC').to_pydatetime())
    def calendar(now):
        return ([event] if now.minute == 0 else []), True
    result = audit.run_live_replay(h, m, f, horizon=1, news_at=calendar)
    assert len(result) == 1 and result.t.iloc[0].minute == 5
    for callback in (lambda now: ([event], True), lambda now: ([], False), lambda now: 1/0):
        assert audit.run_live_replay(h, m, f, horizon=1, news_at=callback).empty


def test_sell_tie_timeout_cost_and_validation():
    h, m, f = fixture()
    for data in (h, m, f):
        data[['open', 'high', 'low', 'close']] = 200 - data[['open', 'low', 'high', 'close']].to_numpy()
    f.loc[f.index[-1], ['high', 'low']] = [110, 80]
    with patch.object(audit, 'SPREAD', .2), patch.object(audit, 'SLIPPAGE', .1):
        result = audit.run_live_replay(h, m, f, horizon=1, diagnostic_technical_only=True)
        row = result.iloc[0]
        assert row.dir == 'SELL' and row.outcome == 'LOSS'
        assert abs(row.r - (-1 - .4 / row.risk)) < 1e-9
        f.loc[f.index[-1], ['high', 'low', 'close']] = [96.5, 95.5, 95.5]
        row = audit.run_live_replay(h, m, f, horizon=1, diagnostic_technical_only=True).iloc[0]
        assert row.outcome == 'TIMEOUT' and abs(row.r - (.5 - .4) / row.risk) < 1e-9
    empty = audit.run_live_replay(h.iloc[:0], m.iloc[:0], f.iloc[:0])
    assert empty.empty and {'t', 'dir', 'grade', 'r', 'outcome', 'label'} <= set(empty.columns)
    for kwargs in ({'horizon': 0}, {'horizon': True}, {'horizon': 1.5}, {'news_at': []}, {'diagnostic_technical_only': 1}, {'diagnostic_technical_only': True, 'news_at': lambda now: ([], True)}):
        try:
            audit.run_live_replay(h, m, f, **kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(kwargs)


def test_main_has_distinct_diagnostic_label():
    import inspect
    source = inspect.getsource(audit.main)
    assert 'diagnostic_technical_only=True' in source
    assert 'NO historical news' in source and 'assumed successful delivery' in source
    assert '"slope_down_without_asia"' in source


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_'):
            fn()
            print('PASS', name)
