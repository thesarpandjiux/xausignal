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
from pathlib import Path

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

    # Blackout asimetris: sebelum rilis arah tak diketahui (wajib blokir),
    # sesudah rilis arah sudah terungkap (blokir lebih pendek).
    def _ev(mins):
        return [{"title": "NFP", "country": "USD", "impact": "High",
                 "time": pd.Timestamp(now + timedelta(minutes=mins)),
                 "forecast": "", "previous": ""}]
    check("55 mnt sebelum rilis → blokir",
          x.check_blackout(_ev(55), now)[0] is not None)
    check("90 mnt sebelum rilis → bebas",
          x.check_blackout(_ev(90), now)[0] is None)
    check("25 mnt sesudah rilis → blokir",
          x.check_blackout(_ev(-25), now)[0] is not None)
    check("40 mnt sesudah rilis → bebas",
          x.check_blackout(_ev(-40), now)[0] is None)
    check("kalender normal → tidak diblokir", ok.blackout is None)

    print("\nKalibrasi (bug: angka karangan)")
    check("sampel <20 → tanpa angka",
          x.lookup_confidence({"A:*": {"n": 5, "win_rate": 100}}, "A", 50) == (None, 0, None))
    c, cn, er = x.lookup_confidence(
        {"A:*": {"n": 90, "win_rate": 61.0, "exp_r": 0.3}}, "A", 50)
    check("sampel cukup → angka muncul", c == 61.0 and cn == 90 and er == 0.3)
    check("tabel kosong → tanpa angka", x.lookup_confidence({}, "B", 50) == (None, 0, None))

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

    print("\nPemetaan interval Twelve Data (bug #11)")
    import requests as _rq
    _real_get = _rq.get
    _box = []

    class _R:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"values": [{"datetime": "2026-08-21 10:00:00", "open": "1",
                                "high": "2", "low": "0", "close": "1.5"}] * 80}

    _rq.get = lambda url, params=None, **k: (_box.append(dict(params or {})), _R())[1]
    os.environ["TWELVEDATA_API_KEY"] = "dummy"
    _seen = {}
    for _iv in ("1h", "4h", "1d"):
        _box.clear()
        try:
            d.from_twelvedata(_iv, 400)
        except Exception:
            pass
        _seen[_iv] = _box[0] if _box else {}
    _rq.get = _real_get
    check("1d dipetakan ke 1day", _seen["1d"].get("interval") == "1day",
          _seen["1d"].get("interval"))
    check("1h tetap 1h", _seen["1h"].get("interval") == "1h")
    check("4h tetap 4h", _seen["4h"].get("interval") == "4h")
    check("timezone UTC dikirim untuk intraday",
          _seen["1h"].get("timezone") == "UTC")
    check("timezone tidak dikirim untuk harian",
          "timezone" not in _seen["1d"])

    print("\nPenguncian sumber (bug #9)")
    _orig = d.SOURCES

    def _mk(px, n=200):
        i = pd.date_range("2026-08-01", periods=n, freq="h", tz="UTC")
        c = np.full(n, float(px))
        return pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c},
                            index=i)

    d.SOURCES = [("twelvedata", lambda i, b: _mk(4540.16)),
                 ("yfinance", lambda i, b: _mk(4594.00))]
    d.reset_session_source()
    s1 = d.get_ohlc("4h", 100, use_cache=False).source
    s2 = d.get_ohlc("1h", 100, use_cache=False).source
    check("timeframe memakai sumber yang sama", s1 == s2, f"{s1} / {s2}")

    d.SOURCES = [("twelvedata",
                  lambda i, b: (_ for _ in ()).throw(RuntimeError("mati"))),
                 ("yfinance", lambda i, b: _mk(4594.00))]
    switched = False
    try:
        switched = d.get_ohlc("1d", 100, use_cache=False).source == "yfinance"
    except RuntimeError:
        pass
    check("tidak berganti instrumen saat sumber mati", not switched)
    poisoned, _, _ = d._read_cache("ohlc_1d", 999999)
    check("sumber yang ditolak tidak meracuni cache",
          poisoned is None or float(poisoned["close"].iloc[-1]) != 4594.0)

    d.reset_session_source()
    check("sesi baru boleh pilih sumber lain",
          d.get_ohlc("1h", 100, use_cache=False).source == "yfinance")
    d.SOURCES = _orig
    d.reset_session_source()

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

    print("\nPemuat .env (bug #7)")
    import tempfile as _tf
    _d = _tf.mkdtemp()
    (Path(_d) / ".env").write_text(
        'export A_TOKEN="abc123"      # komentar di belakang (bug #8)\n'
        "B_ID='999'      # kutip tunggal + komentar\n"
        "C_VAL=7   # tanpa kutip\n"
        'E_HASH="pa#ss"      # pagar di dalam kutip\n'
        'F_EMPTY=""      # kosong + komentar\n'
        "# baris komentar\n\n")
    _cwd = os.getcwd()
    os.chdir(_d)
    for k in ("A_TOKEN", "B_ID", "C_VAL", "E_HASH", "F_EMPTY"):
        os.environ.pop(k, None)
    os.environ["D_EXISTING"] = "jangan_ditimpa"
    d.load_env()
    os.chdir(_cwd)
    for k in ("E_HASH", "F_EMPTY"):
        pass
    check("kutip + komentar di belakang", os.environ.get("A_TOKEN") == "abc123",
          repr(os.environ.get("A_TOKEN")))
    check("kutip tunggal + komentar", os.environ.get("B_ID") == "999")
    check("tanpa kutip + komentar", os.environ.get("C_VAL") == "7")
    check("pagar di dalam kutip dipertahankan",
          os.environ.get("E_HASH") == "pa#ss", repr(os.environ.get("E_HASH")))
    check("nilai kosong tetap kosong", os.environ.get("F_EMPTY") == "")
    check("env yang sudah ada tidak ditimpa",
          os.environ.get("D_EXISTING") == "jangan_ditimpa")

    print("\nPerintah Telegram")
    import telegram_bot as tb
    _tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    _cid = os.environ.get("TELEGRAM_CHAT_ID")
    os.environ["TELEGRAM_BOT_TOKEN"] = "123:FAKE"
    os.environ["TELEGRAM_CHAT_ID"] = "99999"
    _sent = []
    import requests as _rq2
    _rp = _rq2.post

    def _fp(url, json=None, **k):
        _m = url.rsplit("/", 1)[-1]

        class _R:
            status_code = 200

            def raise_for_status(s):
                pass

            def json(s):
                if _m == "sendMessage":
                    _sent.append(json)
                    return {"ok": True, "result": {}}
                return {"ok": True, "result": []}
        return _R()
    _rq2.post = _fp

    tb.handle({"update_id": 1,
               "message": {"chat": {"id": 555555}, "text": "/status"}})
    check("chat tak dikenal diabaikan", len(_sent) == 0)

    tb.handle({"update_id": 2,
               "message": {"chat": {"id": 99999}, "text": "/bantuan"}})
    check("perintah sah dijawab", len(_sent) == 1)

    _sent.clear()
    tb.handle({"update_id": 3,
               "message": {"chat": {"id": 99999}, "text": "/BANTUAN@NamaBot"}})
    check("huruf besar & @suffix dinormalisasi", len(_sent) == 1)

    _sent.clear()
    tb.handle({"update_id": 4,
               "message": {"chat": {"id": 99999}, "text": "/tidakada"}})
    check("perintah asing dapat balasan sopan",
          len(_sent) == 1 and "tidak dikenal" in _sent[0]["text"])

    # Batas /analisa berbasis candle
    _xh = os.environ["XAU_HOME"]
    import tempfile as _tf2
    os.environ["XAU_HOME"] = _tf2.mkdtemp()
    import importlib
    importlib.reload(tb)
    _now = datetime.now(timezone.utc)
    tb.save_tg_state({"waktu": _now.isoformat(),
                      "candle": (_now - timedelta(minutes=5)).isoformat(),
                      "ringkasan": "test"})
    check("/analisa ditolak saat bar masih baru",
          tb.cmd_analisa().startswith("\u23f3"))
    tb.save_tg_state({"waktu": _now.isoformat(),
                      "candle": (_now - timedelta(minutes=70)).isoformat(),
                      "ringkasan": "test"})
    check("/analisa lolos saat bar sudah lewat",
          not tb.cmd_analisa().startswith("\u23f3"))
    check("/status tak dibatasi", "Status bot" in tb.cmd_status())
    os.environ["XAU_HOME"] = _xh
    importlib.reload(tb)

    _rq2.post = _rp
    if _tok is None:
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
    else:
        os.environ["TELEGRAM_BOT_TOKEN"] = _tok
    if _cid is None:
        os.environ.pop("TELEGRAM_CHAT_ID", None)
    else:
        os.environ["TELEGRAM_CHAT_ID"] = _cid

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
