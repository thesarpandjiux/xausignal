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

CACHE = Path(__import__("os").environ.get("SCALP_AUDIT_CACHE", "/tmp/scalpcalib/cache"))


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
    """Structure-break M15: close tertinggi/terendah 200 bar ditembus + H1 regime."""
    c = m15["close"]
    history = c.iloc[-sc.STRUCTURE_LOOKBACK - 1:-1]
    if len(history) < sc.STRUCTURE_LOOKBACK:
        return 0
    range_high = float(history.max())
    range_low = float(history.min())
    px = float(c.iloc[-1])
    direction = 1 if px > range_high and direction_m15(m15) >= 0 else -1 if px < range_low and direction_m15(m15) <= 0 else 0
    extreme = h1_extreme(h1)
    return 0 if extreme and direction and direction != extreme else direction


def run(h1, m15, m5, direction_fn, horizon=12):
    rows = []
    last_by_direction = {}
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
        direction = direction_fn(h1s, m15s)
        if direction == 0:
            continue
        m5s = m5.iloc[:i + 1]
        trend_direction, trend_trigger = sc.trend_h1(h1s)
        momentum = sc.momentum_m15(m15s, direction)
        sweep = sc.liquidity_sweep(m5s, direction)
        # H1 tetap wajib sebagai regime gate, tetapi tidak wajib searah kecuali baseline.
        regime_ok = trend_direction != 0
        n = int(regime_ok) + int(momentum.passed) + int(sweep.passed)
        if n < sc.MIN_TRIGGERS:
            continue
        px = float(m5s["close"].iloc[-1])
        a = float(xs.atr(m5s).iloc[-1])
        sl = px - direction * sc.ATR_SL_MULT * a
        risk = abs(px - sl)
        tp1 = px + direction * sc.MIN_RR * risk
        dirn = "BUY" if direction > 0 else "SELL"
        if dirn in last_by_direction and ts - last_by_direction[dirn] < pd.Timedelta(minutes=45):
            continue
        last_by_direction[dirn] = ts
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
        if won is None:
            continue
        rows.append({"t": ts, "dir": dirn, "grade": {3: "A", 2: "B"}[n],
                     "n": n, "won": won, "r": 1.2 if won else -1.0})
    return pd.DataFrame(rows)


def stats(df, name):
    if df.empty:
        print(f"{name}: tidak ada sinyal")
        return
    total = len(df)
    win = df["won"].mean() * 100
    exp = df["r"].mean()
    print(f"{name}: n={total} win={win:.1f}% exp_r={exp:+.3f}")
    for d, g in df.groupby("dir"):
        if len(g):
            print(f"  {d}: n={len(g)} win={g['won'].mean()*100:.1f}% exp_r={g['r'].mean():+.3f}")
    # walk-forward per 10 hari
    days = df["t"].dt.floor("10D")
    periods = []
    for _, g in df.groupby(days):
        periods.append((g["won"].mean() * 100, g["r"].mean()))
    if periods:
        w = [p[0] for p in periods]
        e = [p[1] for p in periods]
        print(f"  walkforward: {len(periods)} periode, "
              f"win={np.mean(w):.1f}% exp={np.mean(e):+.3f} "
              f"(min exp={min(e):+.3f}, max={max(e):+.3f})")


def main():
    h1 = load("1h")
    m15 = load("15m")
    m5 = load("5m")
    print(f"bar: h1={len(h1)} m15={len(m15)} m5={len(m5)}")
    variants = {
        "baseline_h1_abs": lambda h, m: sc.trend_h1(h)[0],
        "m15_dir": lambda h, m: direction_m15(m),
        "m15_h1veto": lambda h, m: direction_m15_with_h1_veto(h, m),
        "struct_break": lambda h, m: direction_struct(h, m),
    }
    for name, fn in variants.items():
        df = run(h1, m15, m5, fn)
        stats(df, name)


if __name__ == "__main__":
    main()
