#!/usr/bin/env python3
"""
smoke_test.py — uji tanpa jaringan, aman dijalankan di CI.

Fokus pada invarian yang pernah rusak (lihat docs/CATATAN-BUG.md), bukan
sekadar "kodenya jalan".

    python tests/smoke_test.py
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["XAU_HOME"] = tempfile.mkdtemp()

import xau_signal as x          # noqa: E402
import datafeed as d            # noqa: E402
import journal as J             # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


def synth(interval, bars, drift, seed=7):
    rng = np.random.default_rng(seed)
    vol = {"1h": 4.0, "4h": 8.0, "1d": 20.0}[interval]
    mult = {"1h": 1, "4h": 4, "1d": 24}[interval]
    c = 3400 + np.cumsum(rng.normal(drift * mult, vol, bars))
    return pd.DataFrame(
        {"open": np.concatenate([[c[0]], c[:-1]]),
         "high": c + np.abs(rng.normal(0, vol * .5, bars)),
         "low": c - np.abs(rng.normal(0, vol * .5, bars)), "close": c},
        index=pd.date_range(end=datetime.now(timezone.utc), periods=bars,
                            freq=f"{mult}h", tz="UTC"))


def main():
    now = datetime.now(timezone.utc)
    print("\nIndikator")
    s = pd.Series(np.linspace(3300, 3400, 100))
    check("RSI dalam 0–100", 0 <= float(x.rsi(s).iloc[-1]) <= 100)
    check("EMA mengikuti tren naik", x.ema(s, 10).iloc[-1] > x.ema(s, 50).iloc[-1])
    df = synth("1h", 200, 0.3)
    check("ATR positif", float(x.atr(df).iloc[-1]) > 0)

    print("\nScoring")
    up = (synth("4h", 400, .5), synth("1h", 400, .5), synth("1d", 400, .5))
    dn = (synth("4h", 400, -.5, 3), synth("1h", 400, -.5, 3), synth("1d", 400, -.5, 3))
    su, _ = x.technical_score(up[0], up[1], up[2])
    sd, _ = x.technical_score(dn[0], dn[1], dn[2])
    check("tren naik → skor positif", su > 0, f"{su:+.1f}")
    check("tren turun → skor negatif", sd < 0, f"{sd:+.1f}")
    check("skor dalam -100..100", -100 <= su <= 100 and -100 <= sd <= 100)

    print("\nInvarian SL/TP (bug #5)")
    bad_sl = bad_gap = n = 0
    for seed in range(1, 15):
        for dr in (.6, -.6, .2):
            b, e, m = (synth("4h", 400, dr, seed), synth("1h", 400, dr, seed),
                       synth("1d", 400, dr, seed))
            sg = x.build_signal(b, e, m, [], now, {})
            if sg.direction == "NO-TRADE":
                continue
            n += 1
            r = sg.risk_usd / sg.atr
            if not (x.SL_MIN_ATR - .01 <= r <= x.SL_MAX_ATR + .01):
                bad_sl += 1
            for i in range(len(sg.targets) - 1):
                if abs(sg.targets[i + 1] - sg.targets[i]) < x.TP_MIN_GAP_R * sg.risk_usd - .01:
                    bad_gap += 1
    check(f"SL dalam pita {x.SL_MIN_ATR}–{x.SL_MAX_ATR} ATR", bad_sl == 0,
          f"{n} sinyal diuji")
    check(f"jarak antar TP ≥ {x.TP_MIN_GAP_R}R", bad_gap == 0)
    check("ada sinyal terbentuk", n > 0, f"{n}")

    print("\nGate keselamatan (bug #4)")
    b, e, m = synth("4h", 400, .4, 11), synth("1h", 400, .4, 11), synth("1d", 400, .4, 11)
    ok = x.build_signal(b, e, m, [], now, {}, calendar_trusted=True)
    blind = x.build_signal(b, e, m, [], now, {}, calendar_trusted=False)
    check("kalender buta → blackout", blind.blackout is not None)
    check("kalender buta → NO-TRADE", blind.direction == "NO-TRADE")
    ev = [{"title": "NFP", "country": "USD", "impact": "High",
           "time": pd.Timestamp(now + timedelta(minutes=20)),
           "forecast": "", "previous": ""}]
    check("event high-impact → blackout",
          x.build_signal(b, e, m, ev, now, {}).blackout is not None)
    check("kalender normal → tidak diblokir", ok.blackout is None)

    print("\nKalibrasi (bug: angka karangan)")
    check("sampel <20 → tanpa angka",
          x.lookup_confidence({"A:*": {"n": 5, "win_rate": 100}}, "A", 50) == (None, 0))
    c, cn = x.lookup_confidence({"A:*": {"n": 90, "win_rate": 61.0}}, "A", 50)
    check("sampel cukup → angka muncul", c == 61.0 and cn == 90)
    check("tabel kosong → tanpa angka", x.lookup_confidence({}, "B", 50) == (None, 0))

    print("\nGrade (bug #3)")
    grades = set()
    for seed in range(1, 25):
        for dr in (.6, -.6, .3, -.3, .1):
            sg = x.build_signal(synth("4h", 400, dr, seed), synth("1h", 400, dr, seed),
                                synth("1d", 400, dr, seed), [], now, {})
            if sg.direction != "NO-TRADE":
                grades.add(sg.grade)
    check("Grade A dapat tercapai", "A" in grades, f"ditemukan: {sorted(grades)}")

    print("\nCache datafeed (bug #6)")
    idx = pd.date_range("2026-01-01", periods=400, freq="h", tz="UTC")
    cdf = pd.DataFrame({"open": np.arange(400.) + 3300, "high": np.arange(400.) + 3305,
                        "low": np.arange(400.) + 3295,
                        "close": np.arange(400.) + 3302}, index=idx)
    d._write_cache("ohlc_1h", cdf)
    f = d.get_ohlc("1h", 200)
    check("simpan 400 → minta 200 kena cache", f.source == "cache" and len(f.df) == 200)
    raw = pd.DataFrame({"Open": [1., 2, 2], "High": [2., 3, 3], "Low": [0., 1, 1],
                        "Close": [1.5, 2.5, 2.5], "Volume": [0, 0, 0]},
                       index=pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-02"],
                                            utc=True))
    nm = d._normalize(raw)
    check("normalisasi kolom & duplikat",
          list(nm.columns) == ["open", "high", "low", "close"] and len(nm) == 2)

    print("\nJurnal")
    px = synth("1h", 100, 0, 5)
    t0 = px.index[10]
    sig = pd.Series({"time": t0, "direction": "BUY", "entry": float(px["close"].iloc[10]),
                     "sl": float(px["close"].iloc[10]) - 1000,
                     "tp1": float(px["close"].iloc[10]) + 1000, "rr1": 1.5})
    out, r, _, _ = J.resolve(sig, px)
    check("target tak terjangkau → TIMEOUT/PENDING", out in ("TIMEOUT", "PENDING"), out)
    sig2 = sig.copy()
    sig2["sl"] = float(px["close"].iloc[10]) - 0.01
    sig2["tp1"] = float(px["close"].iloc[10]) + 0.01
    out2, _, _, note = J.resolve(sig2, px)
    check("TP & SL bar sama → dihitung kalah", out2 == "LOSS", note or out2)

    print("\nUtilitas")
    check("Sabtu → pasar tutup",
          not x.market_open(datetime(2026, 8, 22, 12, tzinfo=timezone.utc)))
    check("Rabu → pasar buka",
          x.market_open(datetime(2026, 8, 19, 12, tzinfo=timezone.utc)))
    msg = x.format_message(ok)
    check("format lengkap menghasilkan teks", len(msg) > 200)
    check("format ringkas menghasilkan teks", len(x.format_message_simple(ok)) > 50)
    check("pesan Telegram ≤4096 karakter", len(msg) <= 4096, f"{len(msg)}")

    print("\n" + "─" * 50)
    if FAIL:
        print(f"❌ {len(FAIL)} gagal: {', '.join(FAIL)}")
        return 1
    print("✅ Semua uji lolos.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
