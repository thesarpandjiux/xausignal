"""Offline assert regressions: python tests/veto_telemetry_test.py."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
import xau_scalp as sc


def frames():
    def frame(c, freq):
        return pd.DataFrame(dict(open=c, high=c+1, low=c-1, close=c),
                            index=pd.date_range('2026-01-01', periods=len(c), freq=freq, tz='UTC'))
    down = np.linspace(150, 100, 200)
    down[-1] -= 5
    return (frame(np.linspace(100, 200, 200), 'h'),
            frame(down, '15min'),
            frame(np.linspace(150, 100, 200), '5min'))


def test_narrow_bypass():
    h1, m15, m5 = frames()
    now = pd.Timestamp('2026-01-04T12:00Z').to_pydatetime()
    live = sc.build_scalp_signal(h1, m15, m5, now)
    assert live.direction == 'NO-TRADE'
    paper = sc.build_scalp_signal(h1, m15, m5, now, audit_bypass_h1=True)
    assert paper.direction == 'SELL' and paper.n_triggers == 2
    assert sc.build_scalp_signal(h1, m15, m5, now) == live
    assert sc.build_scalp_signal(h1, m15, m5, now.replace(hour=1), audit_bypass_h1=True).direction == 'NO-TRADE'
    assert sc.structure_direction(h1, m15, m5)[0] == -1
    falling = h1.copy()
    falling.loc[falling.index[-3:], 'close'] = 180
    assert sc.structure_direction(falling, m15)[0] == -1


def test_observation():
    import copy
    import tempfile
    import veto_telemetry as vt
    h1, m15, m5 = frames()
    now = pd.Timestamp('2026-01-04T12:00Z').to_pydatetime()
    live = sc.build_scalp_signal(h1, m15, m5, now)
    state = {}
    before = copy.deepcopy(state)
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        vt.observe(home, h1, m15, m5, now, 'test', 'none', None, live, state, False, 'blocked')
        vt.observe(home, h1, m15, m5, now, 'test', 'none', None, live, state, False, 'blocked')
        import json
        saved = json.loads((home / 'veto_state.json').read_text())
        assert len(saved['candidates']) == 1
        assert saved['decisions'][-1]['paper_blocker'] == 'DEDUP'
        assert state == before and sc.build_scalp_signal(h1, m15, m5, now) == live
    for mode, expected in [('blackout', 'NEWS_BLACKOUT'), ('quiet', 'NEWS_QUIET')]:
        with tempfile.TemporaryDirectory() as tmp:
            vt.observe(Path(tmp), h1, m15, m5, now, 'test', mode, None, live, {}, False, 'blocked')
            saved = json.loads((Path(tmp) / 'veto_state.json').read_text())
            assert not saved['candidates']
            assert expected in saved['decisions'][-1]['other_blockers']


def test_outcomes():
    import veto_telemetry as vt
    for high, low, outcome, r in [(100.5, 97, 'WIN', 2), (102, 99, 'LOSS', -1),
                                  (102, 97, 'LOSS', -1), (100.5, 99.5, 'TIMEOUT', 0)]:
        row = dict(time='2026-01-01T12:00:00+00:00', source='test', entry=100,
                   sl=101, tp1=98, rr1=2, direction='SELL', outcome='PENDING')
        bars = pd.DataFrame(dict(high=[high]*24, low=[low]*24, close=[100]*24),
                            index=pd.date_range(row['time'], periods=24, freq='5min'))
        vt.resolve(row, bars, 'test')
        assert row['outcome'] == outcome and row['gross_r'] == r
        row['outcome'] = 'PENDING'
        vt.resolve(row, bars.iloc[1:], 'test')
        assert row['outcome'] == 'PENDING'


def test_invalid_candidate_and_corrupt_state():
    import tempfile
    import json
    import veto_telemetry as vt
    h1, m15, m5 = frames()
    now = pd.Timestamp('2026-01-04T12:00Z').to_pydatetime()
    m5.loc[:, 'high'] = m5['close']
    m5.loc[:, 'low'] = m5['close']
    m5.loc[:, 'close'] = 100
    m5.loc[:, 'high'] = 100
    m5.loc[:, 'low'] = 100
    live = sc.build_scalp_signal(h1, m15, m5, now)
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        vt.observe(home, h1, m15, m5, now, 'test', 'none', None, live, {}, False, 'blocked')
        data = json.loads((home / 'veto_state.json').read_text())
        assert not data['candidates']
        assert 'INVALID_PAPER_LEVELS' in data['decisions'][-1]['paper_blocker']
        (home / 'veto_state.json').write_text('corrupt')
        try:
            vt.observe(home, h1, m15, m5, now, 'test', 'none', None, live, {}, False, 'blocked')
            assert False, 'corrupt state must not reset dedup'
        except json.JSONDecodeError:
            pass
        assert (home / 'veto_state.json').read_text() == 'corrupt'


def test_main_isolation():
    import copy
    import tempfile
    from unittest.mock import patch
    import veto_telemetry as vt
    h1, m15, m5 = frames()
    now = pd.Timestamp.now(tz='UTC')
    feeds = []
    import datafeed
    for frame, freq in [(h1, 'h'), (m15, '15min'), (m5, '5min')]:
        frame.index = pd.date_range(end=now.floor(freq)-pd.Timedelta(freq if freq != 'h' else '1h'), periods=len(frame), freq=freq)
        feeds.append(datafeed.Feed(frame, 'test', stale=False))
    results = []
    for enabled in [False, True]:
        with tempfile.TemporaryDirectory() as tmp:
            state = {'last': {'direction': 'BUY', 'time': now.isoformat()}}
            with patch.object(sc, 'BASE', Path(tmp)), patch.object(sc, 'load_state', return_value=state), \
                 patch.object(sc, 'save_state') as save, patch.object(sc, 'log_shadow_signal'), \
                 patch.object(sc, 'log_signal'), patch.object(sc.xs, 'send_telegram', return_value=True) as send, \
                 patch.object(sc.xs, 'market_open', return_value=True), \
                 patch.object(datafeed, 'get_ohlc', side_effect=feeds), \
                 patch.object(datafeed, 'get_calendar', return_value=([], True)), \
                 patch.object(sys, 'argv', ['xau_scalp.py']):
                if enabled:
                    sc.main()
                else:
                    with patch.object(vt, 'observe'):
                        sc.main()
                results.append((copy.deepcopy(state), send.call_args_list, save.call_args_list))
    assert results[0] == results[1]


if __name__ == '__main__':
    test_narrow_bypass()
    test_observation()
    test_outcomes()
    test_main_isolation()
    test_invalid_candidate_and_corrupt_state()
    print('veto telemetry tests passed')
