#!/usr/bin/env python3
"""Audit simetris BUY/SELL xau_scalp — baseline vs varian penentu arah.
Tidak mengubah file produksi. Data dari cache kalibrasi (Dukascopy 5000 bar).
"""
import sys
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import xau_scalp as sc
import xau_signal as xs

_os = __import__("os")
CACHE = Path(_os.environ.get("SCALP_AUDIT_CACHE", "/tmp/scalpcalib/cache"))
STRUCTURE_LOOKBACK = int(_os.environ.get("SCALP_STRUCTURE_LOOKBACK", "20"))
MIN_BREAK_ATR = float(_os.environ.get("SCALP_MIN_BREAK_ATR", "0"))
MOMENTUM_MODE = _os.environ.get("SCALP_MOMENTUM_MODE", "baseline")
AUDIT_RR = float(_os.environ.get("SCALP_AUDIT_RR", "1.2"))
HORIZON_BARS = int(_os.environ.get("SCALP_HORIZON_BARS", "12"))
CONTINUATION_ATR = float(_os.environ.get("SCALP_CONTINUATION_ATR", "0.5"))
MIN_BODY_ATR = float(_os.environ.get("SCALP_MIN_BODY_ATR", "0.3"))
MAX_CLOSE_WICK = float(_os.environ.get("SCALP_MAX_CLOSE_WICK", "0.25"))


def load(tf):
    d = pd.read_csv(CACHE / f"ohlc_{tf}.csv", index_col=0, parse_dates=True)
    d.index = pd.to_datetime(d.index, utc=True)
    return d


def direction_m15(m15):
    """Penentu arah dari M15: EMA20 vs EMA50 + slope; kembalikan +1/-1/0."""
    c = m15["close"]
    e20, e50 = xs.ema(c, 20), xs.ema(c, 50)
    slope_up = e20.iloc[-1] > e20.iloc[-6]
    if e20.iloc[-1] > e50.iloc[-1] and slope_up:
        return 1
    if e20.iloc[-1] < e50.iloc[-1] and not slope_up:
        return -1
    return 0


def direction_h1_veto(h1):
    """M15 penentu arah, tapi H1 ekstrem memveto:
    jika EMA20 H1 jauh di bawah EMA50 (> 0.5 ATR H1), paksa SELL; simetris."""
    c = h1["close"]
    e20, e50 = xs.ema(c, 20), xs.ema(c, 50)
    a = float(xs.atr(h1).iloc[-1])
    gap = (e20.iloc[-1] - e50.iloc[-1]) / a if a else 0.0
    if gap > 0.5:
        return 1
    if gap < -0.5:
        return -1
    return direction_m15(m15_for_h1(h1)) if False else 0


def m15_for_h1(h1):
    return h1


def h1_extreme(h1):
    """+1/-1 hanya saat gap EMA H1 ekstrem; 0 berarti tidak memveto."""
    c = h1["close"]
    e20, e50 = xs.ema(c, 20), xs.ema(c, 50)
    a = float(xs.atr(h1).iloc[-1])
    gap = (e20.iloc[-1] - e50.iloc[-1]) / a if a else 0.0
    return 1 if gap > 0.5 else -1 if gap < -0.5 else 0


def direction_m15_with_h1_veto(h1, m15):
    direction = direction_m15(m15)
    extreme = h1_extreme(h1)
    return 0 if extreme and direction != extreme else direction


def direction_struct(h1, m15):
    """Structure-break M15 memakai lookback audit yang bisa divariasikan."""
    c = m15["close"]
    history = c.iloc[-STRUCTURE_LOOKBACK - 1:-1]
    if len(history) < STRUCTURE_LOOKBACK:
        return 0
    range_high = float(history.max())
    range_low = float(history.min())
    px = float(c.iloc[-1])
    atr_m15 = float(xs.atr(m15).iloc[-1])
    buy_break = px > range_high and (px - range_high) >= MIN_BREAK_ATR * atr_m15
    sell_break = px < range_low and (range_low - px) >= MIN_BREAK_ATR * atr_m15
    direction = 1 if buy_break and direction_m15(m15) >= 0 else -1 if sell_break and direction_m15(m15) <= 0 else 0
    extreme = h1_extreme(h1)
    return 0 if extreme and direction and direction != extreme else direction


def direction_pullback(h1, m15):
    """Continuation: H1 jelas, tiga close M15 pullback, lalu break candle sebelumnya."""
    direction = sc.trend_h1(h1)[0]
    if not direction or len(m15) < 5:
        return 0
    c = m15["close"]
    previous = m15.iloc[-2]
    pullback = c.iloc[-2] < c.iloc[-4] if direction > 0 else c.iloc[-2] > c.iloc[-4]
    resumed = (c.iloc[-1] > previous["high"] if direction > 0
               else c.iloc[-1] < previous["low"])
    return direction if pullback and resumed else 0


def direction_struct_body(h1, m15):
    direction = direction_struct(h1, m15)
    if not direction:
        return 0
    body = abs(float(m15["close"].iloc[-1]) - float(m15["open"].iloc[-1]))
    atr_m15 = float(xs.atr(m15).iloc[-1])
    return direction if atr_m15 and body >= MIN_BODY_ATR * atr_m15 else 0


def direction_struct_close_location(h1, m15):
    direction = direction_struct(h1, m15)
    if not direction:
        return 0
    bar = m15.iloc[-1]
    spread = float(bar["high"] - bar["low"])
    if spread <= 0:
        return 0
    wrong_wick = (float(bar["high"] - bar["close"]) if direction > 0
                  else float(bar["close"] - bar["low"]))
    return direction if wrong_wick / spread <= MAX_CLOSE_WICK else 0


def momentum_passed(m15, direction):
    if MOMENTUM_MODE in {"rsi_extreme_macd", "rsi_30_80_macd"}:
        c = m15["close"]
        rsi = float(xs.rsi(c).iloc[-1])
        _, _, hist = xs.macd(c)
        macd_up = float(hist.iloc[-1]) > float(hist.iloc[-4])
        if MOMENTUM_MODE == "rsi_extreme_macd":
            return (rsi < 30 and macd_up) if direction > 0 else (rsi > 80 and not macd_up)
        return (rsi > 30 and macd_up) if direction > 0 else (rsi < 80 and not macd_up)
    return sc.momentum_m15(m15, direction).passed


def run(h1, m15, m5, direction_fn, horizon=HORIZON_BARS, setup_dedup=None):
    rows = []
    last_by_direction = {}
    active_by_direction = {}
    for i in range(120, len(m5) - horizon):
        # Keputusan dibuat setelah candle M5 kandidat tutup, bukan saat mulai.
        ts = m5.index[i] + pd.Timedelta(minutes=5)
        # Index Dukascopy menandai awal candle. Pada ts keputusan, candle H1/M15
        # yang sedang berjalan belum punya close final; memasukkannya memberi
        # look-ahead bias. Hanya pakai candle yang sudah benar-benar tutup.
        h1s = h1[h1.index + pd.Timedelta(hours=1) <= ts]
        m15s = m15[m15.index + pd.Timedelta(minutes=15) <= ts]
        if len(h1s) < 60 or len(m15s) < 60:
            continue
        close = float(m15s["close"].iloc[-1])
        for active_dir, active_level in list(active_by_direction.items()):
            inside = close <= active_level if active_dir == "BUY" else close >= active_level
            if inside:
                active_by_direction.pop(active_dir)
        direction = direction_fn(h1s, m15s)
        if direction == 0:
            continue
        dirn = "BUY" if direction > 0 else "SELL"
        history = m15s["close"].iloc[-STRUCTURE_LOOKBACK - 1:-1]
        level = float(history.max() if direction > 0 else history.min())
        active = active_by_direction.get(dirn)
        if setup_dedup is not None and active is not None:
            atr_m15 = float(xs.atr(m15s).iloc[-1])
            advanced = False if np.isinf(setup_dedup) else (
                level >= active + setup_dedup * atr_m15 if direction > 0
                else level <= active - setup_dedup * atr_m15)
            if not advanced:
                continue
        m5s = m5.iloc[:i + 1]
        trend_direction, trend_trigger = sc.trend_h1(h1s)
        momentum_ok = momentum_passed(m15s, direction)
        sweep = sc.liquidity_sweep(m5s, direction)
        # H1 tetap wajib sebagai regime gate, tetapi tidak wajib searah kecuali baseline.
        regime_ok = trend_direction != 0
        n = int(regime_ok) + int(momentum_ok) + int(sweep.passed)
        if n < sc.MIN_TRIGGERS:
            continue
        px = float(m5s["close"].iloc[-1])
        a = float(xs.atr(m5s).iloc[-1])
        sl = px - direction * sc.ATR_SL_MULT * a
        risk = abs(px - sl)
        tp1 = px + direction * AUDIT_RR * risk
        if dirn in last_by_direction and ts - last_by_direction[dirn] < pd.Timedelta(minutes=45):
            continue
        last_by_direction[dirn] = ts
        if setup_dedup is not None:
            active_by_direction[dirn] = level
        won = None
        for _, bar in m5.iloc[i + 1:i + 1 + horizon].iterrows():
            if dirn == "BUY":
                if bar["low"] <= sl:
                    won = False
                    break
                if bar["high"] >= tp1:
                    won = True
                    break
            else:
                if bar["high"] >= sl:
                    won = False
                    break
                if bar["low"] <= tp1:
                    won = True
                    break
        outcome = "TIMEOUT" if won is None else "WIN" if won else "LOSS"
        r_result = 0.0 if won is None else AUDIT_RR if won else -1.0
        rows.append({"t": ts, "dir": dirn, "grade": {3: "A", 2: "B"}[n],
                     "n": n, "won": won, "outcome": outcome, "r": r_result})
    return pd.DataFrame(rows)


def run_retest(h1, m15, m5, horizon=HORIZON_BARS, wait_bars=6):
    rows, pending, last_by_direction = [], None, {}
    seen_m15 = None
    for i in range(120, len(m5) - horizon):
        ts = m5.index[i] + pd.Timedelta(minutes=5)
        h1s = h1[h1.index + pd.Timedelta(hours=1) <= ts]
        m15s = m15[m15.index + pd.Timedelta(minutes=15) <= ts]
        if len(h1s) < 60 or len(m15s) < 60:
            continue
        m15_stamp = m15s.index[-1]
        if m15_stamp != seen_m15:
            seen_m15 = m15_stamp
            direction = direction_struct(h1s, m15s)
            if direction:
                history = m15s["close"].iloc[-STRUCTURE_LOOKBACK - 1:-1]
                level = float(history.max() if direction > 0 else history.min())
                pending = {"direction": direction, "level": level,
                           "created": i, "expires": i + wait_bars}
        if not pending:
            continue
        if i <= pending["created"]:
            continue
        if i > pending["expires"]:
            pending = None
            continue
        direction, level = pending["direction"], pending["level"]
        bar = m5.iloc[i]
        retest = (bar["low"] <= level < bar["close"] if direction > 0
                  else bar["high"] >= level > bar["close"])
        if not retest or not momentum_passed(m15s, direction):
            continue
        dirn = "BUY" if direction > 0 else "SELL"
        if dirn in last_by_direction and ts - last_by_direction[dirn] < pd.Timedelta(minutes=45):
            continue
        last_by_direction[dirn] = ts
        px = float(bar["close"])
        a = float(xs.atr(m5.iloc[:i + 1]).iloc[-1])
        sl = px - direction * sc.ATR_SL_MULT * a
        tp = px + direction * AUDIT_RR * abs(px - sl)
        won = None
        for _, future in m5.iloc[i + 1:i + 1 + horizon].iterrows():
            if direction > 0:
                if future["low"] <= sl: won = False; break
                if future["high"] >= tp: won = True; break
            else:
                if future["high"] >= sl: won = False; break
                if future["low"] <= tp: won = True; break
        outcome = "TIMEOUT" if won is None else "WIN" if won else "LOSS"
        rows.append({"t": ts, "dir": dirn, "grade": "RETEST", "n": 2,
                     "won": won, "outcome": outcome,
                     "r": 0.0 if won is None else AUDIT_RR if won else -1.0})
        pending = None
    return pd.DataFrame(rows)


def session_name(ts):
    hour = ts.hour
    if 0 <= hour < 7:
        return "Asia"
    if 7 <= hour < 13:
        return "London"
    if 13 <= hour < 16:
        return "London-NY overlap"
    if 16 <= hour < 21:
        return "New York"
    return "Off-hours"


def session_stats(df):
    print("session_breakdown:")
    tagged = df.copy()
    tagged["session"] = tagged["t"].map(session_name)
    for session, group in tagged.groupby("session"):
        outcomes = group["outcome"].value_counts()
        print(f"  {session}: n={len(group)} win={(group['outcome'] == 'WIN').mean()*100:.1f}% "
              f"exp_r={group['r'].mean():+.3f} WIN={outcomes.get('WIN', 0)} "
              f"LOSS={outcomes.get('LOSS', 0)} TIMEOUT={outcomes.get('TIMEOUT', 0)}")
        for direction, side in group.groupby("dir"):
            print(f"    {direction}: n={len(side)} exp_r={side['r'].mean():+.3f}")


def stats(df, name):
    if df.empty:
        print(f"{name}: tidak ada sinyal")
        return
    total = len(df)
    win = (df["outcome"] == "WIN").mean() * 100
    exp = df["r"].mean()
    outcomes = df["outcome"].value_counts()
    print(f"{name}: n={total} win={win:.1f}% exp_r={exp:+.3f} "
          f"WIN={outcomes.get('WIN', 0)} LOSS={outcomes.get('LOSS', 0)} "
          f"TIMEOUT={outcomes.get('TIMEOUT', 0)}")
    for d, g in df.groupby("dir"):
        if len(g):
            gout = g["outcome"].value_counts()
            print(f"  {d}: n={len(g)} win={(g['outcome'] == 'WIN').mean()*100:.1f}% "
                  f"exp_r={g['r'].mean():+.3f} TIMEOUT={gout.get('TIMEOUT', 0)}")
    # walk-forward per 10 hari
    days = df["t"].dt.floor("10D")
    periods = []
    for _, g in df.groupby(days):
        periods.append(((g["outcome"] == "WIN").mean() * 100, g["r"].mean()))
    if periods:
        w = [p[0] for p in periods]
        e = [p[1] for p in periods]
        print(f"  walkforward: {len(periods)} periode, "
              f"win={np.mean(w):.1f}% exp={np.mean(e):+.3f} "
              f"(min exp={min(e):+.3f}, max={max(e):+.3f})")


def self_check_pullback():
    idx = pd.date_range("2026-01-01", periods=80, freq="1h", tz="UTC")
    close = np.arange(80, dtype=float) + 100
    h1 = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1,
                       "close": close}, index=idx)
    mi = pd.date_range("2026-01-04", periods=5, freq="15min", tz="UTC")
    buy = pd.DataFrame({"open": [10, 9, 8, 7, 8], "high": [11, 10, 9, 8, 10],
                        "low": [9, 8, 7, 6, 7], "close": [10, 9, 8, 7, 9]}, index=mi)
    assert direction_pullback(h1, buy) == 1
    sell_h1 = h1.copy()
    sell_h1[["open", "high", "low", "close"]] = sell_h1[["open", "high", "low", "close"]].iloc[::-1].to_numpy()
    sell = buy.copy()
    sell[["open", "high", "low", "close"]] = 20 - sell[["open", "high", "low", "close"]]
    sell["high"], sell["low"] = 20 - buy["low"], 20 - buy["high"]
    assert direction_pullback(sell_h1, sell) == -1


def main():
    self_check_pullback()
    h1 = load("1h")
    m15 = load("15m")
    m5 = load("5m")
    print(f"bar: h1={len(h1)} m15={len(m15)} m5={len(m5)}")
    print(f"structure_lookback={STRUCTURE_LOOKBACK}")
    print(f"min_break_atr={MIN_BREAK_ATR}")
    print(f"momentum_mode={MOMENTUM_MODE}")
    print(f"rr={AUDIT_RR}")
    print(f"horizon_bars={HORIZON_BARS}")
    print(f"continuation_atr={CONTINUATION_ATR}")
    print(f"min_body_atr={MIN_BODY_ATR}")
    print(f"max_close_wick={MAX_CLOSE_WICK}")
    variants = {
        "baseline_h1_abs": lambda h, m: sc.trend_h1(h)[0],
        "m15_dir": lambda h, m: direction_m15(m),
        "m15_h1veto": lambda h, m: direction_m15_with_h1_veto(h, m),
        "struct_break": lambda h, m: direction_struct(h, m),
        "pullback_continuation": lambda h, m: direction_pullback(h, m),
    }
    for name, fn in variants.items():
        df = run(h1, m15, m5, fn)
        stats(df, name)
    fn = variants["struct_break"]
    stats(run(h1, m15, m5, fn, setup_dedup=float("inf")), "struct_break_strict_dedup")
    production = run(h1, m15, m5, fn, setup_dedup=CONTINUATION_ATR)
    stats(production, f"struct_break_continuation_{CONTINUATION_ATR:g}atr")
    session_stats(production)
    stats(run(h1, m15, m5, direction_struct_body, setup_dedup=CONTINUATION_ATR),
          f"struct_break_body_{MIN_BODY_ATR:g}atr")
    stats(run(h1, m15, m5, direction_struct_close_location,
              setup_dedup=CONTINUATION_ATR),
          f"struct_break_close_wick_{MAX_CLOSE_WICK:g}")
    stats(run_retest(h1, m15, m5), "struct_break_retest_30m")


if __name__ == "__main__":
    main()
