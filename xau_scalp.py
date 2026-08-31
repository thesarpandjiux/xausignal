#!/usr/bin/env python3
"""
xau_scalp.py — mode scalping XAUUSD, TERPISAH dari xau_signal.py.

Filosofi beda dari xau_signal.py (mode "swing"):
    xau_signal.py : veto gate — 5 syarat WAJIB semua harus lolos, gagal 1 =
                    NO-TRADE total. Sinyal jarang, tapi presisi tinggi.
    xau_scalp.py  : confluence SCORE — tidak ada yang wajib mutlak, makin
                    banyak trigger terpenuhi makin tinggi grade. Sinyal lebih
                    sering, sesuai permintaan eksplisit user ("gaperlu ada
                    semua, makin ada semua trigger makin gede persentasenya").

Timeframe hierarki (beda dari mode swing yang D1→H4→H1):
    H1  : arah tren
    M15 : momentum (RSI + MACD)
    M5  : liquidity sweep + titik entry presisi

Filosofi timing: fire begitu harga MENYENTUH zona + liquidity sweep
terdeteksi — BUKAN menunggu candle konfirmasi reversal selesai kebentuk.
Ini "potensi reaction", bukan "after reaction" (sesuai permintaan user).
Trade-off: entry lebih awal/dekat ke titik optimal, tapi risiko whipsaw
lebih tinggi karena belum ada konfirmasi candle close.

3 trigger (tiap lolos = +1, skor = n_lolos/3 × 100%):
    1. Trend H1 searah      — EMA20 > EMA50 H1 + slope searah
    2. Momentum M15 searah  — RSI + MACD histogram searah tren H1
    3. Liquidity sweep M5   — wick menembus swing high/low lalu close balik
    (Order Block & FVG M15 dihapus — ablation: nol efek / merugikan)

Ambang kirim (bukan gate veto, tapi threshold kualitas): minimal 2/3 trigger
DAN wajib termasuk Trend H1 — biar bukan cuma "kebetulan momentum" tanpa arah
yang jelas. Grade: A=3/3, B=2/3. C (1/3) tidak dikirim.

ENV: sama seperti xau_signal.py (TELEGRAM_BOT_TOKEN/CHAT_ID, TWELVEDATA_API_KEY dst)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import datafeed  # noqa: F401 — memuat .env
import xau_signal as xs   # reuse indikator & util, jangan duplikasi

TF_TREND, TF_MOMENTUM, TF_ENTRY = "1h", "15m", "5m"
BARS = 300

MIN_TRIGGERS = 2          # dari 3 — cukup 2/3 trigger (Trend H1 wajib)
ATR_SL_MULT = 0.8         # SL lebih ketat dari mode swing (1.0-2.5x) —
                           # scalp menahan posisi jauh lebih singkat
MIN_RR = 1.2              # target lebih dekat, wajar untuk scalp

BASE = Path(os.getenv("XAU_HOME", "~/.xau_signal")).expanduser()
STATE_FILE = BASE / "scalp_state.json"
CALIB_FILE = BASE / "scalp_calibration.json"
LOG_FILE = BASE / "signals.csv"


@dataclass
class Trigger:
    name: str
    passed: bool
    note: str


@dataclass
class ScalpSignal:
    time: datetime
    direction: str          # BUY / SELL / NO-TRADE
    grade: str               # A (3/3), B (2/3), "-" (<2/3, tidak dikirim)
    n_triggers: int
    triggers: list[Trigger] = field(default_factory=list)
    price: float = 0.0
    atr: float = 0.0
    stop_loss: float = 0.0
    targets: list[float] = field(default_factory=list)
    rr: list[float] = field(default_factory=list)
    data_source: str = ""

    def signal_id(self) -> str:
        return f"{self.time:%y%m%d-%H%M}-SC{self.direction[:1]}{self.n_triggers}"


# ──────────────────────── Deteksi tiap trigger ───────────────────────────────

def trend_h1(h1: pd.DataFrame) -> tuple[int, Trigger]:
    """Arah dari EMA20 vs EMA50 + slope 5 bar. Return (+1/-1/0, Trigger utk arah dominan)."""
    c = h1["close"]
    e20, e50 = xs.ema(c, 20), xs.ema(c, 50)
    slope_up = e20.iloc[-1] > e20.iloc[-6]
    if e20.iloc[-1] > e50.iloc[-1] and slope_up:
        return 1, Trigger("Trend H1", True, "EMA20>EMA50, slope naik")
    if e20.iloc[-1] < e50.iloc[-1] and not slope_up:
        return -1, Trigger("Trend H1", True, "EMA20<EMA50, slope turun")
    return 0, Trigger("Trend H1", False, "tidak searah / choppy")


def momentum_m15(m15: pd.DataFrame, direction: int) -> Trigger:
    c = m15["close"]
    r = float(xs.rsi(c).iloc[-1])
    _, _, hist = xs.macd(c)
    h_now, h_prev = float(hist.iloc[-1]), float(hist.iloc[-4])
    macd_up = h_now > h_prev
    rsi_ok = (r > 50) if direction > 0 else (r < 50)
    macd_ok = macd_up if direction > 0 else not macd_up
    ok = rsi_ok and macd_ok
    return Trigger("Momentum M15", ok, f"RSI {r:.0f}, MACD hist {h_now:+.2f}")


def order_block(m15: pd.DataFrame, direction: int, a: float) -> Trigger:
    """Candle terakhir berlawanan arah sebelum gerak impulsif searah,
    lalu harga balik menyentuh zona itu (body candle tsb)."""
    w = m15.tail(40)
    px = float(w["close"].iloc[-1])
    for i in range(len(w) - 6, 3, -1):
        body = abs(w["close"].iloc[i] - w["open"].iloc[i])
        if body < 1.5 * a:
            continue
        is_bull_impulse = w["close"].iloc[i] > w["open"].iloc[i]
        # OB valid untuk arah BUY = candle bearish tepat sebelum impuls naik
        if direction > 0 and is_bull_impulse and w["close"].iloc[i - 1] < w["open"].iloc[i - 1]:
            lo, hi = float(w["low"].iloc[i - 1]), float(w["open"].iloc[i - 1])
        elif direction < 0 and not is_bull_impulse and w["close"].iloc[i - 1] > w["open"].iloc[i - 1]:
            lo, hi = float(w["open"].iloc[i - 1]), float(w["high"].iloc[i - 1])
        else:
            continue
        move_after = abs(float(w["close"].iloc[i + 2 if i + 2 < len(w) else -1]) - w["close"].iloc[i])
        if move_after < 2 * a:
            continue
        if lo - 0.1 * a <= px <= hi + 0.1 * a:
            return Trigger("Order Block M15", True, f"harga di zona OB ${lo:.2f}-${hi:.2f}")
    return Trigger("Order Block M15", False, "harga di luar zona OB terdekat")


def fair_value_gap(m15: pd.DataFrame, direction: int) -> Trigger:
    """Gap 3-candle: celah antara candle i-1 dan i+1 yang belum terisi penuh."""
    w = m15.tail(30)
    px = float(w["close"].iloc[-1])
    for i in range(len(w) - 3, 2, -1):
        lo1, hi1 = float(w["low"].iloc[i - 1]), float(w["high"].iloc[i - 1])
        lo3, hi3 = float(w["low"].iloc[i + 1]), float(w["high"].iloc[i + 1])
        if direction > 0 and lo3 > hi1:              # bullish FVG
            if hi1 <= px <= lo3:
                return Trigger("Fair Value Gap M15", True, f"di FVG bullish ${hi1:.2f}-${lo3:.2f}")
        elif direction < 0 and hi3 < lo1:             # bearish FVG
            if hi3 <= px <= lo1:
                return Trigger("Fair Value Gap M15", True, f"di FVG bearish ${hi3:.2f}-${lo1:.2f}")
    return Trigger("Fair Value Gap M15", False, "tidak ada FVG belum-terisi di harga sekarang")


def liquidity_sweep(m5: pd.DataFrame, direction: int) -> Trigger:
    """Wick menembus swing high/low 20-bar lalu CLOSE balik ke dalam range
    dalam 3 bar terakhir — pola stop-hunt sebelum reversal."""
    w = m5.tail(60)
    highs, lows = xs.swing_levels(w, lookback=40, k=2)
    recent = w.tail(3)
    for _, bar in recent.iterrows():
        if direction > 0 and lows:
            near = min(lows, key=lambda lv: abs(lv - bar["low"]))
            if bar["low"] < near and bar["close"] > near:
                return Trigger("Liquidity Sweep M5", True,
                              f"wick tembus low ${near:.2f}, close balik naik")
        if direction < 0 and highs:
            near = min(highs, key=lambda lv: abs(lv - bar["high"]))
            if bar["high"] > near and bar["close"] < near:
                return Trigger("Liquidity Sweep M5", True,
                              f"wick tembus high ${near:.2f}, close balik turun")
    return Trigger("Liquidity Sweep M5", False, "belum ada sweep terdeteksi 3 bar terakhir")


# ─────────────────────────────── Perakit sinyal ──────────────────────────────

def build_scalp_signal(h1: pd.DataFrame, m15: pd.DataFrame, m5: pd.DataFrame,
                        now: datetime, data_source: str = "") -> ScalpSignal:
    direction, trend_trig = trend_h1(h1)
    px = float(m5["close"].iloc[-1])
    a = float(xs.atr(m5).iloc[-1])

    if direction == 0:
        return ScalpSignal(time=now, direction="NO-TRADE", grade="-",
                           n_triggers=0, triggers=[trend_trig],
                           price=px, atr=a, data_source=data_source)

    triggers = [
        trend_trig,
        momentum_m15(m15, direction),
        liquidity_sweep(m5, direction),
    ]
    n = sum(1 for t in triggers if t.passed)
    # OB & FVG dihapus (ablation: nol efek / merugikan). Sistem sekarang
    # Trend + Momentum + Liquidity Sweep — bukti /tmp/scalp_variant2.py:
    # +0.275R (vs baseline +0.254R), win 58%, 88 sinyal.
    # Trend H1 wajib (arah jelas). Grade: A=3/3, B=2/3, C=1/3.
    grade = {3: "A", 2: "B", 1: "C"}.get(n, "-")
    dirn = "BUY" if direction > 0 else "SELL"

    eligible = n >= MIN_TRIGGERS and trend_trig.passed
    if not eligible:
        return ScalpSignal(time=now, direction="NO-TRADE", grade=grade,
                           n_triggers=n, triggers=triggers,
                           price=px, atr=a, data_source=data_source)

    sl = px - direction * ATR_SL_MULT * a
    risk = abs(px - sl)
    tp1 = px + direction * MIN_RR * risk
    tp2 = px + direction * (MIN_RR + 1) * risk

    return ScalpSignal(time=now, direction=dirn, grade=grade, n_triggers=n,
                       triggers=triggers, price=px, atr=a, stop_loss=sl,
                       targets=[tp1, tp2], rr=[MIN_RR, MIN_RR + 1],
                       data_source=data_source)


# ─────────────────────────────── Backtest ───────────────────────────────────

def run_backtest(h1: pd.DataFrame, m15: pd.DataFrame, m5: pd.DataFrame,
                 horizon: int = 12) -> dict:
    """Walk-forward M5. Tiap bar M5 jadi kandidat entry; cek TP1/SL mana
    kena duluan dalam `horizon` bar berikutnya. Same-bar TP+SL dihitung LOSS
    (konservatif, sama filosofi journal.py)."""
    rows = []
    for i in range(120, len(m5) - horizon):
        ts = m5.index[i]
        h1s = h1[h1.index <= ts]
        m15s = m15[m15.index <= ts]
        if len(h1s) < 60 or len(m15s) < 60:
            continue
        sig = build_scalp_signal(h1s, m15s, m5.iloc[:i + 1], ts.to_pydatetime(),
                                 data_source="backtest")
        if sig.direction == "NO-TRADE":
            continue

        tp1, sl = sig.targets[0], sig.stop_loss
        won = None
        for _, bar in m5.iloc[i + 1:i + 1 + horizon].iterrows():
            if sig.direction == "BUY":
                if bar["low"] <= sl:
                    won = False; break
                if bar["high"] >= tp1:
                    won = True; break
            else:
                if bar["high"] >= sl:
                    won = False; break
                if bar["low"] <= tp1:
                    won = True; break
        if won is None:
            continue
        rows.append({"grade": sig.grade, "n": sig.n_triggers, "won": won,
                     "r": MIN_RR if won else -1.0})

    df = pd.DataFrame(rows)
    calib = {"_meta": {"generated": datetime.now(timezone.utc).isoformat(),
                        "total_signals": len(df), "horizon_bars": horizon,
                        "horizon_minutes": horizon * 5,
                        "overall_win_rate": None}}
    if df.empty:
        return calib

    calib["_meta"]["overall_win_rate"] = round(df["won"].mean() * 100, 1)
    calib["_meta"]["overall_exp_r"] = round(df["r"].mean(), 3)
    for g, gdf in df.groupby("grade"):
        calib[f"{g}:*"] = {"n": len(gdf),
                           "win_rate": round(gdf["won"].mean() * 100, 1),
                           "exp_r": round(gdf["r"].mean(), 3)}
    for n, ndf in df.groupby("n"):
        calib[f"{int(n)}/3"] = {"n": len(ndf),
                                "win_rate": round(ndf["won"].mean() * 100, 1),
                                "exp_r": round(ndf["r"].mean(), 3)}
    return calib


# ─────────────────────────────── Pesan Telegram ──────────────────────────────

def format_message(sig: ScalpSignal) -> str:
    wib = sig.time.astimezone(timezone(timedelta(hours=7)))
    if sig.direction == "NO-TRADE":
        lolos = [t.name for t in sig.triggers if t.passed]
        return (f"⚪ <b>SCALP — belum eligible</b>\n<i>{wib:%d %b %H:%M} WIB · "
                f"${sig.price:,.2f}</i>\n\nTrigger lolos: {sig.n_triggers}/3 "
                f"(min {MIN_TRIGGERS})\n" +
                ("\n".join(f"✅ {n}" for n in lolos) or "—"))

    icon = "🟢" if sig.direction == "BUY" else "🔴"
    L = [f"{icon} <b>SCALP {sig.direction} XAUUSD</b> · Grade {sig.grade} ({sig.n_triggers}/3)",
        f"<i>{wib:%d %b %H:%M} WIB · ${sig.price:,.2f}</i>", ""]
    L.append(f"Entry: <b>${sig.price:,.2f}</b>")
    L.append(f"SL: <b>${sig.stop_loss:,.2f}</b>")
    for i, tp in enumerate(sig.targets, 1):
        L.append(f"TP{i}: <b>${tp:,.2f}</b> (R:R {sig.rr[i-1]:.1f})")
    L.append("")
    L.append("<b>Trigger terpenuhi</b>")
    for t in sig.triggers:
        m = "✅" if t.passed else "➖"
        L.append(f"{m} {xs.esc(t.name)} — <i>{xs.esc(t.note)}</i>")
    L += ["", "<i>Mode scalp: entry di POTENSI reaction (sentuh zona + sweep), "
              "bukan setelah konfirmasi candle close. Risiko whipsaw lebih "
              "tinggi dari sinyal swing biasa.</i>",
          "<i>Bukan rekomendasi investasi. Risiko ditanggung sendiri.</i>"]
    return "\n".join(L)


# ──────────────────────────────── State & main ───────────────────────────────

def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(d: dict) -> None:
    BASE.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(d, default=str))


def log_signal(sig: ScalpSignal, sent: bool) -> None:
    """Catat sinyal scalp dalam skema journal.py; hanya satu baris per ID."""
    if sig.direction == "NO-TRADE":
        return
    BASE.mkdir(parents=True, exist_ok=True)
    new = not LOG_FILE.exists()
    if not new:
        with LOG_FILE.open(newline="") as f:
            if sig.signal_id() in {r["id"] for r in csv.DictReader(f)}:
                return
    tps = (sig.targets + [None, None, None])[:3]
    row = dict(zip(xs.SIGNAL_COLS, [
        sig.time.isoformat(), sig.signal_id(), sig.direction, sig.grade,
        sig.n_triggers, sig.n_triggers, 0, round(sig.price, 2),
        round(sig.price, 2), round(sig.stop_loss, 2),
        *[round(t, 2) if t is not None else "" for t in tps],
        round(sig.rr[0], 2) if sig.rr else "", sig.n_triggers, sent,
        sig.data_source.split(" (")[0], "scalp"]))
    with LOG_FILE.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=xs.SIGNAL_COLS)
        if new:
            w.writeheader()
        w.writerow(row)


def should_send(sig: ScalpSignal, state: dict, now: datetime) -> tuple[bool, str]:
    if sig.direction == "NO-TRADE":
        return False, f"belum eligible ({sig.n_triggers}/3 trigger)"
    last = state.get("last")
    if last and last.get("direction") == sig.direction:
        age = now - datetime.fromisoformat(last["time"])
        moved = abs(sig.price - last.get("price", 0)) / sig.atr if sig.atr else 99
        if age < timedelta(minutes=45) and moved < 0.5:
            return False, "duplikat, cooldown 45 menit"
    return True, "ok"


def validate_live_feeds(feeds: list, now: datetime) -> None:
    """Scalp harus fail-closed: cache fallback dan candle M5 lama dilarang."""
    for feed in feeds:
        if feed.stale:
            raise RuntimeError(f"data harga basi: {feed.label()}")
    last = pd.Timestamp(feeds[-1].df.index[-1])
    if last.tzinfo is None:
        last = last.tz_localize("UTC")
    age = pd.Timestamp(now) - last
    if age > pd.Timedelta(minutes=10):
        raise RuntimeError(f"candle M5 terakhir terlalu lama: {age.total_seconds() / 60:.0f} menit")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--backtest", action="store_true", help="bangun scalp_calibration.json")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    prefer = "dukascopy" if args.backtest else None
    if args.backtest:
        h1, _ = xs.get_ohlc(TF_TREND, prefer=prefer)
        m15, _ = xs.get_ohlc(TF_MOMENTUM, prefer=prefer)
        m5, src = xs.get_ohlc(TF_ENTRY, prefer=prefer)
    else:
        import datafeed
        feeds = [datafeed.get_ohlc(tf, xs.BARS) for tf in
                 (TF_TREND, TF_MOMENTUM, TF_ENTRY)]
        validate_live_feeds(feeds, now)
        h1, m15, m5 = [f.df for f in feeds]
        src = feeds[-1].label()

    if args.backtest:
        print("Menjalankan backtest scalp…")
        calib = run_backtest(h1, m15, m5)
        BASE.mkdir(parents=True, exist_ok=True)
        CALIB_FILE.write_text(json.dumps(calib, indent=2))
        meta = calib["_meta"]
        print(f"{meta['total_signals']} sinyal · win rate keseluruhan {meta['overall_win_rate']}%")
        for k in sorted(k for k in calib if k != "_meta"):
            print(f"  {k}: {calib[k]['win_rate']}% (n={calib[k]['n']})")
        print(f"Tersimpan di {CALIB_FILE}")
        print("Catatan: ini in-sample; pakai sebagai kalibrasi awal, bukan jaminan.")
        return 0

    if not args.force and not xs.market_open(now):
        print("Pasar tutup.")
        return 0

    sig = build_scalp_signal(h1, m15, m5, now, data_source=src)
    msg = format_message(sig)
    state = load_state()
    ok, reason = should_send(sig, state, now)

    if args.dry_run:
        for t in ("<b>", "</b>", "<i>", "</i>"):
            msg = msg.replace(t, "")
        print(msg)
        print(f"\n--- kirim: {ok} ({reason}) ---")
        return 0

    if ok or args.force:
        sent = xs.send_telegram(msg)
        log_signal(sig, sent)
        if sent:
            state["last"] = {"id": sig.signal_id(), "time": now.isoformat(),
                             "direction": sig.direction, "price": sig.price}
            save_state(state)
            print(f"Terkirim: SCALP {sig.direction} grade {sig.grade} "
                  f"({sig.n_triggers}/3) @ {sig.price:.2f}")
    else:
        print(f"Dilewati: {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
