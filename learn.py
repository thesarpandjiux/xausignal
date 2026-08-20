#!/usr/bin/env python3
"""
learn.py — alat untuk memperbaiki sistem secara jujur.

Prinsip: sistem ini TIDAK boleh belajar dari hasil live-nya sendiri. Pada
~20 sinyal/bulan, membedakan win rate 55% dari 60% butuh ~13 tahun data.
Menyetel bobot pada 30 trade terakhir bukan pembelajaran, melainkan
mencocokkan diri dengan kebisingan.

Pembelajaran yang sah punya tiga syarat:
  1. Sumbernya data historis panjang (ribuan sinyal), bukan hasil live
  2. Diuji out-of-sample — periode yang belum pernah disentuh saat menyetel
  3. Perubahan diterima hanya bila konsisten lintas periode, bukan rata-rata saja

    python learn.py power                # butuh berapa sampel untuk simpulkan apa
    python learn.py walkforward          # apakah sistem stabil lintas periode
    python learn.py ablation             # komponen mana yang benar-benar berguna
    python learn.py calibration          # apakah angka confidence-nya jujur
"""

from __future__ import annotations

import math
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import xau_signal as x

HORIZON = 48
WARMUP = 260


# ─────────────────────────── Mesin backtest ─────────────────────────────────

def run_trades(entry: pd.DataFrame, bias: pd.DataFrame, macro: pd.DataFrame,
               start: int = WARMUP, stop: int | None = None,
               step: int = 1) -> pd.DataFrame:
    """Jalankan sistem di rentang bar tertentu, kembalikan daftar trade."""
    stop = stop if stop is not None else len(entry) - HORIZON
    rows = []
    for i in range(start, stop, step):
        e = entry.iloc[max(0, i - 399):i + 1]
        ts = e.index[-1]
        b = bias[bias.index <= ts].tail(400)
        m = macro[macro.index <= ts].tail(400)
        if len(b) < 210 or len(m) < 60:
            continue

        sig = x.build_signal(b, e, m, [], ts.to_pydatetime(), {})
        if sig.direction == "NO-TRADE":
            continue

        tp1, sl, rr = sig.targets[0], sig.stop_loss, sig.rr[0]
        won = None
        for _, bar in entry.iloc[i + 1:i + 1 + HORIZON].iterrows():
            hi_tp = bar["high"] >= tp1 if sig.direction == "BUY" else bar["low"] <= tp1
            hi_sl = bar["low"] <= sl if sig.direction == "BUY" else bar["high"] >= sl
            if hi_sl or (hi_tp and hi_sl):      # seri dihitung kalah
                won = False
                break
            if hi_tp:
                won = True
                break
        if won is None:
            continue
        rows.append({"time": ts, "grade": sig.grade, "direction": sig.direction,
                     "composite": sig.composite, "rr": rr,
                     "won": won, "r": rr if won else -1.0})
    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"n": 0, "wr": float("nan"), "exp": float("nan")}
    return {"n": len(df), "wr": df["won"].mean() * 100, "exp": df["r"].mean()}


def load_data(demo: bool = False):
    if demo:
        from frequency import synth_gold, resample
        h1 = synth_gold(400, 5)
        return h1, resample(h1, "4h"), resample(h1, "1D")
    import datafeed
    return (datafeed.get_ohlc("1h", 5000, prefer="dukascopy").df,
            datafeed.get_ohlc("4h", 3000, prefer="dukascopy").df,
            datafeed.get_ohlc("1d", 1500, prefer="dukascopy").df)


# ──────────────────────────── 1. Power analysis ─────────────────────────────

def cmd_power():
    za, zb = 1.96, 0.84

    def need(p1, p2):
        pb = (p1 + p2) / 2
        return math.ceil(2 * (za + zb) ** 2 * pb * (1 - pb) / (p2 - p1) ** 2)

    print("\nBERAPA DATA YANG DIBUTUHKAN UNTUK MENYIMPULKAN SESUATU")
    print("=" * 62)
    print("\nMembedakan dua win rate (alpha 5%, power 80%):\n")
    print(f"{'Pertanyaan':<26}{'total sinyal':>14}{'@20/bln':>12}")
    print("-" * 62)
    for p1, p2, lab in [(.40, .60, "sistem baik vs buruk"),
                        (.45, .60, "jelas lebih baik"),
                        (.50, .60, "cukup lebih baik"),
                        (.55, .60, "sedikit lebih baik"),
                        (.58, .60, "nyaris sama")]:
        tot = need(p1, p2) * 2
        print(f"{lab:<26}{tot:>14}{tot / 20:>9.0f} bln")

    print("\n\nKetidakpastian pada sampel kecil (terukur 60%):\n")
    print(f"{'n':<10}{'sebenarnya bisa jadi':>28}{'kesimpulan':>22}")
    print("-" * 62)
    for n in (20, 30, 50, 100, 300, 1000):
        se = math.sqrt(.6 * .4 / n)
        lo, hi = 60 - 196 * se, 60 + 196 * se
        verdict = ("tak berarti" if lo < 40 else "kabur" if lo < 50
                   else "berguna" if lo < 55 else "meyakinkan")
        print(f"{n:<10}{f'{lo:.0f}% – {hi:.0f}%':>28}{verdict:>22}")

    print("\n" + "-" * 62)
    print("Kesimpulan: hasil LIVE tidak akan pernah cukup untuk menyetel")
    print("parameter. Pakai hasil live untuk MEMVALIDASI, dan data historis")
    print("panjang (Dukascopy, ribuan sinyal) untuk MENYETEL.")
    return 0


# ─────────────────────────── 2. Walk-forward ────────────────────────────────

def cmd_walkforward(demo=False, folds=6):
    """
    Bagi riwayat jadi beberapa periode berurutan, ukur tiap periode terpisah.
    Sistem yang punya edge nyata akan menang di SEBAGIAN BESAR periode.
    Sistem yang cocok-kebetulan akan menang besar di satu periode dan rugi
    di sisanya — rata-ratanya bisa terlihat bagus, tapi itu menipu.
    """
    print("\nWALK-FORWARD — apakah sistem stabil lintas periode?")
    print("=" * 62)
    h1, h4, d1 = load_data(demo)
    print(f"Data: {len(h1)} bar H1 · {h1.index[0]:%b %Y} → {h1.index[-1]:%b %Y}\n")

    span = (len(h1) - WARMUP - HORIZON) // folds
    rows = []
    for k in range(folds):
        a = WARMUP + k * span
        b = a + span
        df = run_trades(h1, h4, d1, a, b, step=2)
        s = summarize(df)
        s["periode"] = f"{h1.index[a]:%b %y}–{h1.index[min(b, len(h1) - 1)]:%b %y}"
        rows.append(s)

    print(f"{'Periode':<18}{'n':>6}{'menang':>9}{'ekspektasi':>13}{'':>6}")
    print("-" * 62)
    for r in rows:
        mark = "✅" if r["exp"] > 0.05 else "❌" if r["exp"] < -0.05 else "➖"
        n, wr, ex = r["n"], r["wr"], r["exp"]
        print(f"{r['periode']:<18}{n:>6}"
              + (f"{wr:>8.0f}%{ex:>+12.2f}R" if n else f"{'—':>9}{'—':>13}")
              + f"{mark:>6}")

    valid = [r for r in rows if r["n"] >= 5]
    if not valid:
        print("\nTerlalu sedikit sinyal untuk disimpulkan.")
        return 1

    pos = sum(1 for r in valid if r["exp"] > 0)
    exps = [r["exp"] for r in valid]
    print("-" * 62)
    print(f"Periode untung : {pos}/{len(valid)}")
    print(f"Ekspektasi     : rata-rata {np.mean(exps):+.2f}R · "
          f"terburuk {min(exps):+.2f}R · simpangan {np.std(exps):.2f}")

    print()
    if pos == len(valid):
        print("✅ Untung di SEMUA periode — tanda edge yang nyata.")
    elif pos >= len(valid) * 0.7:
        print("➖ Untung di mayoritas periode. Cukup menjanjikan, belum kuat.")
    else:
        print("❌ Untung hanya di sebagian periode. Rata-rata positif di sini")
        print("   kemungkinan berasal dari satu periode beruntung, bukan edge.")
    if np.std(exps) > abs(np.mean(exps)):
        print("⚠️  Simpangan melebihi rata-rata — hasilnya sangat bergantung")
        print("   pada regime pasar. Jangan percaya angka rata-ratanya.")
    return 0


# ───────────────────────────── 3. Ablasi ────────────────────────────────────

def cmd_ablation(demo=False):
    """
    Matikan satu komponen, ukur dampaknya. Komponen yang dihapus tanpa
    membuat hasil memburuk berarti tidak menghasilkan apa-apa — hanya
    menambah parameter, dan tiap parameter tambahan memperbesar risiko
    sistem tercocok pada kebisingan.

    Menghapus lebih aman daripada menambah.
    """
    print("\nABLASI — komponen mana yang benar-benar berguna?")
    print("=" * 62)
    h1, h4, d1 = load_data(demo)

    base_df = run_trades(h1, h4, d1, step=3)
    base = summarize(base_df)
    if base["n"] < 20:
        print(f"Hanya {base['n']} sinyal — terlalu sedikit. Perpanjang data.")
        return 1
    print(f"Baseline: {base['n']} sinyal · menang {base['wr']:.0f}% · "
          f"ekspektasi {base['exp']:+.2f}R\n")

    names = ["Trend H4", "Momentum H1", "RSI H1", "Posisi range",
             "Bias D1", "Volatilitas"]
    print(f"{'Tanpa komponen':<18}{'n':>6}{'menang':>9}{'ekspektasi':>12}"
          f"{'selisih':>10}")
    print("-" * 62)

    results = []
    for nm in names:
        x.WEIGHT_OVERRIDE = {nm: 0.0}
        s = summarize(run_trades(h1, h4, d1, step=3))
        x.WEIGHT_OVERRIDE = {}
        delta = s["exp"] - base["exp"] if s["n"] else float("nan")
        results.append((nm, s, delta))
        if s["n"]:
            print(f"{nm:<18}{s['n']:>6}{s['wr']:>8.0f}%{s['exp']:>+11.2f}R"
                  f"{delta:>+9.2f}R")
        else:
            print(f"{nm:<18}{'—':>6}{'—':>9}{'—':>12}{'—':>10}")

    print("-" * 62)
    print("Selisih NEGATIF = komponen itu berguna (dihapus bikin memburuk)")
    print("Selisih POSITIF = komponen itu MERUGIKAN (dihapus bikin membaik)")

    # Jebakan metodologi: mematikan komponen mengubah sinyal MANA yang lolos,
    # bukan cuma skornya. Kalau n berubah drastis, kita membandingkan dua
    # sistem dengan selektivitas berbeda — bukan efek komponen itu sendiri.
    skewed = [(n, s["n"]) for n, s, _ in results
              if s["n"] and abs(s["n"] - base["n"]) / base["n"] > 0.4]
    if skewed:
        print("\n⚠️  POPULASI BERUBAH DRASTIS — selisih di bawah ini tidak bisa")
        print("    dibaca sebagai 'efek komponen':")
        for n, cnt in skewed:
            arah = "lebih selektif" if cnt < base["n"] else "lebih longgar"
            print(f"      {n}: {base['n']} → {cnt} sinyal ({arah})")
        print("    Sistem yang lebih selektif hampir selalu punya ekspektasi")
        print("    lebih tinggi dengan trade lebih sedikit. Itu bukan bukti")
        print("    komponennya merugikan.")

    print()
    valid = [(n, s, d) for n, s, d in results
             if s["n"] and abs(s["n"] - base["n"]) / base["n"] <= 0.4]
    if not valid:
        print("Tidak ada komponen yang bisa dinilai adil — semuanya mengubah")
        print("populasi sinyal terlalu banyak. Ablasi tidak cocok di sini.")
        return 0

    useful = [(n, d) for n, _, d in valid if d < -0.03]
    harmful = [(n, d) for n, _, d in valid if d > 0.03]
    neutral = [n for n, _, d in valid if abs(d) <= 0.03]

    if useful:
        print("✅ Berguna : " + ", ".join(f"{n} ({d:+.2f}R)" for n, d in useful))
    if neutral:
        print("➖ Netral  : " + ", ".join(neutral))
        print("   Pertimbangkan hapus — parameter yang tidak menghasilkan apa-apa")
        print("   hanya memperbesar peluang sistem tercocok pada kebisingan.")
    if harmful:
        print("❌ Merugikan: " + ", ".join(f"{n} ({d:+.2f}R)" for n, d in harmful))
        print("   Periksa: mungkin asumsinya bertentangan dengan komponen lain.")

    print("\n⚠️  Ini in-sample. Sebelum menghapus apa pun, konfirmasi dengan")
    print("   'python learn.py walkforward' setelah perubahan.")
    return 0


# ──────────────────────────── 4. Kalibrasi ──────────────────────────────────

def cmd_calibration(demo=False):
    """
    Apakah angka confidence-nya jujur? Kalau sistem bilang 60%, apakah
    benar-benar menang 60%? Ini bentuk pembelajaran paling aman: menyempurnakan
    perkiraan keyakinan TANPA mengubah aturan keputusan.
    """
    print("\nKALIBRASI — apakah angka confidence-nya jujur?")
    print("=" * 62)
    h1, h4, d1 = load_data(demo)
    df = run_trades(h1, h4, d1, step=2)
    if len(df) < 40:
        print(f"Hanya {len(df)} sinyal — terlalu sedikit.")
        return 1

    print(f"{len(df)} sinyal\n")
    print(f"{'Kelompok skor':<18}{'n':>6}{'menang':>9}{'ekspektasi':>13}")
    print("-" * 62)
    df["bucket"] = (df["composite"].abs() // 10 * 10).astype(int)
    for bkt, sub in df.groupby("bucket"):
        if len(sub) >= 10:
            print(f"skor {bkt}–{bkt + 9:<12}{len(sub):>6}"
                  f"{sub['won'].mean() * 100:>8.0f}%{sub['r'].mean():>+12.2f}R")

    print(f"\n{'Grade':<18}{'n':>6}{'menang':>9}{'ekspektasi':>13}")
    print("-" * 62)
    order = []
    for g in ("A", "B", "C"):
        sub = df[df["grade"] == g]
        if len(sub) >= 10:
            print(f"{g:<18}{len(sub):>6}{sub['won'].mean() * 100:>8.0f}%"
                  f"{sub['r'].mean():>+12.2f}R")
            order.append((g, sub["won"].mean()))

    if len(order) >= 2:
        print()
        if all(order[i][1] >= order[i + 1][1] for i in range(len(order) - 1)):
            print("✅ Urutan A > B > C sesuai harapan — grade bermakna.")
        else:
            best = max(order, key=lambda t: t[1])[0]
            print(f"❌ Urutan TIDAK sesuai harapan. Grade {best} justru terbaik.")
            print("   Ikuti data, bukan hurufnya. Sesuaikan ukuran posisi.")

    # Brier score: 0 = sempurna, 0.25 = setara menebak acak
    p = df["won"].mean()
    brier = ((df["won"].astype(float) - p) ** 2).mean()
    print(f"\nBrier score (tebakan tetap {p * 100:.0f}%): {brier:.3f}")
    print("  0.00 = sempurna · 0.25 = setara melempar koin")
    return 0


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "power"
    demo = "--demo" in sys.argv
    fn = {"power": lambda: cmd_power(),
          "walkforward": lambda: cmd_walkforward(demo),
          "ablation": lambda: cmd_ablation(demo),
          "calibration": lambda: cmd_calibration(demo)}.get(cmd)
    if not fn:
        print(__doc__)
        return 1
    return fn()


if __name__ == "__main__":
    sys.exit(main())
