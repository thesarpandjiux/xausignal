#!/usr/bin/env python3
"""
datafeed.py — lapis data XAUUSD, semua sumber gratis.

Rantai sumber harga (otomatis mundur kalau gagal):
    1. Twelve Data   — free 800 req/hari, butuh API key gratis
    2. Dukascopy     — tanpa key, tanpa registrasi, riwayat bertahun-tahun
    3. yfinance GC=F — cadangan terakhir, rawan rate-limit

Kalender: ForexFactory JSON, di-cache 6 jam karena dibatasi 2 unduhan / 5 menit.

Semua respons di-cache ke disk. Kalau sumber gagal, cache basi tetap dipakai
dan ditandai — lebih baik data telat daripada gate news mati diam-diam.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

BASE = Path(os.getenv("XAU_HOME", "~/.xau_signal")).expanduser()


def load_env() -> None:
    """
    Muat variabel dari berkas .env.

    Kenapa perlu: cron TIDAK membaca ~/.zshrc atau ~/.bashrc — environment-nya
    nyaris kosong. Tanpa ini, bot jalan mulus di terminal lalu gagal senyap
    saat dijadwalkan, dengan gejala yang membingungkan.

    Urutan pencarian: ./.env → ~/.xau_signal/.env → ~/.env
    Variabel yang SUDAH ada di environment tidak ditimpa.
    """
    for cand in (Path.cwd() / ".env", BASE / ".env", Path.home() / ".env"):
        if not cand.is_file():
            continue
        try:
            for line in cand.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                line = line.removeprefix("export ").strip()
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                v = v.split(" #")[0].strip()
                if k and k not in os.environ:
                    os.environ[k] = v
        except Exception as e:
            print(f"[warn] gagal baca {cand}: {e}", file=sys.stderr)
        return


load_env()
CACHE = BASE / "cache"
FF_URLS = ["https://nfs.faireconomy.media/ff_calendar_thisweek.json",
           "https://cdn-nfs.faireconomy.media/ff_calendar_thisweek.json"]

# TTL cache per interval (detik). Bar H1 baru tiap jam, tidak perlu ditarik tiap menit.
OHLC_TTL = {"1h": 900, "4h": 3600, "1d": 21600}
CALENDAR_TTL = 6 * 3600          # 6 jam — jauh di bawah limit 2 per 5 menit
OHLC_COLS = ["open", "high", "low", "close"]


@dataclass
class Feed:
    """Hasil pengambilan data, lengkap dengan asal-usulnya."""
    df: pd.DataFrame
    source: str
    stale: bool = False
    age_s: float = 0.0

    def label(self) -> str:
        if not self.stale:
            return self.source
        return f"{self.source} (cache basi, {self.age_s / 60:.0f} mnt)"


# ────────────────────────────── Cache disk ──────────────────────────────────

def _cache_path(key: str, ext: str) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    return CACHE / f"{key}.{ext}"


def _read_cache(key: str, ttl: int, ext: str = "csv"):
    """Kembalikan (data, umur_detik, masih_segar) atau (None, 0, False)."""
    p = _cache_path(key, ext)
    if not p.exists():
        return None, 0.0, False
    age = time.time() - p.stat().st_mtime
    try:
        if ext == "csv":
            df = pd.read_csv(p, index_col=0, parse_dates=True)
            df.index = pd.to_datetime(df.index, utc=True)
            data = df
        else:
            data = json.loads(p.read_text())
    except Exception:
        return None, 0.0, False
    return data, age, age < ttl


def _write_cache(key: str, data, ext: str = "csv") -> None:
    p = _cache_path(key, ext)
    try:
        if ext == "csv":
            data.to_csv(p)
        else:
            p.write_text(json.dumps(data, default=str))
    except Exception as e:
        print(f"[warn] gagal tulis cache {key}: {e}", file=sys.stderr)


# ─────────────────────────── Sumber harga ───────────────────────────────────

def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]
    missing = [c for c in OHLC_COLS if c not in df.columns]
    if missing:
        raise RuntimeError(f"kolom hilang: {missing}")
    df = df[OHLC_COLS].astype(float)
    df.index = pd.to_datetime(df.index, utc=True)
    return df[~df.index.duplicated(keep="last")].sort_index().dropna()


def from_twelvedata(interval: str, bars: int) -> pd.DataFrame:
    key = os.getenv("TWELVEDATA_API_KEY")
    if not key:
        raise RuntimeError("TWELVEDATA_API_KEY tidak diset")
    r = requests.get("https://api.twelvedata.com/time_series",
                     params={"symbol": "XAU/USD", "interval": interval,
                             "outputsize": min(bars, 5000), "apikey": key,
                             "format": "JSON"},
                     timeout=25)
    r.raise_for_status()
    p = r.json()
    if p.get("status") == "error":
        # kode 429 = kuota harian habis; biar rantai fallback yang menangani
        raise RuntimeError(f"TwelveData {p.get('code', '')}: {p.get('message')}")
    df = pd.DataFrame(p["values"])
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    return _normalize(df.set_index("datetime"))


def from_dukascopy(interval: str, bars: int) -> pd.DataFrame:
    """Tanpa API key, tanpa registrasi. Sumber terbaik untuk riwayat panjang."""
    import dukascopy_python
    from dukascopy_python.instruments import INSTRUMENT_FX_METALS_XAU_USD

    iv = {"1h": dukascopy_python.INTERVAL_HOUR_1,
          "4h": dukascopy_python.INTERVAL_HOUR_4,
          "1d": dukascopy_python.INTERVAL_DAY_1}[interval]
    hours = {"1h": 1, "4h": 4, "1d": 24}[interval]
    # dilebihkan 2.2× karena akhir pekan tidak ada bar
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours * bars * 2.2)

    df = dukascopy_python.fetch(INSTRUMENT_FX_METALS_XAU_USD, iv,
                                dukascopy_python.OFFER_SIDE_BID, start, end)
    if df is None or df.empty:
        raise RuntimeError("Dukascopy mengembalikan data kosong")
    return _normalize(df).tail(bars)


def from_yfinance(interval: str, bars: int) -> pd.DataFrame:
    """Cadangan terakhir. Endpoint tidak resmi, rawan HTTP 429."""
    import yfinance as yf
    period = {"1h": "60d", "4h": "180d", "1d": "3y"}[interval]
    df = yf.Ticker("GC=F").history(period=period,
                                   interval="1h" if interval == "4h" else interval)
    if df.empty:
        raise RuntimeError("yfinance kosong (kemungkinan rate-limited)")
    df = _normalize(df)
    if interval == "4h":
        df = df.resample("4h").agg({"open": "first", "high": "max",
                                    "low": "min", "close": "last"}).dropna()
    return df.tail(bars)


SOURCES = [("twelvedata", from_twelvedata),
           ("dukascopy", from_dukascopy),
           ("yfinance", from_yfinance)]


def get_ohlc(interval: str, bars: int = 400, prefer: str | None = None,
             use_cache: bool = True) -> Feed:
    """
    Ambil OHLC dengan cache dan fallback berjenjang.
    prefer='dukascopy' untuk backtest (riwayat panjang, tanpa kuota).
    """
    # Kunci cache TIDAK menyertakan `bars`: menyimpan 400 bar lalu meminta 200
    # seharusnya kena cache, bukan menembak API lagi dan membakar kuota.
    ckey = f"ohlc_{interval}"
    ttl = OHLC_TTL.get(interval, 900)

    if use_cache:
        df, age, fresh = _read_cache(ckey, ttl)
        if fresh and df is not None and len(df) >= bars:
            return Feed(df.tail(bars), "cache", stale=False, age_s=age)

    order = SOURCES
    if prefer:
        order = ([s for s in SOURCES if s[0] == prefer]
                 + [s for s in SOURCES if s[0] != prefer])

    errors = []
    for name, fn in order:
        try:
            df = fn(interval, bars)
            if len(df) < 60:
                raise RuntimeError(f"hanya {len(df)} bar, terlalu sedikit")
            _write_cache(ckey, df)
            return Feed(df, name)
        except Exception as e:
            errors.append(f"{name}: {e}")
            continue

    # Semua sumber gagal → pakai cache basi kalau ada, dan tandai jelas
    df, age, _ = _read_cache(ckey, ttl)
    if df is not None and len(df) >= 60:
        print(f"[warn] semua sumber gagal, pakai cache basi ({age / 60:.0f} mnt)",
              file=sys.stderr)
        return Feed(df.tail(bars), "cache", stale=True, age_s=age)

    raise RuntimeError("Semua sumber harga gagal:\n  " + "\n  ".join(errors))


# ──────────────────────────── Kalender ekonomi ──────────────────────────────

def get_calendar(use_cache: bool = True) -> tuple[list[dict], bool]:
    """
    Kembalikan (events, terpercaya).

    terpercaya=False berarti kalender tidak bisa diambil DAN cache sudah basi.
    Pemanggil WAJIB memperlakukan ini sebagai alasan menahan sinyal — tanpa
    kalender, gate blackout news tidak berfungsi dan bot bisa mengirim sinyal
    lima menit sebelum NFP.
    """
    raw, age, fresh = _read_cache("calendar", CALENDAR_TTL, ext="json") \
        if use_cache else (None, 0.0, False)

    if not fresh:
        for url in FF_URLS:
            try:
                r = requests.get(url, timeout=15,
                                 headers={"User-Agent": "Mozilla/5.0"})
                # Saat limit terlampaui, FF membalas HALAMAN HTML, bukan JSON,
                # dengan status 200. Tanpa cek ini, r.json() melempar error dan
                # kalender diam-diam jadi kosong.
                ctype = r.headers.get("content-type", "")
                if r.status_code != 200 or "json" not in ctype.lower():
                    raise RuntimeError(
                        f"balasan bukan JSON (HTTP {r.status_code}, {ctype}) "
                        "— kemungkinan kena limit unduhan")
                data = r.json()
                if not isinstance(data, list) or not data:
                    raise RuntimeError("payload kalender kosong")
                _write_cache("calendar", data, ext="json")
                raw, age = data, 0.0
                break
            except Exception as e:
                print(f"[warn] kalender {url}: {e}", file=sys.stderr)
        else:
            if raw is None:
                print("[warn] kalender tidak tersedia dan tidak ada cache",
                      file=sys.stderr)
                return [], False

    events = []
    for e in raw or []:
        try:
            when = pd.to_datetime(e["date"], utc=True)
        except Exception:
            continue
        events.append({"title": e.get("title", ""), "country": e.get("country", ""),
                       "impact": e.get("impact", ""), "time": when,
                       "forecast": e.get("forecast", ""),
                       "previous": e.get("previous", "")})

    # Kalender mingguan; kalau umurnya lewat sepekan, isinya sudah kedaluwarsa.
    trusted = bool(events) and age < 7 * 24 * 3600
    return events, trusted


# ─────────────────────────────── Telegram ───────────────────────────────────

def send_telegram(text: str, retries: int = 3) -> bool:
    """Gratis tanpa batas praktis. Retry karena jaringan rumahan sering putus."""
    tok, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        print("[warn] kredensial Telegram kosong", file=sys.stderr)
        return False

    for attempt in range(retries):
        try:
            r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                              json={"chat_id": chat, "text": text[:4096],
                                    "parse_mode": "HTML",
                                    "disable_web_page_preview": True},
                              timeout=20)
            if r.ok:
                return True
            if r.status_code == 429:      # Telegram menyuruh menunggu
                wait = r.json().get("parameters", {}).get("retry_after", 5)
                time.sleep(min(wait, 30))
                continue
            print(f"[error] Telegram {r.status_code}: {r.text[:200]}",
                  file=sys.stderr)
            return False
        except requests.RequestException as e:
            print(f"[warn] Telegram percobaan {attempt + 1}: {e}", file=sys.stderr)
            time.sleep(2 ** attempt)
    return False


# ─────────────────────────────── Diagnostik ─────────────────────────────────

def selftest() -> int:
    """Cek sumber mana yang hidup. Jalankan: python datafeed.py"""
    print("Cek konfigurasi & sumber data XAUUSD\n" + "─" * 52)

    # Kredensial dulu — ini penyebab kegagalan paling umum
    found = None
    for cand in (Path.cwd() / ".env", BASE / ".env", Path.home() / ".env"):
        if cand.is_file():
            found = cand
            break
    print(f"  {'📄' if found else '⚠️ '} berkas .env   "
          f"{found if found else 'tidak ditemukan — pakai environment shell'}")

    for k, wajib in (("TELEGRAM_BOT_TOKEN", True), ("TELEGRAM_CHAT_ID", True),
                     ("TWELVEDATA_API_KEY", False), ("LLM_BASE_URL", False)):
        v = os.getenv(k, "")
        if v:
            print(f"  ✅ {k:<20} {v[:8]}…{v[-4:] if len(v) > 12 else ''}")
        else:
            print(f"  {'❌' if wajib else '➖'} {k:<20} "
                  f"{'BELUM DISET (wajib)' if wajib else 'kosong (opsional)'}")
    print("─" * 52)

    ok = 0
    for name, fn in SOURCES:
        t0 = time.time()
        try:
            df = fn("1h", 100)
            print(f"  ✅ {name:12} {len(df):4d} bar · terakhir "
                  f"{df.index[-1]:%Y-%m-%d %H:%M} · ${df['close'].iloc[-1]:,.2f} "
                  f"· {time.time() - t0:.1f}s")
            ok += 1
        except Exception as e:
            print(f"  ❌ {name:12} {str(e)[:60]}")

    ev, trusted = get_calendar()
    high = sum(1 for e in ev if e["impact"] == "High")
    print(f"  {'✅' if trusted else '❌'} {'kalender':12} {len(ev)} event "
          f"({high} high-impact) · terpercaya={trusted}")

    # Uji kirim sungguhan — satu-satunya cara tahu Telegram benar-benar jalan
    if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
        sent = send_telegram("✅ <b>Uji koneksi berhasil</b>\n"
                             "<i>XAUUSD Signal Bot siap.</i>")
        print(f"  {'✅' if sent else '❌'} {'telegram':12} "
              f"{'pesan uji terkirim — cek Telegram Anda' if sent else 'GAGAL kirim, lihat error di atas'}")
    else:
        print(f"  ❌ {'telegram':12} kredensial belum lengkap")

    print("─" * 52)
    print(f"{ok}/{len(SOURCES)} sumber harga hidup.")
    if not found and not os.getenv("TELEGRAM_BOT_TOKEN"):
        print("\n💡 Buat berkas .env di folder ini:")
        print("   cp .env.example .env    lalu isi token Anda")
        print("   Cara ini bekerja di zsh, bash, MAUPUN cron.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(selftest())
