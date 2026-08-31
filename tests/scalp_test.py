#!/usr/bin/env python3
"""
Uji ringan xau_scalp.py — dijalankan manual: python tests/scalp_test.py
Tidak butuh jaringan (data sintetis), tidak ada framework.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
from datetime import datetime, timezone

import xau_scalp as sc
import datafeed


def test_rejects_stale_or_old_m5_feed():
    now = datetime.now(timezone.utc)
    idx = pd.date_range(end=now - pd.Timedelta(minutes=20), periods=100, freq="5min", tz="UTC")
    values = np.full(100, 4500.0)
    frame = pd.DataFrame({"open": values, "high": values + 1, "low": values - 1,
                          "close": values}, index=idx)
    stale = datafeed.Feed(frame, "cache", stale=True, age_s=1)
    live_but_old = datafeed.Feed(frame, "twelvedata", stale=False)
    try:
        sc.validate_live_feeds([stale], now)
        assert False, "stale feed harus ditolak"
    except RuntimeError:
        pass
    try:
        sc.validate_live_feeds([live_but_old], now)
        assert False, "candle M5 lama harus ditolak"
    except RuntimeError:
        pass
    ok("stale feed dan candle M5 >10 menit ditolak")


def mk(rows, freq="15min"):
    idx = pd.date_range("2026-01-01", periods=len(rows), freq=freq, tz="utc")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


def ok(label):
    print(f"  ✅ {label}")


def test_trend_and_full_pipeline():
    rng = np.random.default_rng(42)

    def make_df(n, freq, base=4600, trend_per_bar=0.3, noise=1.0):
        idx = pd.date_range("2026-01-01", periods=n, freq=freq, tz="utc")
        trend = np.arange(n) * trend_per_bar
        close = base + trend + rng.normal(0, noise, n)
        high = close + np.abs(rng.normal(0, noise * 0.6, n))
        low = close - np.abs(rng.normal(0, noise * 0.6, n))
        open_ = np.concatenate([[close[0]], close[:-1]])
        return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)

    h1 = make_df(200, "1h", trend_per_bar=1.5, noise=2.0)
    m15 = make_df(200, "15min", trend_per_bar=0.4, noise=1.0)
    m5 = make_df(200, "5min", trend_per_bar=0.15, noise=0.5)
    swing_low = float(m5["low"].tail(40).min())
    m5.iloc[-2, m5.columns.get_loc("low")] = swing_low - 1.0
    m5.iloc[-2, m5.columns.get_loc("close")] = swing_low + 0.5

    sig = sc.build_scalp_signal(h1, m15, m5, datetime.now(timezone.utc), data_source="test")
    assert sig.direction == "BUY", f"expected BUY, got {sig.direction}"
    assert sig.n_triggers >= sc.MIN_TRIGGERS
    names = {t.name for t in sig.triggers if t.passed}
    assert "Trend H1" in names
    assert "Liquidity Sweep M5" in names
    assert sig.stop_loss < sig.price
    assert all(tp > sig.price for tp in sig.targets)
    ok("tren naik + liquidity sweep -> BUY eligible, SL/TP arah benar")


def test_fvg_bullish():
    base = [[100, 101, 99, 100]] * 25
    rows = list(base) + [
        [100, 102, 99, 101],
        [101, 112, 100, 111],
        [111, 115, 105, 113],     # gap: low 105 > high candle i-1 (102)
        [113, 114, 103, 103.5],   # harga balik masuk gap 102-105
    ]
    t = sc.fair_value_gap(mk(rows), direction=1)
    assert t.passed
    ok("FVG bullish terdeteksi saat harga masuk gap belum-terisi")


def test_order_block_positive_and_negative():
    a = 1.0
    rows = [[100, 101, 99, 100]] * 20
    rows.append([100, 100.5, 97, 97.5])     # candle bearish -> jadi OB
    rows.append([97.5, 103, 97, 102.5])     # impuls bullish, body >= 1.5*a
    for k in range(12):                     # jarak drift menjauh
        rows.append([102.5 + k, 104 + k, 102 + k, 103.5 + k])
    rows_ok = rows + [[115, 116, 99.5, 100]]      # balik ke zona OB (97-100)
    rows_far = rows + [[115, 130, 114, 129]]       # jauh dari zona

    t_ok = sc.order_block(mk(rows_ok), direction=1, a=a)
    t_far = sc.order_block(mk(rows_far), direction=1, a=a)
    assert t_ok.passed, "harusnya terdeteksi saat harga kembali ke zona OB"
    assert not t_far.passed, "harus gagal saat harga jauh dari zona OB"
    ok("Order Block: positif (di zona) dan negatif (jauh dari zona) benar")


def test_backtest_returns_calibration_buckets():
    rng = np.random.default_rng(123)

    def make_df(n, freq, base=4600, trend_per_bar=0.1, noise=0.4):
        idx = pd.date_range("2026-01-01", periods=n, freq=freq, tz="utc")
        close = base + np.arange(n) * trend_per_bar + rng.normal(0, noise, n)
        high = close + 1
        low = close - 1
        open_ = np.concatenate([[close[0]], close[:-1]])
        return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)

    h1 = make_df(120, "1h", trend_per_bar=1.0, noise=0.2)
    m15 = make_df(480, "15min", trend_per_bar=0.25, noise=0.2)
    m5 = make_df(1440, "5min", trend_per_bar=0.08, noise=0.2)
    calib = sc.run_backtest(h1, m15, m5, horizon=6)
    assert "_meta" in calib
    assert calib["_meta"]["horizon_bars"] == 6
    assert calib["_meta"]["total_signals"] >= 0
    ok("backtest scalp menghasilkan metadata kalibrasi")


def test_no_trigger_when_trend_choppy():
    idx = pd.date_range("2026-01-01", periods=100, freq="1h", tz="utc")
    # Harga BENAR-BENAR konstan -> EMA20 == EMA50 == harga terus, deterministik
    # "tidak searah". Noise acak bisa kebetulan bentuk mikro-tren (perilaku
    # wajar EMA, bukan bug) makanya di sini dipakai data flat murni.
    flat = np.full(100, 4600.0)
    df = pd.DataFrame({"open": flat, "high": flat + 0.5, "low": flat - 0.5, "close": flat}, index=idx)
    direction, trig = sc.trend_h1(df)
    assert direction == 0, f"expected choppy (0), got {direction}"
    assert not trig.passed
    ok("harga flat -> direction 0, tidak paksa arah palsu")


if __name__ == "__main__":
    print("xau_scalp.py — uji trigger detection\n")
    test_rejects_stale_or_old_m5_feed()
    test_trend_and_full_pipeline()
    test_fvg_bullish()
    test_order_block_positive_and_negative()
    test_backtest_returns_calibration_buckets()
    test_no_trigger_when_trend_choppy()
    print("\n" + "─" * 50)
    print("✅ Semua uji lolos.")
