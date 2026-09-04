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


def trend_h1_var(h1, slope_bars=5):
    """Salinan sc.trend_h1 dengan slope lookback bisa divariasikan."""
    c = h1["close"]
    e20, e50 = xs.ema(c, 20), xs.ema(c, 50)
    slope_up = e20.iloc[-1] > e20.iloc[-1 - slope_bars]
    if e20.iloc[-1] > e50.iloc[-1] and slope_up:
        return 1, True
    if e20.iloc[-1] < e50.iloc[-1] and not slope_up:
        return -1, True
    return 0, False


def direction_struct_h1_aligned(h1, m15):
    """Structure break M15 TAPI wajib searah trend H1 penuh (bukan hanya
    veto ekstrem 0.5 ATR): BUY hanya saat H1 uptrend, SELL hanya saat H1
    downtrend. Menguji hipotesis: sinyal melawan H1 (mis. SELL di bottom
    4306 saat H1 masih downtrend) adalah sumber rugi reversal."""
    direction = direction_struct(h1, m15)
    if not direction:
        return 0
    trend, _ = trend_h1_var(h1, 5)
    return direction if trend == direction else 0


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


def direction_struct_noveto(h1, m15):
    """Structure break M15 TANPA veto H1 ekstrem — counterfactual utk mengukur
    berapa sinyal diblokir veto (gap EMA H1 >0.5 ATR melawan arah) dan hasilnya
    kalau dieksekusi. Sama persis dgn direction_struct minus dua baris veto."""
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
    return direction


def h1_extension_atr(h1) -> float:
    """Jarak close H1 terakhir vs EMA20 H1 dalam satuan ATR H1.
    Positif = harga di atas EMA20 (naik), negatif = di bawah (turun)."""
    c = h1["close"]
    e20 = xs.ema(c, 20)
    a = float(xs.atr(h1).iloc[-1])
    return ((float(c.iloc[-1]) - float(e20.iloc[-1])) / a) if a else 0.0


def direction_struct_no_extreme(h1, m15, max_ext=2.0):
    """Structure break M15, TAPI tolak bila harga teregang ekstrem dari EMA20
    H1 (mean-reversion risk): SELL ditolak bila close > max_ext*ATR di atas
    EMA20? BUKAN — SELL di bottom artinya harga JAUH DI BAWAH EMA20.
    Logika: SELL (short) berisiko saat harga sudah JAUH DI BAWAH EMA20 (fall
    ekstrem, siap rebound) → tolak bila extension < -max_ext.
    BUY berisiko saat harga sudah jauh DI ATAS EMA20 (rally ekstrem) →
    tolak bila extension > +max_ext."""
    direction = direction_struct(h1, m15)
    if not direction:
        return 0
    ext = h1_extension_atr(h1)
    if direction > 0 and ext > max_ext:
        return 0  # BUY di puncak teregang
    if direction < 0 and ext < -max_ext:
        return 0  # SELL di dasar teregang
    return direction


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


def direction_fast_reversal(h1, m15):
    """Reversal awal: struktur 3-candle berubah + RSI cross 50 + MACD searah."""
    if len(m15) < 20:
        return 0
    trend = sc.trend_h1(h1)[0]
    c, low, high = m15["close"], m15["low"], m15["high"]
    r = xs.rsi(c)
    _, _, hist = xs.macd(c)
    buy = (trend != 1 and low.iloc[-2] > low.iloc[-3]
           and c.iloc[-1] > high.iloc[-4:-1].max()
           and r.iloc[-2] <= 50 < r.iloc[-1]
           and hist.iloc[-1] > hist.iloc[-2])
    sell = (trend != -1 and high.iloc[-2] < high.iloc[-3]
            and c.iloc[-1] < low.iloc[-4:-1].min()
            and r.iloc[-2] >= 50 > r.iloc[-1]
            and hist.iloc[-1] < hist.iloc[-2])
    return 1 if buy else -1 if sell else 0


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
    c = m15["close"]
    rsi = float(xs.rsi(c).iloc[-1])
    _, _, hist = xs.macd(c)
    if MOMENTUM_MODE == "rsi_extreme_macd":
        macd_up = float(hist.iloc[-1]) > float(hist.iloc[-4])
        return (rsi < 30 and macd_up) if direction > 0 else (rsi > 80 and not macd_up)
    if MOMENTUM_MODE == "rsi_30_80_macd":
        macd_up = float(hist.iloc[-1]) > float(hist.iloc[-4])
        return (rsi > 30 and macd_up) if direction > 0 else (rsi < 80 and not macd_up)
    if MOMENTUM_MODE == "hist_level":
        # Momentum hidup: RSI searah DAN histogram MACD masih di sisi yang benar,
        # tanpa syarat slope 3-candle yang menolak rally melambat.
        return (rsi > 50 and float(hist.iloc[-1]) > 0) if direction > 0 \
            else (rsi < 50 and float(hist.iloc[-1]) < 0)
    if MOMENTUM_MODE == "hist_slope1":
        up = float(hist.iloc[-1]) > float(hist.iloc[-2])
        return (rsi > 50 and up) if direction > 0 else (rsi < 50 and not up)
    if MOMENTUM_MODE == "rsi_strength":
        # RSI saja, tanpa MACD: ambang lebih tegas dari 50.
        return rsi > 55 if direction > 0 else rsi < 45
    return sc.momentum_m15(m15, direction).passed


def momentum_m5_passed(m5s: pd.DataFrame, direction: int) -> bool:
    """Momentum ala M15 (RSI>50 + hist MACD naik vs 3 bar lalu) tapi dihitung
    pada deret close M5 yang SEMUA closed — resolusi 5 menit, tidak menunggu
    candle M15 berikutnya tutup untuk mendapat nilai indikator baru."""
    c = m5s["close"]
    r = float(xs.rsi(c).iloc[-1])
    _, _, hist = xs.macd(c)
    h_now, h_prev = float(hist.iloc[-1]), float(hist.iloc[-4])
    macd_up = h_now > h_prev
    rsi_ok = (r > 50) if direction > 0 else (r < 50)
    return rsi_ok and (macd_up if direction > 0 else not macd_up)


def run(h1, m15, m5, direction_fn, horizon=HORIZON_BARS, setup_dedup=None,
        momentum_on="m15", trend_fn=None):
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
        trend_direction, trend_trigger = (trend_fn(h1s) if trend_fn
                                          else sc.trend_h1(h1s))
        momentum_ok = (momentum_m5_passed(m5s, direction) if momentum_on == "m5"
                       else momentum_passed(m15s, direction))
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
                     "n": n, "won": won, "outcome": outcome, "r": r_result,
                     "px": px})
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


def direction_intrabar_struct(m15s, px, m15_full, n_closed):
    """Structure break intrabar: px (close M5 terakhir) dibandingkan 20 close
    M15 yang SUDAH tutup sebelum candle berjalan. Memakai data nyata sampai ts,
    tanpa look-ahead — close parsial candle berjalan = harga live saat ts."""
    if n_closed < 21:
        return 0
    history = m15s["close"].iloc[-20:]
    hi = float(history.max())
    lo = float(history.min())
    if px > hi:
        return 1
    if px < lo:
        return -1
    return 0


def run_intrabar(h1, m15, m5, horizon=HORIZON_BARS, setup_dedup=CONTINUATION_ATR,
                 min_age_min=5):
    """Varian timing: sinyal bisa muncul saat candle M15 masih berjalan (tiap
    close M5), bukan menunggu candle M15 tutup. Tanpa look-ahead: close parsial
    candle berjalan = harga live; momentum/RSI/MACD dihitung ulang pada deret
    yang memuat close parsial itu (sama seperti indikator platform realtime)."""
    rows = []
    last_by_direction = {}
    active_by_direction = {}
    for i in range(120, len(m5) - horizon):
        ts = m5.index[i] + pd.Timedelta(minutes=5)
        h1s = h1[h1.index + pd.Timedelta(hours=1) <= ts]
        m15s = m15[m15.index + pd.Timedelta(minutes=15) <= ts]
        if len(h1s) < 60 or len(m15s) < 21:
            continue
        n_closed = len(m15s)
        if n_closed >= len(m15):
            continue  # tidak ada candle berjalan
        forming_start = m15.index[n_closed]
        if not (forming_start < ts < forming_start + pd.Timedelta(minutes=15)):
            continue
        age = (ts - forming_start).total_seconds() / 60
        if age < min_age_min:
            continue
        px = float(m5["close"].iloc[i])
        direction = direction_intrabar_struct(m15s, px, m15, n_closed)
        if direction == 0:
            continue
        dirn = "BUY" if direction > 0 else "SELL"
        level = float(m15s["close"].iloc[-20:].max() if direction > 0
                      else m15s["close"].iloc[-20:].min())
        active = active_by_direction.get(dirn)
        if setup_dedup is not None and active is not None:
            atr_m15 = float(xs.atr(m15s).iloc[-1])
            advanced = np.isinf(setup_dedup) or (
                level >= active + setup_dedup * atr_m15 if direction > 0
                else level <= active - setup_dedup * atr_m15)
            if not advanced:
                continue
        # deret momentum = close M15 closed + close parsial candle berjalan
        ext = pd.concat([m15s["close"], pd.Series([px])]).reset_index(drop=True)
        trend_direction, _ = sc.trend_h1(h1s)
        momentum_ok = momentum_series_passed(ext, direction)
        sweep = sc.liquidity_sweep(m5.iloc[:i + 1], direction)
        regime_ok = trend_direction != 0
        n = int(regime_ok) + int(momentum_ok) + int(sweep.passed)
        if n < sc.MIN_TRIGGERS:
            continue
        a = float(xs.atr(m5.iloc[:i + 1]).iloc[-1])
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
                     "n": n, "won": won, "outcome": outcome, "r": r_result,
                     "age_min": age})
    return pd.DataFrame(rows)


def momentum_series_passed(closes: pd.Series, direction: int) -> bool:
    """Sama dengan momentum_m15, tapi bekerja pada deret close arbitrer
    (closed + parsial) sehingga indikator mencerminkan nilai realtime."""
    r = float(xs.rsi(closes).iloc[-1])
    _, _, hist = xs.macd(closes)
    h_now, h_prev = float(hist.iloc[-1]), float(hist.iloc[-4])
    macd_up = h_now > h_prev
    rsi_ok = (r > 50) if direction > 0 else (r < 50)
    return rsi_ok and (macd_up if direction > 0 else not macd_up)


def direction_early_m5(h1, m15, m5):
    """Early momentum: M15 RSI+MACD searah dan M5 break 6-candle; H1 ekstrem memveto."""
    if len(m5) < 40 or len(m15) < 20:
        return 0
    extreme = h1_extreme(h1)
    buy = (momentum_passed(m15, 1) and not extreme == -1
           and float(m5["close"].iloc[-1]) > float(m5["high"].iloc[-7:-1].max()))
    sell = (momentum_passed(m15, -1) and not extreme == 1
            and float(m5["close"].iloc[-1]) < float(m5["low"].iloc[-7:-1].min()))
    return 1 if buy else -1 if sell else 0


def run_early(h1, m15, m5, horizon=HORIZON_BARS):
    """Early momentum memakai level M5; dedup bila level maju 0.75 ATR M15."""
    rows, last_by_direction, active_by_direction = [], {}, {}
    for i in range(120, len(m5) - horizon):
        ts = m5.index[i] + pd.Timedelta(minutes=5)
        h1s = h1[h1.index + pd.Timedelta(hours=1) <= ts]
        m15s = m15[m15.index + pd.Timedelta(minutes=15) <= ts]
        m5s = m5.iloc[:i + 1]
        if len(h1s) < 60 or len(m15s) < 60:
            continue
        direction = direction_early_m5(h1s, m15s, m5s)
        if not direction:
            continue
        dirn = "BUY" if direction > 0 else "SELL"
        level = float(m5s["high"].iloc[-7:-1].max() if direction > 0
                      else m5s["low"].iloc[-7:-1].min())
        active = active_by_direction.get(dirn)
        if active is not None:
            atr_m15 = float(xs.atr(m15s).iloc[-1])
            advanced = (level >= active + 0.75 * atr_m15 if direction > 0
                        else level <= active - 0.75 * atr_m15)
            if not advanced:
                continue
        if dirn in last_by_direction and ts - last_by_direction[dirn] < pd.Timedelta(minutes=45):
            continue
        last_by_direction[dirn] = ts
        active_by_direction[dirn] = level
        px = float(m5s["close"].iloc[-1])
        a = float(xs.atr(m5s).iloc[-1])
        sl = px - direction * sc.ATR_SL_MULT * a
        tp = px + direction * AUDIT_RR * abs(px - sl)
        won = None
        for _, bar in m5.iloc[i + 1:i + 1 + horizon].iterrows():
            if direction > 0:
                if bar["low"] <= sl: won = False; break
                if bar["high"] >= tp: won = True; break
            else:
                if bar["high"] >= sl: won = False; break
                if bar["low"] <= tp: won = True; break
        outcome = "TIMEOUT" if won is None else "WIN" if won else "LOSS"
        rows.append({"t": ts, "dir": dirn, "grade": "EARLY", "n": 2,
                     "won": won, "outcome": outcome,
                     "r": 0.0 if won is None else AUDIT_RR if won else -1.0})
    return pd.DataFrame(rows)


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


def self_check_fast_reversal():
    rng = np.random.default_rng(0)
    close = 100 + np.cumsum(rng.normal(-0.1, 1, 40))
    close[-3:] = [close[-4] - 2, close[-4] - 1, close[-4] + 4]
    idx = pd.date_range("2026-01-01", periods=40, freq="15min", tz="UTC")
    m15 = pd.DataFrame({"open": np.r_[close[0], close[:-1]], "high": close + .5,
                        "low": close - .5, "close": close}, index=idx)
    hi = pd.date_range("2025-12-20", periods=100, freq="1h", tz="UTC")
    hc = np.arange(100, 0, -1, dtype=float)
    h1 = pd.DataFrame({"open": hc, "high": hc + 1, "low": hc - 1,
                       "close": hc}, index=hi)
    assert direction_fast_reversal(h1, m15) == 1
    mirrored = m15.copy()
    mirrored[["open", "high", "low", "close"]] = -m15[["open", "low", "high", "close"]].to_numpy()
    mirrored_h1 = h1.copy()
    mirrored_h1[["open", "high", "low", "close"]] = -h1[["open", "low", "high", "close"]].to_numpy()
    assert direction_fast_reversal(mirrored_h1, mirrored) == -1


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
    self_check_fast_reversal()
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
        "struct_break_noveto": lambda h, m: direction_struct_noveto(h, m),
        "pullback_continuation": lambda h, m: direction_pullback(h, m),
        "fast_reversal": lambda h, m: direction_fast_reversal(h, m),
    }
    for name, fn in variants.items():
        df = run(h1, m15, m5, fn)
        stats(df, name)
    fn = variants["struct_break"]
    stats(run(h1, m15, m5, fn, setup_dedup=float("inf")), "struct_break_strict_dedup")
    production = run(h1, m15, m5, fn, setup_dedup=CONTINUATION_ATR)
    stats(production, f"struct_break_continuation_{CONTINUATION_ATR:g}atr")
    stats(production[production["grade"] == "A"], "production_grade_a")
    stats(production[production["grade"] == "B"], "production_grade_b")

    # ── Counterfactual veto H1 ekstrem ─────────────────────────────────────
    # Sinyal yang muncul di run no-veto TAPI hilang di run veto (karena gap
    # EMA H1 >0.5 ATR melawan arah) = sinyal yang diblokir veto. Ukur hasilnya
    # kalau dieksekusi: berapa yang sebenarnya WIN (veto blokir bagus) vs LOSS
    # (veto melindungi dari rugi).
    fn_noveto = variants["struct_break_noveto"]
    run_veto = run(h1, m15, m5, fn, setup_dedup=CONTINUATION_ATR)
    run_noveto = run(h1, m15, m5, fn_noveto, setup_dedup=CONTINUATION_ATR)
    veto_keys = set(run_veto[["t", "dir", "px"]].round(3).itertuples(index=False))
    blocked = run_noveto[
        ~run_noveto.apply(
            lambda r: (r["t"], r["dir"], round(r["px"], 3)) in veto_keys, axis=1)
    ]
    print("\n── counterfactual veto H1 ekstrem (>0.5 ATR melawan arah) ──")
    print(f"run dengan veto:    n={len(run_veto)} exp={run_veto['r'].mean():+.3f}")
    print(f"run tanpa veto:     n={len(run_noveto)} exp={run_noveto['r'].mean():+.3f}")
    print(f"sinyal DIBLOKIR veto: n={len(blocked)} "
          f"exp={blocked['r'].mean():+.3f} (hasil kalau dieksekusi)")
    if len(blocked):
        bout = blocked["outcome"].value_counts()
        print(f"  WIN={bout.get('WIN', 0)} LOSS={bout.get('LOSS', 0)} "
              f"TIMEOUT={bout.get('TIMEOUT', 0)}")
        for d, g in blocked.groupby("dir"):
            gout = g["outcome"].value_counts()
            print(f"  {d}: n={len(g)} exp={g['r'].mean():+.3f} "
                  f"WIN={gout.get('WIN', 0)} LOSS={gout.get('LOSS', 0)}")

    stats(run(h1, m15, m5, fn, setup_dedup=CONTINUATION_ATR,
              momentum_on="m5"), "struct_break_momentum_on_m5")
    # Dump sinyal pada jendela reversal 4306->4500 (2-4 Sep) utk banding
    # timing entry baseline vs slope3 vs h1_aligned
    dump_signal_window = ("2026-09-02", "2026-09-04")
    for label, kwargs in [
        ("base", {}),
        ("slope3", {"trend_fn": lambda h: trend_h1_var(h, 3)}),
        ("h1align", {"direction_fn": direction_struct_h1_aligned}),
        ("noext2", {"direction_fn": lambda h, m: direction_struct_no_extreme(h, m, 2.0)}),
        ("noext1.5", {"direction_fn": lambda h, m: direction_struct_no_extreme(h, m, 1.5)}),
    ]:
        df = run(h1, m15, m5, kwargs.pop("direction_fn", fn),
                 setup_dedup=CONTINUATION_ATR, **kwargs)
        win = df[(df["t"] >= dump_signal_window[0])
                 & (df["t"] < dump_signal_window[1])]
        print(f"signal_window_{label}: {len(win)} sinyal 2-4 Sep")
        for _, s in win.iterrows():
            print(f"  {s['t']:%m-%d %H:%M} {s['dir']:4} {s['outcome']:7} "
                  f"r={s['r']:+.1f} px={s['px']:.2f}")
    stats(run(h1, m15, m5, fn, setup_dedup=CONTINUATION_ATR,
              trend_fn=lambda h: trend_h1_var(h, 3)),
          "struct_break_trend_slope3")
    stats(run(h1, m15, m5, direction_struct_h1_aligned,
              setup_dedup=CONTINUATION_ATR),
          "struct_break_h1_aligned")
    stats(run(h1, m15, m5, lambda h, m: direction_struct_no_extreme(h, m, 2.0),
              setup_dedup=CONTINUATION_ATR),
          "struct_break_no_extreme_2atr")
    stats(run(h1, m15, m5, lambda h, m: direction_struct_no_extreme(h, m, 1.5),
              setup_dedup=CONTINUATION_ATR),
          "struct_break_no_extreme_1.5atr")
    session_stats(production)
    sessions = production["t"].map(session_name)
    stats(production[~((sessions == "Asia") & (production["dir"] == "SELL"))],
          "production_without_asia_sell")
    stats(run(h1, m15, m5, direction_struct_body, setup_dedup=CONTINUATION_ATR),
          f"struct_break_body_{MIN_BODY_ATR:g}atr")
    stats(run(h1, m15, m5, direction_struct_close_location,
              setup_dedup=CONTINUATION_ATR),
          f"struct_break_close_wick_{MAX_CLOSE_WICK:g}")
    stats(run_retest(h1, m15, m5), "struct_break_retest_30m")
    stats(run_early(h1, m15, m5), "early_momentum_m5")


if __name__ == "__main__":
    main()
