#!/usr/bin/env python3
"""
XAUUSD Signal Bot v2 — teknikal + news → Telegram

Perubahan dari v1:
  • Confluence gating: syarat wajib yang bisa mem-veto, bukan cuma skor aditif
  • SL/TP berbasis struktur swing, bukan kelipatan ATR murni
  • Grade setup A/B/C
  • Confidence diambil dari backtest (calibration.json), bukan dikarang
  • Setiap evaluasi dicatat ke CSV untuk kalibrasi ulang

Jalankan:
    python xau_signal.py --demo         # data sintetis
    python xau_signal.py --backtest     # bangun calibration.json
    python xau_signal.py --dry-run      # live, tidak kirim
    python xau_signal.py --json         # payload JSON mentah
    python xau_signal.py                # live

ENV:
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    TWELVEDATA_API_KEY                      (opsional → fallback yfinance)
    LLM_BASE_URL, LLM_API_KEY, LLM_MODEL    (opsional, sentimen news)
    ACCOUNT_BALANCE, RISK_PCT               (opsional, position sizing)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

import datafeed  # noqa: F401  — impor ini memuat .env sebelum os.getenv dipakai

# ─────────────────────────────── Konfigurasi ────────────────────────────────

SYMBOL = "XAU/USD"
TF_MACRO, TF_BIAS, TF_ENTRY = "1d", "4h", "1h"
BARS = 400

W_TECH, W_NEWS = 0.60, 0.40
THRESHOLD = 40
ATR_SL_MULT = 1.5
SL_BUFFER_ATR = 0.3          # jarak aman di balik swing level
SL_MIN_ATR = 1.0             # SL tidak boleh lebih sempit dari noise sejam
SL_MAX_ATR = 2.5             # dan tidak boleh melebar sampai R tak sebanding
TP_MIN_GAP_R = 0.5           # jarak minimum antar target (dalam satuan R)
MIN_RR = 1.5                 # R:R minimum ke target struktur pertama
MAX_EXTENSION_ATR = 2.5      # jangan kejar harga yang sudah jauh dari EMA20
VOL_RANGE = (0.6, 2.2)       # regime ATR yang dianggap layak ditradingkan
MIN_CONFIRMS = 3             # dari 5 syarat konfirmasi

# Override bobot komponen — dipakai learn.py untuk uji ablasi.
# Kosongkan untuk memakai bobot bawaan. Contoh: {"RSI H1": 0.0}
WEIGHT_OVERRIDE: dict = {}

# Blackout ASIMETRIS. Kedua sisi tidak setara:
#   sebelum rilis → arah benar-benar tidak diketahui, wajib diblokir
#   setelah rilis → arah sudah terungkap; yang tersisa hanya spread lebar,
#                   yang normal kembali dalam 15–30 menit
NEWS_BLACKOUT_BEFORE_MIN = 60
NEWS_BLACKOUT_AFTER_MIN = 30
COOLDOWN_HOURS = 4

BASE = Path(os.getenv("XAU_HOME", "~/.xau_signal")).expanduser()
STATE_FILE = BASE / "state.json"
CALIB_FILE = BASE / "calibration.json"
LOG_FILE = BASE / "signals.csv"
FF_CALENDAR = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


# ────────────────────────────── Struktur data ───────────────────────────────

@dataclass
class Component:
    name: str
    score: float
    weight: float
    note: str

    @property
    def contribution(self) -> float:
        return self.score * self.weight


@dataclass
class Check:
    """Satu syarat confluence. mandatory=True berarti bisa mem-veto sinyal."""
    name: str
    passed: bool
    mandatory: bool
    note: str


@dataclass
class Signal:
    time: datetime
    direction: str
    grade: str
    composite: float
    tech_score: float
    news_score: float
    news_available: bool
    price: float
    atr: float
    entry: float
    entry_zone: tuple[float, float]
    stop_loss: float
    targets: list[float]
    rr: list[float]
    invalidation: str = ""
    components: list[Component] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    news_events: list[dict] = field(default_factory=list)
    blackout: str | None = None
    news_note: str = ""
    confidence: float | None = None   # None = belum terkalibrasi
    confidence_n: int = 0
    calendar_trusted: bool = True
    data_source: str = ""

    @property
    def risk_usd(self) -> float:
        return abs(self.entry - self.stop_loss)

    @property
    def n_confirms(self) -> int:
        return sum(1 for c in self.checks if not c.mandatory and c.passed)

    def signal_id(self) -> str:
        return f"{self.time:%y%m%d-%H%M}-{self.direction[:1]}{self.grade}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["time"] = self.time.isoformat()
        d["news_events"] = [{**e, "time": e["time"].isoformat()}
                            for e in self.news_events]
        return d


# ─────────────────────────────── Ambil data ─────────────────────────────────

def fetch_twelvedata(interval: str, bars: int, api_key: str) -> pd.DataFrame:
    r = requests.get("https://api.twelvedata.com/time_series",
                     params={"symbol": SYMBOL, "interval": interval,
                             "outputsize": bars, "apikey": api_key, "format": "JSON"},
                     timeout=20)
    r.raise_for_status()
    payload = r.json()
    if payload.get("status") == "error":
        raise RuntimeError(f"TwelveData: {payload.get('message')}")
    df = pd.DataFrame(payload["values"])
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.set_index("datetime").sort_index()
    for c in ("open", "high", "low", "close"):
        df[c] = pd.to_numeric(df[c])
    return df[["open", "high", "low", "close"]]


def fetch_yfinance(interval: str, bars: int) -> pd.DataFrame:
    import yfinance as yf
    period = {"1h": "60d", "4h": "180d", "1d": "3y"}.get(interval, "60d")
    yfi = "1h" if interval == "4h" else interval
    df = yf.Ticker("GC=F").history(period=period, interval=yfi)
    if df.empty:
        raise RuntimeError("yfinance kosong")
    df.columns = [c.lower() for c in df.columns]
    df = df[["open", "high", "low", "close"]]
    df.index = pd.to_datetime(df.index, utc=True)
    if interval == "4h":
        df = df.resample("4h").agg({"open": "first", "high": "max",
                                    "low": "min", "close": "last"}).dropna()
    return df.tail(bars)


def fetch_demo(interval: str, bars: int, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed + len(interval))
    vol = {"1h": 4.0, "4h": 8.0, "1d": 20.0}.get(interval, 4.0)
    close = 3350 + np.cumsum(rng.normal(0.35, vol, bars))
    high = close + np.abs(rng.normal(0, vol * .5, bars))
    low = close - np.abs(rng.normal(0, vol * .5, bars))
    op = np.concatenate([[close[0]], close[:-1]])
    step = {"1h": 1, "4h": 4, "1d": 24}.get(interval, 1)
    idx = pd.date_range(end=datetime.now(timezone.utc), periods=bars,
                        freq=f"{step}h", tz="UTC")
    return pd.DataFrame({"open": op, "high": high, "low": low, "close": close},
                        index=idx)


def get_ohlc(interval: str, demo: bool = False, prefer: str | None = None):
    """Kembalikan (DataFrame, label sumber). Rantai fallback ada di datafeed.py."""
    if demo:
        return fetch_demo(interval, BARS), "demo"
    import datafeed
    feed = datafeed.get_ohlc(interval, BARS, prefer=prefer)
    return feed.df, feed.label()


# ──────────────────────────────── Indikator ─────────────────────────────────

def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    g = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    ls = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return (100 - 100 / (1 + g / ls.replace(0, np.nan))).fillna(50)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"],
                    (df["high"] - prev).abs(),
                    (df["low"] - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def macd(s: pd.Series, fast=12, slow=26, sig=9):
    line = ema(s, fast) - ema(s, slow)
    return line, ema(line, sig), line - ema(line, sig)


def swing_levels(df: pd.DataFrame, lookback: int = 120, k: int = 3):
    """Fractal swing high/low sebagai kandidat support & resistance."""
    w = df.tail(lookback)
    h, l = w["high"].to_numpy(), w["low"].to_numpy()
    highs, lows = [], []
    for i in range(k, len(w) - k):
        if h[i] == h[i - k:i + k + 1].max():
            highs.append(float(h[i]))
        if l[i] == l[i - k:i + k + 1].min():
            lows.append(float(l[i]))
    return sorted(set(highs)), sorted(set(lows))


def _clamp(x: float, lo: float = -100, hi: float = 100) -> float:
    return max(lo, min(hi, x))


# ─────────────────────────── Scoring teknikal ───────────────────────────────

def technical_score(bias: pd.DataFrame, entry: pd.DataFrame,
                    macro: pd.DataFrame | None = None):
    comps: list[Component] = []

    b = bias["close"]
    e50, e200 = ema(b, 50), ema(b, 200)
    px = float(b.iloc[-1])
    slope = (e50.iloc[-1] - e50.iloc[-6]) / e50.iloc[-6] * 100
    t = (40 if e50.iloc[-1] > e200.iloc[-1] else -40)
    t += 30 if px > e50.iloc[-1] else -30
    t += _clamp(slope * 40, -30, 30)
    comps.append(Component(
        "Trend H4", _clamp(t), 0.30,
        f"EMA50 {'>' if e50.iloc[-1] > e200.iloc[-1] else '<'} EMA200, "
        f"harga {'atas' if px > e50.iloc[-1] else 'bawah'} EMA50, slope {slope:+.2f}%"))

    ec = entry["close"]
    _, _, hist = macd(ec)
    a_entry = atr(entry)
    h_now, h_prev = float(hist.iloc[-1]), float(hist.iloc[-4])
    h_norm = _clamp(h_now / (float(a_entry.iloc[-1]) * 0.5) * 50)
    accel = 20 if abs(h_now) > abs(h_prev) and np.sign(h_now) == np.sign(h_prev) else 0
    comps.append(Component(
        "Momentum H1", _clamp(h_norm + np.sign(h_now) * accel), 0.22,
        f"MACD hist {h_now:+.2f} ({'menguat' if accel else 'melemah'})"))

    r = float(rsi(ec).iloc[-1])
    if abs(comps[0].score) >= 50:                    # regime trending
        rs = (r - 50) * 2.4
        if r >= 82 or r <= 18:
            rs *= 0.5
            note = f"RSI {r:.0f} ekstrem, tren overextended"
        else:
            note = f"RSI {r:.0f} konfirmasi tren"
    elif r >= 70:
        rs, note = -(r - 70) * 3, f"RSI {r:.0f} overbought (ranging)"
    elif r <= 30:
        rs, note = (30 - r) * 3, f"RSI {r:.0f} oversold (ranging)"
    else:
        rs, note = (r - 50) * 1.6, f"RSI {r:.0f} netral"
    comps.append(Component("RSI H1", _clamp(rs), 0.15, note))

    w = entry.tail(50)
    hi, lo = float(w["high"].max()), float(w["low"].min())
    pos = (float(ec.iloc[-1]) - lo) / (hi - lo) if hi > lo else .5
    comps.append(Component("Posisi range", _clamp((pos - .5) * 140), 0.13,
                           f"{pos * 100:.0f}% dari range {lo:.0f}–{hi:.0f}"))

    if macro is not None and len(macro) >= 60:
        m = macro["close"]
        m20, m50 = ema(m, 20), ema(m, 50)
        ms = 60 if m20.iloc[-1] > m50.iloc[-1] else -60
        ms += 40 if float(m.iloc[-1]) > m20.iloc[-1] else -40
        comps.append(Component(
            "Bias D1", _clamp(ms), 0.12,
            f"EMA20 {'>' if m20.iloc[-1] > m50.iloc[-1] else '<'} EMA50 harian"))

    an, aa = float(a_entry.iloc[-1]), float(a_entry.tail(50).mean())
    ratio = an / aa if aa else 1.0
    comps.append(Component(
        "Volatilitas", _clamp((ratio - 1) * 100) * np.sign(comps[0].score or 1), 0.08,
        f"ATR {an:.2f} vs avg {aa:.2f} ({ratio:.2f}×)"))

    if WEIGHT_OVERRIDE:
        for c in comps:
            if c.name in WEIGHT_OVERRIDE:
                c.weight = WEIGHT_OVERRIDE[c.name]

    tw = sum(c.weight for c in comps)
    if tw <= 0:
        return 0.0, comps
    return _clamp(sum(c.contribution for c in comps) / tw), comps


# ──────────────────────── Confluence & level struktur ───────────────────────

def structure_levels(direction: str, entry: pd.DataFrame, px: float, a: float):
    """
    SL di balik swing level terdekat, TAPI dikunci ke pita 1.0–2.5 ATR.
    Tanpa pita ini jarak SL bisa berayun 0.8–4.0 ATR antar sinyal: yang terlalu
    sempit tersapu noise biasa, yang terlalu lebar membuat "1R" tidak sebanding
    antar sinyal — dan kalibrasi win-rate jadi membandingkan hal yang berbeda.

    TP mengejar level struktur, dengan jarak minimum antar target supaya tidak
    muncul TP2 dan TP3 yang terpaut beberapa sen.
    """
    sign = 1 if direction == "BUY" else -1
    highs, lows = swing_levels(entry)

    # ── Stop loss ──
    if direction == "BUY":
        cands = [lv for lv in lows if lv < px - 0.2 * a]
        raw_sl = (max(cands) - SL_BUFFER_ATR * a) if cands else px - ATR_SL_MULT * a
    else:
        cands = [lv for lv in highs if lv > px + 0.2 * a]
        raw_sl = (min(cands) + SL_BUFFER_ATR * a) if cands else px + ATR_SL_MULT * a

    risk = min(max(abs(px - raw_sl), SL_MIN_ATR * a), SL_MAX_ATR * a)
    sl = px - sign * risk

    # ── Target ──
    # Level struktural dicari yang sudah >= MIN_RR (bukan 1R) supaya TP1
    # yang lolos filter di sini otomatis lolos gate "R:R memadai" di
    # build_checks(). Filter longgar (>= 1R) + gate ketat (>= MIN_RR)
    # bikin sinyal valid ke-reject: TP1 kepilih di 1.2R misalnya, padahal
    # ada level 2.3R yang sebenarnya memenuhi MIN_RR=2.0.
    levels = [lv for lv in (highs if direction == "BUY" else lows)
              if sign * (lv - px) >= MIN_RR * risk]
    levels.sort(key=lambda lv: abs(lv - px))

    tps: list[float] = []
    for lv in levels:
        if all(abs(lv - t) >= TP_MIN_GAP_R * risk for t in tps):
            tps.append(lv)
        if len(tps) == 3:
            break

    for rr in (1.5, 2.5, 3.5, 4.5, 5.5):       # lengkapi dengan kelipatan R, mulai >= MIN_RR
        if len(tps) >= 3:
            break
        cand = px + sign * rr * risk
        if all(abs(cand - t) >= TP_MIN_GAP_R * risk for t in tps):
            tps.append(cand)

    tps.sort(key=lambda t: abs(t - px))         # TP1 terdekat, TP3 terjauh
    tps = tps[:3]
    return sl, tps, [abs(t - px) / risk for t in tps]


def build_checks(direction, entry, comps, news, news_ok, blackout, rr_first):
    """Syarat wajib mem-veto. Syarat konfirmasi dihitung jumlahnya."""
    sign = 1 if direction == "BUY" else -1
    ec = entry["close"]
    px = float(ec.iloc[-1])
    a_series = atr(entry)
    a = float(a_series.iloc[-1])
    checks: list[Check] = []

    # ── Wajib ──
    trend = comps[0].score
    checks.append(Check("Searah tren H4",
                        bool(np.sign(trend) == sign and abs(trend) >= 25), True,
                        f"skor tren {trend:+.0f}"))

    e20 = float(ema(ec, 20).iloc[-1])
    ext = abs(px - e20) / a if a else 0
    checks.append(Check("Tidak overextended", ext <= MAX_EXTENSION_ATR, True,
                        f"{ext:.1f} ATR dari EMA20 (maks {MAX_EXTENSION_ATR})"))

    checks.append(Check("R:R memadai", rr_first >= MIN_RR, True,
                        f"R:R ke TP1 {rr_first:.2f} (min {MIN_RR})"))

    aa = float(a_series.tail(50).mean())
    ratio = a / aa if aa else 1.0
    checks.append(Check("Volatilitas wajar",
                        VOL_RANGE[0] <= ratio <= VOL_RANGE[1], True,
                        f"ATR {ratio:.2f}× rata-rata"))

    checks.append(Check("Bebas blackout news", blackout is None, True,
                        blackout or "tidak ada event high-impact terdekat"))

    # ── Konfirmasi ──
    checks.append(Check("Momentum searah",
                        bool(np.sign(comps[1].score) == sign), False, comps[1].note))

    r = float(rsi(ec).iloc[-1])
    checks.append(Check("RSI searah", (r > 50) == (sign > 0), False, f"RSI {r:.0f}"))

    d1 = next((c for c in comps if c.name == "Bias D1"), None)
    checks.append(Check("Bias D1 searah",
                        bool(d1 is not None and np.sign(d1.score) == sign), False,
                        d1.note if d1 else "data D1 tidak tersedia"))

    highs, lows = swing_levels(entry)
    levels = lows if direction == "BUY" else highs
    near = min((abs(px - lv) for lv in levels), default=1e9) / a if a else 1e9
    checks.append(Check("Entry dekat level", near <= 1.2, False,
                        f"{near:.1f} ATR dari "
                        f"{'support' if direction == 'BUY' else 'resistance'} terdekat"))

    checks.append(Check("News tidak melawan",
                        bool((not news_ok) or np.sign(news) != -sign or abs(news) < 20),
                        False, f"skor news {news:+.0f}" if news_ok else "news n/a"))
    return checks


# ──────────────────────────── News & kalender ───────────────────────────────

def fetch_calendar() -> tuple[list[dict], bool]:
    import datafeed
    return datafeed.get_calendar()


def check_blackout(events: list[dict], now: datetime):
    before = timedelta(minutes=NEWS_BLACKOUT_BEFORE_MIN)
    after = timedelta(minutes=NEWS_BLACKOUT_AFTER_MIN)
    upcoming, blocker = [], None
    for e in events:
        d = e["time"] - now                     # positif = belum rilis
        if -after <= d <= timedelta(hours=12):
            upcoming.append(e)
        if e["impact"] != "High" or e["country"] not in ("USD", "ALL"):
            continue
        m = int(d.total_seconds() / 60)
        if timedelta(0) < d <= before:
            blocker = f"{e['title']} (dalam {m} mnt — arah belum diketahui)"
        elif -after <= d <= timedelta(0):
            blocker = f"{e['title']} ({-m} mnt lalu — spread masih lebar)"
    upcoming.sort(key=lambda x: x["time"])
    return blocker, upcoming[:4]


def news_sentiment(events: list[dict]) -> tuple[float, str, bool]:
    base, key = os.getenv("LLM_BASE_URL"), os.getenv("LLM_API_KEY")
    if base and key:
        try:
            lines = [f"- {e['time']:%d %b %H:%M} UTC | {e['country']} | {e['impact']} | "
                     f"{e['title']} (f {e['forecast'] or '-'}, p {e['previous'] or '-'})"
                     for e in events]
            prompt = ("Kamu analis makro emas (XAUUSD). Agenda 12 jam ke depan:\n"
                      + "\n".join(lines) +
                      "\n\nNilai bias jangka pendek HARGA EMAS. USD kuat & yield riil naik "
                      "= emas turun; risk-off & Fed dovish = emas naik.\n"
                      'Balas HANYA JSON: {"score": <int -100..100>, "reason": "<maks 20 kata>"}')
            r = requests.post(f"{base.rstrip('/')}/chat/completions",
                              headers={"Authorization": f"Bearer {key}"},
                              json={"model": os.getenv("LLM_MODEL",
                                                       "anthropic/claude-sonnet-4.5"),
                                    "messages": [{"role": "user", "content": prompt}],
                                    "max_tokens": 200},
                              timeout=45)
            r.raise_for_status()
            t = r.json()["choices"][0]["message"]["content"].strip()
            t = t.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            d = json.loads(t)
            return _clamp(float(d["score"])), str(d["reason"]), True
        except Exception as ex:
            print(f"[warn] sentimen LLM gagal: {ex}", file=sys.stderr)
    high = sum(1 for e in events if e["impact"] == "High")
    if high >= 2:
        return -10.0, f"{high} event high-impact dalam 12 jam, bias defensif", True
    return 0.0, "Sentimen news tidak tersedia — skor murni teknikal", False


# ───────────────────────────── Kalibrasi ────────────────────────────────────

def load_calibration() -> dict:
    try:
        return json.loads(CALIB_FILE.read_text())
    except Exception:
        return {}


def lookup_confidence(calib: dict, grade: str, score: float):
    """
    Confidence = win rate empiris dari backtest untuk bucket ini.
    Kalau sampelnya kurang dari 20, kembalikan None — jangan karang angka.
    """
    for k in (f"{grade}:{int(abs(score) // 10) * 10}", f"{grade}:*"):
        e = calib.get(k)
        if e and e.get("n", 0) >= 20:
            return float(e["win_rate"]), int(e["n"])
    return None, 0


# ───────────────────────── Pembentukan signal ───────────────────────────────

def build_signal(bias, entry, macro, events, now, calib=None,
                 calendar_trusted: bool = True, data_source: str = "") -> Signal:
    calib = calib or {}
    tech, comps = technical_score(bias, entry, macro)
    blackout, upcoming = check_blackout(events, now)

    # Gate keselamatan: tanpa kalender yang terpercaya, kita TIDAK TAHU apakah
    # NFP rilis 5 menit lagi. Diamnya kalender bukan tanda aman — itu buta.
    # Perlakukan sebagai blackout, sama seperti event high-impact sungguhan.
    if not calendar_trusted:
        blackout = ("kalender ekonomi tidak dapat diambil — gate news buta, "
                    "sinyal ditahan demi keamanan")

    news, news_note, news_ok = news_sentiment(upcoming)

    composite = (W_TECH * tech + W_NEWS * news) if news_ok else tech
    px = float(entry["close"].iloc[-1])
    a = float(atr(entry).iloc[-1])

    raw = ("BUY" if composite >= THRESHOLD
           else "SELL" if composite <= -THRESHOLD else "NO-TRADE")

    if raw == "NO-TRADE":
        return Signal(time=now, direction="NO-TRADE", grade="—", composite=composite,
                      tech_score=tech, news_score=news, news_available=news_ok,
                      price=px, atr=a, entry=px, entry_zone=(px, px), stop_loss=px,
                      targets=[], rr=[], components=comps, news_events=upcoming,
                      blackout=blackout, news_note=news_note,
                      calendar_trusted=calendar_trusted, data_source=data_source,
                      invalidation="Skor di bawah ambang, tidak ada setup.")

    sl, tps, rrs = structure_levels(raw, entry, px, a)
    checks = build_checks(raw, entry, comps, news, news_ok, blackout, rrs[0])
    failed = [c for c in checks if c.mandatory and not c.passed]
    confirms = sum(1 for c in checks if not c.mandatory and c.passed)

    # Grade = jumlah konfluensi, TITIK. Jangan gabungkan dengan ambang skor:
    # "5/5 konfirmasi" menuntut entry dekat level (pullback), sementara
    # "skor tinggi" menuntut tren terekstensi. Keduanya bertentangan, dan
    # meng-AND-kannya membuat Grade A mustahil tercapai (terbukti di simulasi:
    # skor tertinggi saat 5/5 konfirmasi hanya 56, tidak pernah menyentuh 60).
    #
    # Skor tetap terukur — lookup_confidence membucketkan per grade DAN per
    # rentang skor, jadi biarkan data backtest yang memutuskan mana yang penting.
    if failed or confirms < MIN_CONFIRMS:
        direction, grade = "NO-TRADE", "—"
    elif confirms == 5:
        direction, grade = raw, "A"
    elif confirms == 4:
        direction, grade = raw, "B"
    else:
        direction, grade = raw, "C"

    conf, conf_n = lookup_confidence(calib, grade, composite) if grade != "—" else (None, 0)

    sign = 1 if raw == "BUY" else -1
    z = (px - sign * 0.25 * a, px + sign * 0.15 * a)
    inval = (f"Batal jika close H1 {'di bawah' if sign > 0 else 'di atas'} "
             f"${sl:,.2f}, atau tren H4 berbalik.")
    if failed:
        inval = "Ditolak syarat wajib: " + "; ".join(c.name for c in failed)
    elif confirms < MIN_CONFIRMS:
        inval = f"Konfirmasi kurang ({confirms}/{MIN_CONFIRMS} minimum)."

    return Signal(time=now, direction=direction, grade=grade, composite=composite,
                  tech_score=tech, news_score=news, news_available=news_ok,
                  price=px, atr=a, entry=px, entry_zone=(min(z), max(z)),
                  stop_loss=sl, targets=tps, rr=rrs, invalidation=inval,
                  components=comps, checks=checks, news_events=upcoming,
                  blackout=blackout, news_note=news_note,
                  calendar_trusted=calendar_trusted, data_source=data_source,
                  confidence=conf, confidence_n=conf_n)


# ──────────────────────────────── Backtest ──────────────────────────────────

def run_backtest(entry, bias, macro, horizon: int = 48) -> dict:
    """
    Jalan mundur di data historis. Untuk tiap sinyal yang lolos gating,
    cek mana yang kena duluan: TP1 atau SL. Hasilnya jadi tabel kalibrasi.
    """
    rows = []
    for i in range(260, len(entry) - horizon):
        e_slice = entry.iloc[:i + 1]
        ts = e_slice.index[-1]
        b_slice = bias[bias.index <= ts]
        m_slice = macro[macro.index <= ts] if macro is not None else None
        if len(b_slice) < 210 or (m_slice is not None and len(m_slice) < 60):
            continue

        sig = build_signal(b_slice, e_slice, m_slice, [], ts.to_pydatetime())
        if sig.direction == "NO-TRADE":
            continue

        tp1, sl = sig.targets[0], sig.stop_loss
        won = None
        for _, bar in entry.iloc[i + 1:i + 1 + horizon].iterrows():
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
        rows.append({"grade": sig.grade,
                     "bucket": int(abs(sig.composite) // 10) * 10, "won": won})

    if not rows:
        return {}

    df = pd.DataFrame(rows)
    calib = {}
    for g, gdf in df.groupby("grade"):
        calib[f"{g}:*"] = {"n": len(gdf),
                           "win_rate": round(gdf["won"].mean() * 100, 1)}
        for bkt, bdf in gdf.groupby("bucket"):
            calib[f"{g}:{bkt}"] = {"n": len(bdf),
                                   "win_rate": round(bdf["won"].mean() * 100, 1)}
    calib["_meta"] = {"generated": datetime.now(timezone.utc).isoformat(),
                      "total_signals": len(df), "horizon_bars": horizon,
                      "overall_win_rate": round(df["won"].mean() * 100, 1)}
    return calib


# ──────────────────────────── Format & kirim ────────────────────────────────

def position_size(sig: Signal):
    bal = os.getenv("ACCOUNT_BALANCE")
    if not bal or sig.direction == "NO-TRADE":
        return None
    try:
        balance, pct = float(bal), float(os.getenv("RISK_PCT", "1"))
    except ValueError:
        return None
    risk = balance * pct / 100
    return f"{risk / (sig.risk_usd * 100):.2f} lot (risiko ${risk:.0f} / {pct}%)"


def plain_reason(sig: Signal) -> str:
    """Rangkum alasan teknikal jadi satu kalimat bahasa manusia."""
    arah = "naik" if sig.direction == "BUY" else "turun"
    bits = []

    trend = next((c for c in sig.components if c.name == "Trend H4"), None)
    d1 = next((c for c in sig.components if c.name == "Bias D1"), None)
    if trend and d1 and np.sign(trend.score) == np.sign(d1.score):
        bits.append(f"tren {arah} kompak di grafik 4 jam dan harian")
    elif trend:
        bits.append(f"tren {arah} di grafik 4 jam")

    passed = {c.name for c in sig.checks if not c.mandatory and c.passed}
    if "Entry dekat level" in passed:
        lvl = "support" if sig.direction == "BUY" else "resistance"
        bits.append(f"harga pas menyentuh {lvl}")
    if "Momentum searah" in passed:
        bits.append("momentum mendukung")
    if sig.news_available and abs(sig.news_score) >= 20 and \
            np.sign(sig.news_score) == (1 if sig.direction == "BUY" else -1):
        bits.append("sentimen berita sejalan")

    return ", ".join(bits[:3]).capitalize() + "."


def format_message_simple(sig: Signal) -> str:
    """
    Versi ringkas: hanya yang perlu dieksekusi.
    Semua diagnostik disembunyikan — sinyal ini sudah lolos seluruh gate,
    jadi tidak ada keputusan tambahan yang perlu diambil pembaca.
    """
    wib = sig.time.astimezone(timezone(timedelta(hours=7)))

    if sig.direction == "NO-TRADE":
        alasan = "Sedang ada rilis berita penting" if sig.blackout \
            else "Belum ada peluang yang memenuhi syarat"
        return (f"⚪ <b>XAUUSD — TIDAK ADA SINYAL</b>\n"
                f"<i>{wib:%d %b %H:%M} WIB</i>\n\n{alasan}. Tidak perlu "
                f"melakukan apa-apa.")

    icon = "🟢" if sig.direction == "BUY" else "🔴"
    aksi = "BELI" if sig.direction == "BUY" else "JUAL"
    arah_kata = "di bawah" if sig.direction == "BUY" else "di atas"

    L = [f"{icon} <b>XAUUSD — {aksi}</b>",
         f"<i>{wib:%d %b %Y %H:%M} WIB</i>", "",
         f"Masuk di  : <b>${sig.entry_zone[0]:,.2f} – ${sig.entry_zone[1]:,.2f}</b>",
         f"Stop loss : <b>${sig.stop_loss:,.2f}</b>"]

    for i, tp in enumerate(sig.targets[:2], 1):
        tail = "  ← tutup separuh di sini" if i == 1 else ""
        L.append(f"Target {i}  : ${tp:,.2f}{tail}")

    if (ps := position_size(sig)):
        L.append(f"Ukuran    : {ps.split(' (')[0]}")
    else:
        L.append("<i>Set ACCOUNT_BALANCE untuk saran ukuran lot</i>")

    L += ["", f"<b>Kenapa</b>: {plain_reason(sig)}", "",
          f"⚠️ Kalau harga tutup {arah_kata} <b>${sig.stop_loss:,.2f}</b>, "
          f"keluar. Jangan ditahan."]

    jejak = f"Grade {sig.grade} · skor {sig.composite:+.0f}"
    if sig.confidence is not None:
        jejak += f" · menang {sig.confidence:.0f}% dari {sig.confidence_n} sinyal serupa"
    else:
        jejak += " · <b>belum teruji</b>"
    L += ["", f"<i>{jejak}</i>",
          "<i>Bukan rekomendasi investasi. Risiko ditanggung sendiri.</i>"]
    return "\n".join(L)


def format_message(sig: Signal) -> str:
    wib = sig.time.astimezone(timezone(timedelta(hours=7)))
    icon = {"BUY": "🟢", "SELL": "🔴", "NO-TRADE": "⚪"}[sig.direction]
    gtag = f" · Grade {sig.grade}" if sig.grade != "—" else ""

    L = [f"{icon} <b>XAUUSD — {sig.direction}</b>{gtag}",
         f"<code>{sig.signal_id()}</code> · <i>{wib:%d %b %Y %H:%M} WIB</i>", ""]

    if sig.blackout:
        L += [f"⛔ <b>BLACKOUT</b> — {sig.blackout}", ""]

    if sig.confidence is not None:
        L.append(f"📊 Win rate historis: <b>{sig.confidence:.0f}%</b> "
                 f"<i>(n={sig.confidence_n} sinyal serupa)</i>")
    else:
        L.append("📊 <i>Belum terkalibrasi — jalankan --backtest. Tanpa itu tidak ada "
                 "dasar menyebut angka confidence.</i>")
    L.append("")

    if sig.direction != "NO-TRADE":
        L += ["<b>━━ RENCANA TRADE ━━</b>",
              f"Entry : <b>${sig.entry_zone[0]:,.2f} – ${sig.entry_zone[1]:,.2f}</b>",
              f"SL    : <b>${sig.stop_loss:,.2f}</b>  (−${sig.risk_usd:.2f})"]
        for i, (tp, rr) in enumerate(zip(sig.targets, sig.rr), 1):
            L.append(f"TP{i}   : ${tp:,.2f}  ({rr:.1f}R)")
        if (ps := position_size(sig)):
            L.append(f"Size  : {ps}")
        L += ["", f"⚠️ <b>Invalidasi</b>: {sig.invalidation}", ""]
    else:
        L += [f"Harga : ${sig.price:,.2f}", f"<i>{sig.invalidation}</i>", ""]

    L += ["<b>━━ ALASAN ━━</b>",
          f"Skor komposit <b>{sig.composite:+.1f}</b> (teknikal {sig.tech_score:+.0f}"
          + (f" · news {sig.news_score:+.0f})" if sig.news_available else " · news n/a)"),
          ""]

    if sig.checks:
        wajib = [c for c in sig.checks if c.mandatory]
        konf = [c for c in sig.checks if not c.mandatory]
        L.append(f"<b>Syarat wajib</b> ({sum(c.passed for c in wajib)}/{len(wajib)})")
        L += [f"{'✅' if c.passed else '❌'} {c.name} — <i>{c.note}</i>" for c in wajib]
        L.append(f"\n<b>Konfirmasi</b> ({sum(c.passed for c in konf)}/{len(konf)}, "
                 f"min {MIN_CONFIRMS})")
        L += [f"{'✅' if c.passed else '➖'} {c.name} — <i>{c.note}</i>" for c in konf]
        L.append("")

    L.append("<b>Komponen teknikal</b>")
    for c in sorted(sig.components, key=lambda x: -abs(x.contribution)):
        m = "▲" if c.score > 10 else "▼" if c.score < -10 else "•"
        L.append(f"{m} {c.name}: {c.score:+.0f} — <i>{c.note}</i>")

    L += ["", f"<b>News</b>: {sig.news_note}"]
    for e in sig.news_events:
        dot = {"High": "🔴", "Medium": "🟠"}.get(e["impact"], "⚪")
        t = e["time"].astimezone(timezone(timedelta(hours=7)))
        L.append(f"{dot} {t:%d/%m %H:%M} {e['country']} — {e['title']}")

    if sig.data_source:
        L.append(f"\n<i>Sumber data: {sig.data_source}</i>")
    L += ["", "<i>Analisis otomatis, bukan rekomendasi investasi. "
              "Verifikasi sendiri sebelum eksekusi.</i>"]
    return "\n".join(L)


def send_telegram(text: str) -> bool:
    import datafeed
    return datafeed.send_telegram(text)


# ──────────────────────── State, log, jam pasar ─────────────────────────────

def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(s: dict) -> None:
    BASE.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(s, indent=2))


# Kolom kanonik signals.csv. TAMBAH kolom baru di sini, jangan pernah lewat
# csv.writer positional — itu penyebab bug #skema-drift: kolom "source"
# ditambah ke data tapi header lama (16 kolom) tidak pernah dimigrasi,
# sehingga pd.read_csv() di journal.py pecah begitu baris campur 16/17 field
# dan journal.py gagal SENYAP (bukan "tidak ada data", tapi crash tertutup).
SIGNAL_COLS = ["time", "id", "direction", "grade", "composite", "tech", "news",
               "price", "entry", "sl", "tp1", "tp2", "tp3", "rr1",
               "confirms", "sent", "source"]


def _migrate_signal_log() -> None:
    """Perbaiki signals.csv kalau header di disk tidak cocok SIGNAL_COLS.

    Baris lama diisi "" untuk kolom yang belum ada saat itu (mis. "source").
    Dijalankan sekali di awal tiap proses, jadi schema drift di masa depan
    juga sembuh sendiri tanpa perlu migrasi manual lagi.
    """
    if not LOG_FILE.exists():
        return
    with LOG_FILE.open(newline="") as f:
        rows = list(csv.reader(f))
    if not rows or rows[0] == SIGNAL_COLS:
        return
    fixed = [SIGNAL_COLS]
    for r in rows[1:]:
        if r == SIGNAL_COLS:      # baris header duplikat dari migrasi lama
            continue
        r = (r + [""] * len(SIGNAL_COLS))[:len(SIGNAL_COLS)]
        fixed.append(r)
    with LOG_FILE.open("w", newline="") as f:
        csv.writer(f).writerows(fixed)
    print(f"[info] signals.csv dimigrasi ke skema {len(SIGNAL_COLS)} kolom "
          f"({len(fixed) - 1} baris)", file=sys.stderr)


def log_signal(sig: Signal, sent: bool) -> None:
    """Catat tiap evaluasi. Ini bahan baku kalibrasi ulang nanti."""
    BASE.mkdir(parents=True, exist_ok=True)
    _migrate_signal_log()
    new = not LOG_FILE.exists()
    tps = (sig.targets + [None, None, None])[:3]
    row = dict(zip(SIGNAL_COLS, [
        sig.time.isoformat(), sig.signal_id(), sig.direction, sig.grade,
        round(sig.composite, 1), round(sig.tech_score, 1),
        round(sig.news_score, 1), round(sig.price, 2), round(sig.entry, 2),
        round(sig.stop_loss, 2),
        *[round(t, 2) if t is not None else "" for t in tps],
        round(sig.rr[0], 2) if sig.rr else "", sig.n_confirms, sent,
        sig.data_source.split(" (")[0]]))
    with LOG_FILE.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SIGNAL_COLS)
        if new:
            w.writeheader()
        w.writerow(row)


def should_send(sig: Signal, state: dict, now: datetime):
    if sig.direction == "NO-TRADE":
        return False, "tidak ada setup yang lolos gating"
    if sig.blackout:
        return False, f"blackout: {sig.blackout[:60]}"
    # Gate kalibrasi: tolak bucket grade/skor yang riwayatnya menang <50%.
    # confidence None berarti sampel <20 (lookup_confidence) — belum cukup
    # data untuk digating, biarkan skor teknikal saja yang bicara.
    if sig.confidence is not None and sig.confidence < 50:
        return False, (f"win rate historis {sig.confidence:.0f}% "
                        f"(n={sig.confidence_n}) di bawah ambang 50%")
    last = state.get("last")
    if last and last.get("direction") == sig.direction:
        age = now - datetime.fromisoformat(last["time"])
        moved = abs(sig.entry - last.get("entry", 0)) / sig.atr if sig.atr else 99
        if age < timedelta(hours=COOLDOWN_HOURS) and moved < 1.0:
            return False, f"duplikat, cooldown {COOLDOWN_HOURS}j"
    return True, "ok"


def market_open(now: datetime) -> bool:
    wd, hr = now.weekday(), now.hour
    if wd == 5:
        return False
    if wd == 4 and hr >= 21:
        return False
    if wd == 6 and hr < 22:
        return False
    return True


# ──────────────────────────────── Main ──────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--backtest", action="store_true", help="bangun calibration.json")
    ap.add_argument("--json", action="store_true", help="cetak payload JSON")
    ap.add_argument("--simple", action="store_true",
                    help="pesan ringkas (bisa juga lewat SIMPLE_MODE=1)")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    # Backtest butuh riwayat panjang → Dukascopy (gratis, tanpa kuota).
    # Live cukup ratusan bar terakhir → Twelve Data (kuota 800/hari cukup).
    prefer = "dukascopy" if args.backtest else None
    macro, _ = get_ohlc(TF_MACRO, args.demo, prefer)
    bias, _ = get_ohlc(TF_BIAS, args.demo, prefer)
    entry, src = get_ohlc(TF_ENTRY, args.demo, prefer)

    if args.backtest:
        print("Menjalankan backtest…")
        calib = run_backtest(entry, bias, macro)
        if not calib:
            print("Tidak ada sinyal terpicu. Longgarkan THRESHOLD/MIN_CONFIRMS "
                  "atau perpanjang rentang data.")
            return 1
        BASE.mkdir(parents=True, exist_ok=True)
        CALIB_FILE.write_text(json.dumps(calib, indent=2))
        m = calib["_meta"]
        print(f"\n{m['total_signals']} sinyal · win rate keseluruhan "
              f"{m['overall_win_rate']}%")
        for k in sorted(k for k in calib if k.endswith(":*")):
            print(f"  Grade {k[0]}: {calib[k]['win_rate']}% (n={calib[k]['n']})")
        print(f"\nTersimpan di {CALIB_FILE}")
        print("Catatan: ini in-sample. Sisihkan periode terpisah untuk uji "
              "out-of-sample sebelum percaya angkanya.")
        return 0

    if not args.demo and not args.force and not market_open(now):
        print("Pasar tutup.")
        return 0

    if args.demo:
        events, cal_ok = [], True
    else:
        events, cal_ok = fetch_calendar()
    sig = build_signal(bias, entry, macro, events, now, load_calibration(),
                       calendar_trusted=cal_ok, data_source=src)

    if args.json:
        print(json.dumps(sig.to_dict(), indent=2, default=str))
        return 0

    simple = args.simple or os.getenv("SIMPLE_MODE", "").strip() in ("1", "true", "yes")
    msg = format_message_simple(sig) if simple else format_message(sig)
    state = load_state()
    ok, reason = should_send(sig, state, now)

    if args.dry_run or args.demo:
        for t in ("<b>", "</b>", "<i>", "</i>", "<code>", "</code>"):
            msg = msg.replace(t, "")
        print(msg)
        print(f"\n--- kirim: {ok} ({reason}) ---")
        return 0

    sent = False
    if ok or args.force:
        sent = send_telegram(msg)
        if sent:
            state["last"] = {"id": sig.signal_id(), "time": now.isoformat(),
                             "direction": sig.direction, "entry": sig.entry}
            save_state(state)
            print(f"Terkirim: {sig.direction} grade {sig.grade} @ {sig.entry:.2f}")
    else:
        print(f"Dilewati: {reason} (skor {sig.composite:+.1f})")

    log_signal(sig, sent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
