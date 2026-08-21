#!/usr/bin/env python3
"""
journal.py — catat hasil nyata tiap sinyal dan hitung statistiknya.

Ini alat inti fase observasi. Tanpa ini, dua bulan paper trading hanya
menghasilkan daftar sinyal tanpa jawaban atas satu-satunya pertanyaan yang
penting: berapa yang benar?

    python journal.py update     # tentukan hasil sinyal yang sudah selesai
    python journal.py report     # tampilkan statistik
    python journal.py export     # gabungkan jadi journal.csv untuk Excel

Alur: xau_signal.py menulis signals.csv → journal.py update mengambil harga
setelah sinyal → tentukan TP1 atau SL yang kena duluan → tulis journal.csv.
"""

from __future__ import annotations

import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

import datafeed  # noqa: F401  — memuat .env

BASE = Path(os.getenv("XAU_HOME", "~/.xau_signal")).expanduser()
SIGNALS = BASE / "signals.csv"
JOURNAL = BASE / "journal.csv"
HORIZON_H = 48          # batas waktu: kalau 48 jam belum kena apa-apa, dianggap batal

JOURNAL_COLS = ["id", "time", "direction", "grade", "composite", "entry", "sl",
                "tp1", "rr1", "outcome", "r_result", "closed_at", "note"]


def load_signals() -> pd.DataFrame:
    if not SIGNALS.exists():
        print(f"Belum ada {SIGNALS}. Jalankan xau_signal.py dulu.")
        return pd.DataFrame()
    df = pd.read_csv(SIGNALS)
    df = df[(df["direction"] != "NO-TRADE") & (df["sent"].astype(str) == "True")]
    if df.empty:
        return df
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.drop_duplicates(subset=["id"], keep="first").sort_values("time")


def load_journal() -> dict:
    if not JOURNAL.exists():
        return {}
    out = {}
    with JOURNAL.open() as f:
        for row in csv.DictReader(f):
            out[row["id"]] = row
    return out


def save_journal(rows: dict) -> None:
    BASE.mkdir(parents=True, exist_ok=True)
    with JOURNAL.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=JOURNAL_COLS)
        w.writeheader()
        for r in sorted(rows.values(), key=lambda x: x["time"]):
            w.writerow({k: r.get(k, "") for k in JOURNAL_COLS})


def resolve(sig: pd.Series, price: pd.DataFrame) -> tuple[str, float, str, str]:
    """
    Tentukan hasil satu sinyal. Return (outcome, hasil_R, waktu_tutup, catatan).

    Aturan konservatif: kalau dalam SATU bar harga menyentuh TP1 dan SL
    sekaligus, dihitung KALAH. Data H1 tidak memberi tahu mana yang lebih dulu,
    dan menebak ke arah yang menguntungkan akan membuat statistik terlalu manis.
    """
    t0 = sig["time"]
    fwd = price[(price.index > t0) & (price.index <= t0 + timedelta(hours=HORIZON_H))]
    if fwd.empty:
        return "PENDING", 0.0, "", "belum ada data setelah sinyal"

    entry, sl, tp1 = float(sig["entry"]), float(sig["sl"]), float(sig["tp1"])
    rr1 = float(sig["rr1"]) if str(sig["rr1"]).strip() else 1.5
    buy = sig["direction"] == "BUY"

    for ts, bar in fwd.iterrows():
        hit_tp = bar["high"] >= tp1 if buy else bar["low"] <= tp1
        hit_sl = bar["low"] <= sl if buy else bar["high"] >= sl
        if hit_tp and hit_sl:
            return "LOSS", -1.0, ts.isoformat(), "TP & SL kena di bar sama — dihitung kalah"
        if hit_sl:
            return "LOSS", -1.0, ts.isoformat(), ""
        if hit_tp:
            return "WIN", rr1, ts.isoformat(), ""

    # Belum kena apa-apa sampai batas waktu
    last = float(fwd["close"].iloc[-1])
    risk = abs(entry - sl)
    r = ((last - entry) if buy else (entry - last)) / risk if risk else 0.0
    if len(fwd) < HORIZON_H * 0.5:
        return "PENDING", 0.0, "", f"baru {len(fwd)} jam berjalan"
    return "TIMEOUT", round(r, 2), fwd.index[-1].isoformat(), f"{HORIZON_H}j tanpa TP/SL"


def cmd_update() -> int:
    sigs = load_signals()
    if sigs.empty:
        print("Tidak ada sinyal terkirim untuk dinilai.")
        return 0

    jr = load_journal()
    todo = [s for _, s in sigs.iterrows()
            if s["id"] not in jr or jr[s["id"]]["outcome"] == "PENDING"]
    if not todo:
        print(f"Semua {len(sigs)} sinyal sudah dinilai. Tidak ada yang baru.")
        return 0

    oldest = min(s["time"] for s in todo)
    need_h = (datetime.now(timezone.utc) - oldest).total_seconds() / 3600 + HORIZON_H
    bars = min(int(need_h * 1.6) + 100, 5000)
    print(f"{len(todo)} sinyal perlu dinilai · mengambil ~{bars} bar H1…")

    # Sinyal dari sumber berbeda TIDAK boleh dinilai bersama: yfinance memakai
    # futures GC=F yang berpremi ~$54 terhadap spot. Menilai sinyal spot dengan
    # harga futures membuat setiap BUY tercatat menang seketika.
    srcs = {str(s.get("source", "")).strip() for s in todo if str(s.get("source", "")).strip()}
    if len(srcs) > 1:
        print(f"⚠️  Sinyal berasal dari sumber berbeda: {', '.join(sorted(srcs))}")
        print("   Harga antar sumber tidak sebanding (spot vs futures).")
        print("   Nilai terpisah per sumber, atau abaikan sinyal lama.")
        return 1

    try:
        import datafeed
        want = next(iter(srcs)) if srcs else "dukascopy"
        feed = datafeed.get_ohlc("1h", bars, prefer=want)
        if srcs and feed.source not in (want, "cache"):
            print(f"❌ Sinyal dibuat dari '{want}' tapi hanya '{feed.source}' "
                  f"yang tersedia sekarang.")
            print("   Harga tidak sebanding — penilaian dibatalkan.")
            return 1
        price = feed.df
        print(f"  sumber: {feed.label()} · {len(price)} bar · "
              f"{price.index[0]:%d %b} → {price.index[-1]:%d %b}")
    except Exception as e:
        print(f"Gagal mengambil harga: {e}")
        return 1

    counts = defaultdict(int)
    for s in todo:
        if s["time"] < price.index[0]:
            outcome, r, closed, note = "NO-DATA", 0.0, "", "sinyal lebih tua dari data"
        else:
            outcome, r, closed, note = resolve(s, price)
        counts[outcome] += 1
        jr[s["id"]] = {"id": s["id"], "time": s["time"].isoformat(),
                       "direction": s["direction"], "grade": s["grade"],
                       "composite": s["composite"], "entry": s["entry"],
                       "sl": s["sl"], "tp1": s["tp1"], "rr1": s["rr1"],
                       "outcome": outcome, "r_result": r,
                       "closed_at": closed, "note": note}

    save_journal(jr)
    print("  " + " · ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    print(f"Tersimpan di {JOURNAL}")
    return 0


def cmd_report() -> int:
    jr = load_journal()
    if not jr:
        print("Jurnal kosong. Jalankan: python journal.py update")
        return 1

    rows = [r for r in jr.values() if r["outcome"] in ("WIN", "LOSS", "TIMEOUT")]
    if not rows:
        pend = sum(1 for r in jr.values() if r["outcome"] == "PENDING")
        print(f"Belum ada sinyal yang selesai ({pend} masih berjalan).")
        return 0

    df = pd.DataFrame(rows)
    df["r_result"] = pd.to_numeric(df["r_result"], errors="coerce").fillna(0)
    decided = df[df["outcome"].isin(["WIN", "LOSS"])]

    print("═" * 56)
    print("  HASIL NYATA SINYAL XAUUSD")
    print("═" * 56)
    span = ""
    if len(df):
        t = pd.to_datetime(df["time"], utc=True, format="mixed")
        span = f"{t.min():%d %b %Y} → {t.max():%d %b %Y}"
    print(f"Periode      : {span}")
    print(f"Total selesai: {len(df)}  (menang/kalah tegas: {len(decided)}, "
          f"timeout: {(df['outcome'] == 'TIMEOUT').sum()})")

    if len(decided) == 0:
        print("\nBelum ada yang kena TP/SL. Tunggu lebih lama.")
        return 0

    wr = (decided["outcome"] == "WIN").mean() * 100
    exp = decided["r_result"].mean()
    print(f"\nWin rate     : {wr:.1f}%")
    print(f"Ekspektasi   : {exp:+.3f}R per trade")
    print(f"Total        : {df['r_result'].sum():+.1f}R")

    # Win rate impas: berapa persen yang DIBUTUHKAN agar tidak rugi
    rr = pd.to_numeric(decided["rr1"], errors="coerce").fillna(1.5).mean()
    be = 100 / (1 + rr)
    print(f"\nR:R rata-rata: {rr:.2f}  → butuh menang {be:.1f}% untuk impas")
    margin = wr - be
    verdict = ("✅ di atas titik impas" if margin > 5 else
               "⚠️  tipis, belum meyakinkan" if margin > -5 else
               "❌ di bawah titik impas")
    print(f"Selisih      : {margin:+.1f} poin  {verdict}")

    print(f"\n{'Grade':<8}{'n':>5}{'menang':>9}{'ekspektasi':>13}")
    print("─" * 56)
    for g in ("A", "B", "C"):
        sub = decided[decided["grade"] == g]
        if len(sub):
            print(f"{g:<8}{len(sub):>5}{(sub['outcome'] == 'WIN').mean() * 100:>8.0f}%"
                  f"{sub['r_result'].mean():>+12.2f}R")

    print(f"\n{'Arah':<8}{'n':>5}{'menang':>9}{'ekspektasi':>13}")
    print("─" * 56)
    for d in ("BUY", "SELL"):
        sub = decided[decided["direction"] == d]
        if len(sub):
            print(f"{d:<8}{len(sub):>5}{(sub['outcome'] == 'WIN').mean() * 100:>8.0f}%"
                  f"{sub['r_result'].mean():>+12.2f}R")

    print("\n" + "─" * 56)
    n = len(decided)
    if n < 30:
        print(f"⚠️  Baru {n} sinyal. Di bawah 30, angka di atas masih kebisingan —")
        print("    beruntun 5 kali menang itu wajar bahkan pada sistem yang buruk.")
        print(f"    Lanjutkan observasi sampai minimal 30 (kurang {30 - n} lagi).")
    elif n < 100:
        print(f"{n} sinyal — cukup untuk gambaran kasar, belum untuk keyakinan.")
        print("Perbedaan antar grade di bawah 100 sampel belum bisa dipercaya.")
    else:
        print(f"{n} sinyal — sampel memadai untuk keputusan.")
    return 0


def cmd_export() -> int:
    jr = load_journal()
    if not jr:
        print("Jurnal kosong.")
        return 1
    out = BASE / "journal_export.csv"
    pd.DataFrame(list(jr.values())).to_csv(out, index=False)
    print(f"{len(jr)} baris → {out}")
    return 0


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    return {"update": cmd_update, "report": cmd_report,
            "export": cmd_export}.get(cmd, cmd_report)()


if __name__ == "__main__":
    sys.exit(main())
