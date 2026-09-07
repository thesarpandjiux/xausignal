"""Frozen BR1 prospective paper recorder. Never sends or writes live state."""
import json
import os
from pathlib import Path
import pandas as pd
import math
import types
import xau_scalp as sc


def allowed_h1(h1, direction, mode):
    if direction == 1 or mode == 'aggressive':
        return True
    e20, e50 = sc.xs.ema(h1.close, 20), sc.xs.ema(h1.close, 50)
    a = float(sc.xs.atr(h1).iloc[-1])
    gap = (float(e20.iloc[-1]) - float(e50.iloc[-1])) / a if a else 0.
    return not (gap > .5 and float(e20.iloc[-1]) >= float(e20.iloc[-4]))


def retest_signal(h1, m15, m5, now, source, mode, event, pending):
    # Private globals: reuse exact native builder without mutating live module.
    namespace = dict(sc.build_scalp_signal.__globals__)
    namespace['structure_direction'] = lambda *a, **k: (
        pending['direction'], sc.Trigger('Structure Break M15', True, 'BR1 frozen level retest'))
    builder = types.FunctionType(sc.build_scalp_signal.__code__, namespace,
                                 argdefs=sc.build_scalp_signal.__defaults__)
    builder.__kwdefaults__ = sc.build_scalp_signal.__kwdefaults__
    sig = builder(h1, m15, m5, now, source, mode, event)
    sig.setup_id, sig.breakout_level = pending['setup_id'], pending['level']
    return sig


def observe(home, h1, m15, m5, now, source, news_mode, news_event):
    now = pd.Timestamp(now)
    data = load(home, now)
    # Defensive closed-input prefix, even if caller already filtered frames.
    h1, m15, m5 = [f.loc[f.index + pd.Timedelta(minutes=n) <= now].copy()
                   for f, n in [(h1, 60), (m15, 15), (m5, 5)]]
    if min(map(len, (h1, m15, m5))) < 60:
        raise ValueError('insufficient closed bars')
    sc.reset_setup_if_inside_range(data['native_state'], m15)
    t = m5.index[-1] + pd.Timedelta(minutes=5)
    source_changed = data.get('last_source', source.split(' (')[0]) != source.split(' (')[0]
    p = data['pending']
    if (source_changed or m5.index[-1]-m5.index[-2] != pd.Timedelta(minutes=5) or
        (p and (news_mode not in ('none', 'aggressive') or
              now.hour < sc.ASIA_BLOCK_UTC_UNTIL or now.date() != pd.Timestamp(p['created']).date() or
              not allowed_h1(h1, p['direction'], news_mode)))):
        data['pending'] = None
        data['last_bar'] = max(t, pd.Timestamp(data['last_bar']) if data['last_bar'] else t).isoformat()
        data['last_source'] = source.split(' (')[0]
        data['decisions'].append(dict(time=now.isoformat(), reason='cancel_gate_or_source'))
        save(home, data)
        return
    data['last_source'] = source.split(' (')[0]
    if data['last_bar'] and t <= pd.Timestamp(data['last_bar']):
        return
    for row in data['candidates'].values():
        resolve(row, m5, source)
    if t <= pd.Timestamp(data['activation_utc']):
        data['last_bar'] = t.isoformat()
        data['reason'] = 'activation_no_backfill'
    else:
        permission = lambda d: (now.hour >= sc.ASIA_BLOCK_UTC_UNTIL and
            t.hour >= sc.ASIA_BLOCK_UTC_UNTIL and now.date() == t.date() and
            news_mode in ('none', 'aggressive') and allowed_h1(h1, d, news_mode))
        p = step(data, t, m5.iloc[-1], float(m5.close.iloc[-2]),
                 float(m15.close.iloc[-21:-1].max()), float(m15.close.iloc[-21:-1].min()),
                 m15.index[-1].strftime('%Y%m%dT%H%M'), permission)
        if p:
            sig = retest_signal(h1, m15, m5, now, source, news_mode, news_event, p)
            if sig.direction == 'NO-TRADE':
                data['reason'] = 'score_wait'
            else:
                data['pending'] = None  # Eligible candidate consumes even native rejection.
                ok, reason = sc.should_send(sig, data['native_state'], now.to_pydatetime())
                risk = abs(sig.price - sig.stop_loss)
                valid = all(math.isfinite(v) for v in [sig.price, sig.stop_loss, *sig.targets, sig.breakout_atr]) and risk > 0
                data['reason'] = 'accepted' if ok and valid else 'native_reject:' + reason if valid else 'invalid_risk'
                if ok and valid:
                    key = 'BR1:' + t.isoformat() + ':' + sig.direction
                    data['candidates'][key] = dict(id=key, time=now.isoformat(), bar_close=t.isoformat(),
                        source=source.split(' (')[0], source_sha=os.getenv('GITHUB_SHA', 'local-unversioned'),
                        direction=sig.direction, grade=sig.grade, score=sig.n_triggers,
                        entry=sig.price, sl=sig.stop_loss, tp1=sig.targets[0], rr1=sig.rr[0],
                        setup_id=sig.setup_id, origin=p, risk=risk, outcome='PENDING',
                        gross_r=None, net_r=None, horizon_bars=12, cost_price=.5,
                        entry_latency_seconds=(now-t).total_seconds(), news_mode=news_mode,
                        news_event=news_event)
                    data['native_state']['last'] = dict(direction=sig.direction, time=now.isoformat())
                    data['native_state']['active_setup'] = dict(id=sig.setup_id, direction=sig.direction, level=sig.breakout_level)
    data['decisions'].append(dict(time=now.isoformat(), bar_close=t.isoformat(), reason=data['reason'],
                                 news_mode=news_mode, source=source, source_sha=os.getenv('GITHUB_SHA', 'local-unversioned'),
                                 last_closed={k: (f.index[-1]+pd.Timedelta(minutes=n)).isoformat()
                                              for k, f, n in [('h1', h1, 60), ('m15', m15, 15), ('m5', m5, 5)]}))
    save(home, data)


def resolve(row, m5, source):
    if row['outcome'] != 'PENDING':
        return
    first = pd.Timestamp(row['time']).ceil('5min')
    bars = m5.loc[(m5.index >= first) & (m5.index < first + pd.Timedelta(minutes=60))]
    if source.split(' (')[0] != row['source']:
        row['coverage'] = 'source mismatch; pending'
        return
    expected = pd.date_range(first, periods=len(bars), freq='5min')
    if bars.empty or not bars.index.equals(expected) or not all(math.isfinite(float(v)) for v in bars[['high', 'low', 'close']].to_numpy().flat):
        row['coverage'] = 'missing/noncontiguous bars; pending'
        return
    row['coverage'] = f'{len(bars)}/12 closed M5 bars'
    d = 1 if row['direction'] == 'BUY' else -1
    for i, (t, bar) in enumerate(bars.iterrows()):
        loss = bar.low <= row['sl'] if d == 1 else bar.high >= row['sl']
        win = bar.high >= row['tp1'] if d == 1 else bar.low <= row['tp1']
        if loss or win or i == 11:
            gross = -1. if loss else 2. if win else d*(bar.close-row['entry'])/row['risk']
            row.update(outcome='LOSS' if loss else 'WIN' if win else 'TIMEOUT', gross_r=float(gross),
                       net_r=float(gross-.5/row['risk']), closed_at=(t+pd.Timedelta(minutes=5)).isoformat())
            return


def step(data, t, bar, previous, hi, lo, origin, allowed):
    last = pd.Timestamp(data['last_bar']) if data['last_bar'] else None
    if last is not None and t <= last:
        return None
    gap = last is not None and (t - last != pd.Timedelta(minutes=5) or t.date() != last.date())
    data['last_bar'] = t.isoformat()
    p = data['pending']
    data['reason'] = 'idle'
    if p:
        d, level = p['direction'], p['level']
        if gap or not allowed(d) or t - pd.Timestamp(p['created']) > pd.Timedelta(minutes=30) or d * (bar['close'] - level) < 0:
            data['pending'] = None
            data['reason'] = 'cancel'
            return None
        touch = bar['low'] <= level if d == 1 else bar['high'] >= level
        data['reason'] = 'waiting'
        if t > pd.Timestamp(p['created']) and touch and d * (bar['close'] - level) > 0:
            data['reason'] = 'retest'
            return p
        return None
    d = 1 if previous <= hi < bar['close'] else -1 if previous >= lo > bar['close'] else 0
    if not gap and d and allowed(d):
        level = float(hi if d == 1 else lo)
        side = 'BUY' if d == 1 else 'SELL'
        data['pending'] = dict(direction=d, level=level, created=t.isoformat(),
                               origin_m15=origin, setup_id=f'{side}:{origin}:{level:.2f}')
        data['reason'] = 'armed'
    return None


def run(home, now):
    import datafeed
    # Persist activation before network; outages cannot move start backward.
    data = load(home, now)
    save(home, data)
    try:
        if not sc.xs.market_open(now):
            raise RuntimeError('MARKET_CLOSED')
        feeds = [datafeed.get_ohlc(tf, sc.xs.BARS) for tf in ('1h', '15m', '5m')]
        sc.validate_live_feeds(feeds, now)
        frames = [sc.closed_frame(f, tf, now) for f, tf in zip(feeds, ('1h', '15m', '5m'))]
        for f, minutes in zip(frames, (60, 15, 5)):
            if (len(f) < 60 or not f.index.is_unique or not f.index.is_monotonic_increasing or
                pd.Timestamp(now) - (f.index[-1]+pd.Timedelta(minutes=minutes)) >= pd.Timedelta(minutes=minutes) or
                not all(math.isfinite(float(v)) for v in f[['open', 'high', 'low', 'close']].to_numpy().flat)):
                raise RuntimeError('INVALID_OR_STALE_CLOSED_FEED')
        events, trusted = datafeed.get_calendar()
        if not trusted:
            raise RuntimeError('NEWS_UNTRUSTED')
        mode, event = sc.news_window(events, now)
        observe(home, *frames, now, feeds[-1].label(), mode, event)
    except Exception as ex:
        # Do not reset activation/native dedup or swallow corrupt state.
        data = load(home, now)
        data['pending'] = None
        data['decisions'].append(dict(time=now.isoformat(), reason='UNAVAILABLE', detail=str(ex),
                                      source_sha=os.getenv('GITHUB_SHA', 'local-unversioned')))
        save(home, data)
        print(f'BR1 fail-closed: {ex}')
    data = load(home, now)
    print(json.dumps(dict(variant='BR1', activation_utc=data['activation_utc'],
                          activation_sha=data['activation_sha'], last_bar=data['last_bar'],
                          decisions=len(data['decisions']), candidates=len(data['candidates']),
                          latest=data['decisions'][-1] if data['decisions'] else None)))


def load(home, now):
    path = home / 'br1_state.json'
    if path.exists():
        data = json.loads(path.read_text())
        if data['schema'] != 1 or data['variant'] != 'BR1':
            raise ValueError('unsupported BR1 state')
        return data
    return dict(schema=1, variant='BR1', activation_utc=now.isoformat(),
                activation_sha=os.getenv('GITHUB_SHA', 'local-unversioned'),
                pending=None, native_state={}, candidates={}, decisions=[], last_bar=None)


def save(home, data):
    home.mkdir(parents=True, exist_ok=True)
    path = home / 'br1_state.json'
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, default=str, allow_nan=False, indent=2) + '\n')
    tmp.replace(path)


if __name__ == '__main__':
    from datetime import datetime, timezone
    run(Path(os.getenv('XAU_HOME', '~/.xau_signal')).expanduser(), datetime.now(timezone.utc))
