"""BR1 forward-only regressions: python tests/br1_test.py."""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
import xau_scalp as sc


def test_activation():
    assert importlib.util.find_spec('br1_shadow') is not None, 'BR1 recorder missing'
    import br1_shadow as br
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        now = pd.Timestamp('2026-09-07T12:01Z')
        data = br.load(home, now)
        assert data['activation_utc'] == now.isoformat()
        assert data['pending'] is None and data['native_state'] == {} and not data['candidates']
        br.save(home, data)
        assert br.load(home, now + pd.Timedelta(minutes=5)) == data
        assert {p.name for p in home.iterdir()} == {'br1_state.json'}
        (home / 'br1_state.json').write_text('broken')
        try:
            br.load(home, now)
        except json.JSONDecodeError:
            pass
        else:
            assert False, 'corrupt state must never reset activation/dedup'


def test_pending_lifecycle():
    import br1_shadow as br
    assert hasattr(br, 'step'), 'causal BR1 step missing'
    t = pd.Timestamp('2026-09-07T12:05Z')
    def fresh():
        return dict(pending=None, last_bar=None)
    def step(data, n, close=101, low=100, high=102, allowed=True, previous=99):
        return br.step(data, t + pd.Timedelta(minutes=n * 5),
                       dict(close=close, low=low, high=high), previous, 100, 90,
                       '20260907T1145', lambda d: allowed)
    data = fresh()
    assert step(data, 0) is None and data['pending']['direction'] == 1
    frozen = data['pending'].copy()
    assert step(data, 0) is None and data['pending'] == frozen
    assert step(data, 1) == frozen
    assert data['pending'] == frozen, 'score failure must be able to wait'
    for n in range(2, 7):
        assert step(data, n) == frozen
    assert step(data, 7) is None and data['pending'] is None
    for kwargs in [dict(close=99), dict(allowed=False)]:
        data = fresh(); step(data, 0)
        assert step(data, 1, **kwargs) is None and data['pending'] is None
    data = fresh(); step(data, 0)
    assert step(data, 2) is None and data['pending'] is None
    data = fresh(); step(data, 0)
    assert step(data, 1, close=100) is None and data['pending'] is not None
    data = fresh()
    assert step(data, 0, close=89, previous=91) is None
    assert data['pending']['direction'] == -1
    assert step(data, 1, close=89, high=90)['direction'] == -1
    data = fresh(); step(data, 0)
    assert step(data, 1, close=89, previous=91) is None and data['pending'] is None
    data = fresh(); step(data, 0)
    data['last_bar'] = (t - pd.Timedelta(days=1) + pd.Timedelta(minutes=5)).isoformat()
    assert step(data, 1) is None and data['pending'] is None


def frames(t, direction=1):
    result = []
    for freq, minutes in [('h', 60), ('15min', 15), ('5min', 5)]:
        c = np.linspace(80, 100, 100) if direction == 1 else np.linspace(120, 100, 100)
        if minutes == 15:
            c[-5:] += direction * np.arange(1, 6)
        if minutes == 60:
            c = np.full(100, 100.)
        f = pd.DataFrame(dict(open=c, high=c+1, low=c-1, close=c),
                         index=pd.date_range(end=t.floor(freq)-pd.Timedelta(minutes=minutes), periods=100, freq=freq))
        result.append(f)
    return result


def test_observe_causal_native():
    import br1_shadow as br
    assert hasattr(br, 'observe'), 'BR1 observe missing'
    from unittest.mock import patch
    from dataclasses import asdict
    import copy
    t = pd.Timestamp('2026-09-07T12:05Z')
    for d in [1, -1]:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            ff = frames(t, d)
            br.observe(home, *ff, t, 'test', 'none', None)
            data = br.load(home, t)
            assert not data['pending'] and not data['candidates'], 'activation must skip existing closed bar'
            # Controlled frozen range; real momentum/ATR/sweep and builder.
            hi = float(ff[1].close.iloc[-21:-1].max()); lo = float(ff[1].close.iloc[-21:-1].min())
            level = hi if d == 1 else lo
            for n in [1, 2]:
                now = t + pd.Timedelta(minutes=5*n, seconds=20)
                baropen = t + pd.Timedelta(minutes=5*(n-1))
                ff[2].loc[baropen] = [level, level+2, level-2, level+d]
                if n == 1:
                    ff[2].iloc[-2, ff[2].columns.get_loc('close')] = level
                before = [f.copy(deep=True) for f in ff]
                live = asdict(sc.build_scalp_signal(*ff, now))
                with patch.object(sc.xs, 'send_telegram', side_effect=AssertionError('no send')):
                    br.observe(home, *ff, now, 'test', 'none', None)
                assert asdict(sc.build_scalp_signal(*ff, now)) == live
                for a, b in zip(ff, before):
                    pd.testing.assert_frame_equal(a, b)
            data = br.load(home, t)
            assert len(data['candidates']) == 1, data
            row = next(iter(data['candidates'].values()))
            assert row['direction'] == ('BUY' if d == 1 else 'SELL')
            assert row['rr1'] == 2 and abs(row['entry'] - row['sl']) > 0
            assert row['bar_close'] < row['time']
            assert data['pending'] is None and data['native_state']['last']['direction'] == row['direction']
            saved = (home / 'br1_state.json').read_bytes()
            br.observe(home, *ff, now, 'test', 'none', None)
            assert (home / 'br1_state.json').read_bytes() == saved
            # Future input must not leak into decisions (prefix invariance).
            future = [f.copy() for f in ff]
            for f in future:
                f.loc[now.ceil('h') + pd.Timedelta(hours=1)] = [500, 600, 1, 550]
            br.observe(home, *future, now, 'test', 'none', None)
            assert (home / 'br1_state.json').read_bytes() == saved


def test_exits():
    import br1_shadow as br
    assert hasattr(br, 'resolve'), 'BR1 12-bar resolver missing'
    for d in [1, -1]:
        for high, low, close, outcome, gross in [(103, 98, 100, 'LOSS', -1),
                (102.5 if d == 1 else 100.5, 99.5 if d == 1 else 97.5, 100, 'WIN', 2),
                (100.5, 99.5, 100.25, 'TIMEOUT', .25*d)]:
            row = dict(time='2026-09-07T12:00:20+00:00', entry=100., sl=100-d,
                       tp1=100+2*d, direction='BUY' if d == 1 else 'SELL',
                       risk=1., source='test', outcome='PENDING')
            bars = pd.DataFrame(dict(high=[high]*13, low=[low]*13, close=[close]*13),
                index=pd.date_range('2026-09-07T12:00Z', periods=13, freq='5min'))
            # Candle containing decision is excluded; full next candle starts 12:05.
            br.resolve(row, bars.iloc[:1], 'test')
            assert row['outcome'] == 'PENDING'
            br.resolve(row, bars, 'other')
            assert row['outcome'] == 'PENDING'
            br.resolve(row, bars.drop(bars.index[1]), 'test')
            assert row['outcome'] == 'PENDING'
            br.resolve(row, bars, 'test')
            assert row['outcome'] == outcome and row['gross_r'] == gross, row
            assert row['net_r'] == gross - .5
            assert row['closed_at'] >= '2026-09-07T12:10'


def test_gates_and_risk():
    import br1_shadow as br
    t = pd.Timestamp('2026-09-07T12:05Z')
    ff = frames(t)
    up = ff[0].copy()
    up['close'] = np.linspace(100, 200, len(up))
    up['high'], up['low'] = up.close+1, up.close-1
    assert br.allowed_h1(up, 1, 'none') and not br.allowed_h1(up, -1, 'none')
    assert br.allowed_h1(up, -1, 'aggressive')
    for d in [1, -1]:
        ff = frames(t, d)
        p = dict(direction=d, setup_id='frozen', level=100)
        for mode, mult in [('none', .8), ('aggressive', .6)]:
            if mode == 'aggressive':
                ff[2].loc[ff[2].index[-5:], 'close'] += d*np.arange(1, 6)
            sig = br.retest_signal(*ff, t, 'test', mode, None, p)
            assert sig.direction != 'NO-TRADE'
            assert abs(abs(sig.price-sig.stop_loss)-mult*float(sc.xs.atr(ff[2]).iloc[-1])) < 1e-9
            assert abs(sig.targets[0]-sig.price) == 2*abs(sig.price-sig.stop_loss)
            assert sig.breakout_atr == float(sc.xs.atr(ff[1]).iloc[-1])
    for mode in ['blackout', 'quiet', None]:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            data = br.load(home, t-pd.Timedelta(minutes=5))
            data['pending'] = dict(direction=1, level=99., created=(t-pd.Timedelta(minutes=5)).isoformat())
            data['last_bar'] = (t-pd.Timedelta(minutes=5)).isoformat()
            br.save(home, data)
            br.observe(home, *frames(t), t, 'test', mode, None)
            data = br.load(home, t)
            assert data['pending'] is None and not data['candidates']


def test_runtime_fail_closed():
    import br1_shadow as br
    import datafeed
    from unittest.mock import patch
    assert hasattr(br, 'run'), 'scheduled recorder missing'
    t = pd.Timestamp('2026-09-07T12:05Z')
    ff = frames(t)
    feeds = [datafeed.Feed(f, 'test', stale=False) for f in ff]
    for failure in ['calendar', 'stale', 'market', 'exception']:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home/'scalp_state.json').write_text('live sentinel')
            (home/'veto_state.json').write_text('veto sentinel')
            data = br.load(home, t-pd.Timedelta(minutes=10))
            data['pending'] = dict(direction=1, level=99, created=(t-pd.Timedelta(minutes=5)).isoformat())
            br.save(home, data)
            feeds[-1].stale = failure == 'stale'
            with patch.object(datafeed, 'get_ohlc', side_effect=feeds), \
                 patch.object(datafeed, 'get_calendar', side_effect=RuntimeError('missing') if failure=='exception' else None, return_value=([], failure!='calendar')), \
                 patch.object(sc.xs, 'market_open', return_value=failure!='market'), \
                 patch.object(sc.xs, 'send_telegram', side_effect=AssertionError('live send forbidden')):
                br.run(home, t)
            data = br.load(home, t)
            assert data['pending'] is None and not data['candidates']
            assert (home/'scalp_state.json').read_text() == 'live sentinel'
            assert (home/'veto_state.json').read_text() == 'veto sentinel'


def test_resolution_wired_and_native_consumption():
    import br1_shadow as br
    t = pd.Timestamp('2026-09-07T12:05Z')
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        data = br.load(home, t-pd.Timedelta(minutes=10))
        data['candidates']['old'] = dict(time=(t-pd.Timedelta(minutes=10)).isoformat(),
            direction='BUY', entry=100, sl=99, tp1=102, risk=1, source='test', outcome='PENDING')
        br.save(home, data)
        br.observe(home, *frames(t), t, 'test', 'none', None)
        assert br.load(home,t)['candidates']['old']['outcome'] == 'LOSS', 'outcomes must update in recorder'
    for score in [False, True]:
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            data = br.load(home, t-pd.Timedelta(minutes=10))
            data['pending'] = dict(direction=1, level=100, created=(t-pd.Timedelta(minutes=5)).isoformat(), setup_id='frozen')
            data['last_bar'] = (t-pd.Timedelta(minutes=5)).isoformat()
            data['native_state'] = dict(last=dict(direction='BUY', time=(t-pd.Timedelta(minutes=5)).isoformat()))
            br.save(home, data)
            ff = frames(t)
            ff[2].iloc[-1] = [100, 102, 99, 101]
            if not score:
                ff[1].loc[:, 'close'] = 100.
            br.observe(home, *ff, t, 'test', 'none', None)
            data = br.load(home,t)
            assert not data['candidates']
            assert (data['pending'] is None) == score, data
            assert data['reason'].startswith('native_reject') if score else data['reason'] == 'score_wait'


def test_duplicate_gate_cancel_and_source_change():
    import br1_shadow as br
    t = pd.Timestamp('2026-09-07T12:05Z')
    for mode, source in [('blackout', 'test'), ('none', 'changed')]:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            data = br.load(home, t-pd.Timedelta(minutes=10))
            data['pending'] = dict(direction=1, level=99, created=(t-pd.Timedelta(minutes=5)).isoformat())
            data['last_bar'] = t.isoformat()
            data['last_source'] = 'test'
            br.save(home, data)
            br.observe(home, *frames(t), t+pd.Timedelta(seconds=30), source, mode, None)
            assert br.load(home,t)['pending'] is None, 'gate/source change cancels even repeated bar'


def test_cancellation_consumes_bar_and_missing_previous():
    import br1_shadow as br
    t = pd.Timestamp('2026-09-07T12:05Z')
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        data = br.load(home, t-pd.Timedelta(minutes=10))
        data['pending'] = dict(direction=1, level=99, created=(t-pd.Timedelta(minutes=5)).isoformat())
        data['last_bar'] = (t-pd.Timedelta(minutes=5)).isoformat()
        br.save(home, data)
        br.observe(home, *frames(t), t, 'test', 'blackout', None)
        assert br.load(home,t)['last_bar'] == t.isoformat(), 'cancel consumes current bar'
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        data = br.load(home, t-pd.Timedelta(minutes=10))
        br.save(home, data)
        ff = frames(t)
        ff[2].iloc[-1] = [100, 110, 99, 109]
        ff[2] = ff[2].drop(ff[2].index[-2])
        br.observe(home, *ff, t, 'test', 'none', None)
        assert br.load(home,t)['pending'] is None, 'cross cannot use missing previous M5'


def test_blocked_reset_and_activation_boundary():
    import br1_shadow as br
    t = pd.Timestamp('2026-09-07T12:05Z')
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        data = br.load(home, t-pd.Timedelta(minutes=10))
        data['native_state']['active_setup'] = dict(direction='BUY', level=1000)
        data['pending'] = dict(direction=1, level=99, created=(t-pd.Timedelta(minutes=5)).isoformat())
        br.save(home, data)
        br.observe(home, *frames(t), t, 'test', 'blackout', None)
        assert 'active_setup' not in br.load(home,t)['native_state'], 'reset even blocked decision'


def test_workflow_contract():
    root = Path(__file__).resolve().parents[1]
    workflow = (root/'.github/workflows/scalp.yml').read_text()
    assert 'python br1_shadow.py' in workflow, 'existing schedule not wired'
    assert 'br1_state.json' in workflow and 'actions/upload-artifact@v4' in workflow
    assert 'python tests/br1_test.py' in (root/'.github/workflows/test.yml').read_text()


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_'):
            fn()
            print(name, 'passed')
