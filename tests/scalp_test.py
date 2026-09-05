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


def test_structure_direction_is_symmetric():
    idx15 = pd.date_range("2026-01-01", periods=80, freq="15min", tz="UTC")
    base = np.linspace(100, 104, 80)
    up = pd.DataFrame({"open": base, "high": base + 1, "low": base - 1,
                       "close": base}, index=idx15)
    up.iloc[-1, up.columns.get_loc("close")] = float(up["close"].iloc[-21:-1].max()) + 2
    down = up.copy()
    down["close"] = np.linspace(104, 100, 80)
    down.iloc[-1, down.columns.get_loc("close")] = float(down["close"].iloc[-21:-1].min()) - 2

    idx1h = pd.date_range("2025-12-20", periods=200, freq="1h", tz="UTC")
    flat = np.full(200, 102.0)
    h1 = pd.DataFrame({"open": flat, "high": flat + 1, "low": flat - 1,
                       "close": flat}, index=idx1h)
    assert sc.structure_direction(h1, up)[0] == 1
    assert sc.structure_direction(h1, down)[0] == -1
    ok("structure break menilai BUY dan SELL secara simetris")


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
    breakout = float(m15["close"].iloc[-21:-1].max()) + 3.0
    m15.iloc[-1, m15.columns.get_loc("close")] = breakout
    m15.iloc[-1, m15.columns.get_loc("high")] = breakout + 0.5
    m5 = make_df(200, "5min", trend_per_bar=0.15, noise=0.5)
    swing_low = float(m5["low"].tail(40).min())
    m5.iloc[-2, m5.columns.get_loc("low")] = swing_low - 1.0
    m5.iloc[-2, m5.columns.get_loc("close")] = swing_low + 0.5

    before = sc.build_scalp_signal(h1, m15, m5, datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc), data_source="test")
    telemetry = sc.gate_telemetry(h1, m15, m5)
    sig = sc.build_scalp_signal(h1, m15, m5, datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc), data_source="test")
    assert sig.direction == before.direction and sig.n_triggers == before.n_triggers
    assert "structure=" in telemetry and "momentum_buy=" in telemetry and "sweep_sell=" in telemetry
    assert sig.direction == "BUY", f"expected BUY, got {sig.direction}"
    assert sig.n_triggers >= sc.MIN_TRIGGERS
    names = {t.name for t in sig.triggers if t.passed}
    assert "Structure Break M15" in names
    assert "Liquidity Sweep M5" in names
    assert sig.stop_loss < sig.price
    assert all(tp > sig.price for tp in sig.targets)
    assert sig.rr[0] == 2.0
    assert abs((sig.targets[0] - sig.price) / (sig.price - sig.stop_loss) - 2.0) < 1e-9
    ok("tren naik + liquidity sweep -> BUY eligible, SL/TP arah dan RR 1:2 benar")


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


def test_setup_dedup_persists_until_range_reset():
    now = datetime.now(timezone.utc)
    sig = sc.ScalpSignal(time=now, direction="BUY", grade="B", n_triggers=2,
                         price=100, atr=1, setup_id="BUY:20260101T1200:99.50")
    state = {"active_setup": {"id": sig.setup_id, "direction": "BUY",
                              "level": 99.5},
             "last": {"time": (now - pd.Timedelta(hours=2)).isoformat(),
                      "direction": "BUY", "price": 99}}
    allowed, reason = sc.should_send(sig, state, now)
    assert not allowed and "setup" in reason

    closes = np.full(30, 99.0)
    idx = pd.date_range("2026-01-01", periods=30, freq="15min", tz="UTC")
    m15 = pd.DataFrame({"open": closes, "high": closes + 1, "low": closes - 1,
                        "close": closes}, index=idx)
    sc.reset_setup_if_inside_range(state, m15)
    assert "active_setup" not in state
    allowed, _ = sc.should_send(sig, state, now)
    assert allowed
    ok("setup sama diblokir sampai harga kembali ke dalam range M15")


def test_setup_continuation_after_075_atr():
    now = datetime.now(timezone.utc)
    state = {"active_setup": {"id": "old", "direction": "SELL", "level": 100},
             "last": {"time": (now - pd.Timedelta(hours=2)).isoformat(),
                      "direction": "SELL", "price": 99}}
    weak = sc.ScalpSignal(time=now, direction="SELL", grade="B", n_triggers=2,
                          breakout_level=99.3, breakout_atr=1.0)
    strong = sc.ScalpSignal(time=now, direction="SELL", grade="B", n_triggers=2,
                            breakout_level=99.25, breakout_atr=1.0)
    assert not sc.should_send(weak, state, now)[0]
    assert sc.should_send(strong, state, now)[0]

    state["active_setup"].update(direction="BUY", level=100)
    strong.direction, strong.breakout_level = "BUY", 100.75
    assert sc.should_send(strong, state, now)[0]
    ok("continuation BUY/SELL lolos setelah level maju minimal 0.75 ATR M15")


def test_shadow_logger_deduplicates_without_marking_sent():
    import csv
    import tempfile
    old_base, old_log = sc.BASE, sc.SHADOW_LOG_FILE
    try:
        sc.BASE = Path(tempfile.mkdtemp())
        sc.SHADOW_LOG_FILE = sc.BASE / "signals_shadow.csv"
        now = datetime.now(timezone.utc)
        sig = sc.ScalpSignal(time=now, direction="BUY", grade="B", n_triggers=2,
                             price=100, stop_loss=99, targets=[102, 103], rr=[2, 3],
                             setup_id="BUY:test:100", data_source="twelvedata")
        sc.log_shadow_signal(sig)
        sc.log_shadow_signal(sig)
        with sc.SHADOW_LOG_FILE.open() as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1 and rows[0]["sent"] == "False"
        assert rows[0]["trigger"] == "shadow"
        ok("shadow setup dicatat sekali tanpa menandai Telegram terkirim")
    finally:
        sc.BASE, sc.SHADOW_LOG_FILE = old_base, old_log


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


def test_news_window_phases():
    from datetime import timedelta
    now = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
    ev = lambda m: [{"impact": "High", "country": "USD", "time": now + timedelta(minutes=m),
                     "title": "Non-Farm Employment Change"}]
    assert sc.news_window(ev(29), now)[0] == "blackout"      # 29 mnt sebelum
    assert sc.news_window(ev(45), now)[0] == "blackout"      # 45 mnt sebelum (NEWS_BLOCK_BEFORE_MIN=60)
    assert sc.news_window(ev(70), now)[0] == "none"          # 70 mnt sebelum → di luar blackout
    assert sc.news_window(ev(-5), now)[0] == "quiet"          # 5 mnt sesudah
    assert sc.news_window(ev(-20), now)[0] == "aggressive"    # 20 mnt sesudah
    assert sc.news_window(ev(-120), now)[0] == "none"         # jauh sesudah
    assert sc.news_window(ev(120), now)[0] == "none"          # jauh sebelum
    ok("fase news: blackout/quiet/aggressive/none benar")


def test_news_alert_due_once():
    from datetime import timedelta
    now = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
    e = {"impact": "High", "country": "USD", "time": now + timedelta(minutes=20),
         "title": "NFP"}
    due, ev = sc.news_alert_due([e], now, {})
    assert due and ev is not None
    key = f"alerted_{e['time'].isoformat()}"
    due2, _ = sc.news_alert_due([e], now, {key: True})
    assert not due2
    ok("alert countdown news hanya sekali per event")


def test_momentum_m5_closed_matches_m15_on_clean_trend():
    """Pada tren naik yang menguat, momentum M5 closed = BUY."""
    idx = pd.date_range("2026-01-01", periods=300, freq="5min", tz="utc")
    x = np.arange(300)
    close = 4600 + 10 * np.exp(x / 120)               # akselerasi → hist MACD naik
    m5 = pd.DataFrame({"open": close - 0.2, "high": close + 0.5,
                       "low": close - 0.5, "close": close}, index=idx)
    assert sc.momentum_m5_closed(m5, 1).passed
    assert not sc.momentum_m5_closed(m5, -1).passed
    ok("momentum M5 closed searah tren menguat, bukan searah turun")


def test_structure_break_via_m5_news_mode():
    """Mode news (m5 diberikan): M15 belum break 20 candle, tapi close M5
    terakhir sudah menembus level M15 → structure break terdeteksi.
    Mode normal (m5=None): tetap nunggu candle M15 tutup → NO-TRADE."""
    idx15 = pd.date_range("2026-01-01", periods=60, freq="15min", tz="utc")
    # M15: harga konsolidasi sempit, belum break mana-mana
    flat = np.full(60, 4500.0)
    m15 = pd.DataFrame({"open": flat - 0.2, "high": flat + 2,
                        "low": flat - 2, "close": flat + 1}, index=idx15)
    # M5: bar terakhir menembus ATAS range M15 (4500+1+2 → close 4510)
    idx5 = pd.date_range("2026-01-01", periods=180, freq="5min", tz="utc")
    up = np.full(180, 4500.0)
    up[-1] = 4510.0
    m5 = pd.DataFrame({"open": up - 0.2, "high": up + 1,
                       "low": up - 1, "close": up}, index=idx5)
    idxh = pd.date_range("2026-01-01", periods=60, freq="1h", tz="utc")
    h1 = pd.DataFrame({"open": flat - 1, "high": flat + 3,
                       "low": flat - 3, "close": flat + 1}, index=idxh)
    # H1 flat → tidak ada veto ekstrem
    d_normal, _ = sc.structure_direction(h1, m15, None)
    d_news, trig = sc.structure_direction(h1, m15, m5)
    assert d_normal == 0, "mode normal harus tetap nunggu M15 close"
    assert d_news == 1, f"mode news harus deteksi break via M5, dapat {d_news}"
    assert trig.passed
    ok("structure break via M5 hanya aktif di mode news, normal tetap nunggu M15")


def test_h1_veto_asymmetric():
    """Veto H1 ekstrem asimetris (bukti 5000 bar, continuation 0.75 ATR):
    - SELL melawan gap H1 positif ekstrem (extreme=+1) → TETAP diveto (-0.500R).
    - BUY melawan gap H1 negatif ekstrem (extreme=-1) → DIBUKA (+0.529R).
    - Mode news (m5 diberikan): veto dilonggarkan penuh (2 arah)."""
    # H1 uptrend ekstrem: EMA20 jauh di atas EMA50 (+1.5 ATR)
    idxh = pd.date_range("2026-01-01", periods=80, freq="1h", tz="utc")
    up = 4500 + np.arange(80) * 3.0                      # naik konsisten
    h1_up = pd.DataFrame({"open": up - 1, "high": up + 2,
                          "low": up - 2, "close": up}, index=idxh)
    # H1 downtrend ekstrem: EMA20 jauh di bawah EMA50 (-1.5 ATR)
    down = 4500 - np.arange(80) * 3.0
    h1_dn = pd.DataFrame({"open": down + 1, "high": down + 2,
                          "low": down - 2, "close": down}, index=idxh)
    # M15: break BAWAH — 21 bar pertama datar, bar terakhir tembus ke bawah
    idx15 = pd.date_range("2026-01-01", periods=60, freq="15min", tz="utc")
    flat = np.full(60, 4500.0)
    dn15 = flat.copy()
    dn15[-1] = 4496.0
    m15_dn = pd.DataFrame({"open": dn15 + 0.2, "high": dn15 + 2,
                           "low": dn15 - 2, "close": dn15}, index=idx15)
    # M15: break ATAS — 21 bar pertama datar, bar terakhir tembus ke atas
    up15 = np.full(60, 4500.0)
    up15[-1] = 4504.0
    m15_up = pd.DataFrame({"open": up15 - 0.2, "high": up15 + 2,
                           "low": up15 - 2, "close": up15}, index=idx15)
    # SELL vs H1 uptrend ekstrem → diveto (mode normal)
    d, trig = sc.structure_direction(h1_up, m15_dn, None)
    assert d == 0 and not trig.passed, \
        f"SELL vs H1 up-ekstrem harus diveto, dapat {d} {trig.note}"
    # BUY vs H1 downtrend ekstrem → DIBUKA (mode normal)
    d2, _ = sc.structure_direction(h1_dn, m15_up, None)
    assert d2 == 1, f"BUY vs H1 dn-ekstrem harus dibuka, dapat {d2}"
    # Mode news (m5 diberikan): SELL vs H1 up-ekstrem → DIBUKA
    idx5 = pd.date_range("2026-01-01", periods=180, freq="5min", tz="utc")
    m5_dn = pd.DataFrame({"open": np.full(180, 4497.0),
                          "high": np.full(180, 4499.0),
                          "low": np.full(180, 4496.0),
                          "close": np.full(180, 4497.0)}, index=idx5)
    d3, _ = sc.structure_direction(h1_up, m15_dn, m5_dn)
    assert d3 == -1, f"mode news: SELL vs H1 up-ekstrem harus dibuka, dapat {d3}"
    ok("veto H1 asimetris: SELL vs gap+ tetap diveto, BUY vs gap- dibuka, "
       "mode news bebas veto 2 arah")


def test_asia_session_gate():
    """Gate Asia: jam 0-6 UTC semua NO-TRADE, jam London normal."""
    rng = np.random.default_rng(7)
    def make_df(n, freq, base=4600, trend_per_bar=0.3, noise=1.0):
        idx = pd.date_range("2026-01-01", periods=n, freq=freq, tz="utc")
        trend = np.arange(n) * trend_per_bar
        close = base + trend + rng.normal(0, noise, n)
        high = close + np.abs(rng.normal(0, noise * 0.6, n))
        low = close - np.abs(rng.normal(0, noise * 0.6, n))
        open_ = np.concatenate([[close[0]], close[:-1]])
        return pd.DataFrame({"open": open_, "high": high, "low": low,
                             "close": close}, index=idx)
    h1 = make_df(200, "1h", trend_per_bar=1.5, noise=2.0)
    m15 = make_df(200, "15min", trend_per_bar=0.4, noise=1.0)
    breakout = float(m15["close"].iloc[-21:-1].max()) + 3.0
    m15.iloc[-1, m15.columns.get_loc("close")] = breakout
    m15.iloc[-1, m15.columns.get_loc("high")] = breakout + 0.5
    m5 = make_df(200, "5min", trend_per_bar=0.15, noise=0.5)
    swing_low = float(m5["low"].tail(40).min())
    m5.iloc[-2, m5.columns.get_loc("low")] = swing_low - 1.0
    m5.iloc[-2, m5.columns.get_loc("close")] = swing_low + 0.5

    asia = datetime(2026, 9, 4, 3, 0, tzinfo=timezone.utc)   # jam 3 UTC
    london = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)  # jam 12 UTC
    sig_asia = sc.build_scalp_signal(h1, m15, m5, asia, data_source="test")
    sig_ldn = sc.build_scalp_signal(h1, m15, m5, london, data_source="test")
    assert sig_asia.direction == "NO-TRADE", \
        f"Asia harus NO-TRADE, dapat {sig_asia.direction}"
    assert sig_ldn.direction == "BUY", \
        f"London harus BUY (setup sama), dapat {sig_ldn.direction}"
    ok("session gate: Asia 0-6 UTC diblokir, London diizinkan")


if __name__ == "__main__":
    print("xau_scalp.py — uji trigger detection\n")
    test_rejects_stale_or_old_m5_feed()
    test_structure_direction_is_symmetric()
    test_trend_and_full_pipeline()
    test_fvg_bullish()
    test_order_block_positive_and_negative()
    test_setup_dedup_persists_until_range_reset()
    test_setup_continuation_after_075_atr()
    test_shadow_logger_deduplicates_without_marking_sent()
    test_backtest_returns_calibration_buckets()
    test_no_trigger_when_trend_choppy()
    test_news_window_phases()
    test_news_alert_due_once()
    test_momentum_m5_closed_matches_m15_on_clean_trend()
    test_structure_break_via_m5_news_mode()
    test_h1_veto_asymmetric()
    test_asia_session_gate()
    print("\n" + "─" * 50)
    print("✅ Semua uji lolos.")
