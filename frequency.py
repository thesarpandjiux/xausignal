#!/usr/bin/env python3
"""
frequency.py — hitung berapa sinyal terkirim per hari, dan di mana saja
kandidat tersaring.

Berjalan di data sintetis dengan regime bergantian (trending / ranging),
karena random walk murni meremehkan persistensi tren dan akan membuat
estimasi frekuensi meleset.
"""
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

os.environ.setdefault("XAU_HOME", "/tmp/freq")
import xau_signal as x


def synth_gold(days: int, seed: int, regime_len: int = 72) -> pd.DataFrame:
    """
    H1 dengan regime bergantian. ATR emas ±0.15% harga per jam.
    Tren bergantian arah tiap ~3 hari agar tidak searah selamanya.
    """
    rng = np.random.default_rng(seed)
    n = days * 24
    price, out = 3400.0, []
    i = 0
    while i < n:
        L = min(regime_len + int(rng.normal(0, 20)), n - i)
        if L <= 0:
            break
        trending = rng.random() < 0.45
        drift = rng.choice([-1, 1]) * rng.uniform(0.8, 2.2) if trending else 0.0
        vol = price * (0.0015 if trending else 0.0011)
        for _ in range(L):
            price += rng.normal(drift, vol)
            out.append(price)
        i += L

    c = np.array(out[:n])
    rng2 = np.random.default_rng(seed + 999)
    idx = pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({
        "open": np.concatenate([[c[0]], c[:-1]]),
        "high": c + np.abs(rng2.normal(0, c * 0.0008)),
        "low": c - np.abs(rng2.normal(0, c * 0.0008)),
        "close": c}, index=idx)


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    return df.resample(rule).agg({"open": "first", "high": "max",
                                  "low": "min", "close": "last"}).dropna()


def fake_calendar(start, days, seed):
    """~3 event high-impact USD per pekan, tersebar di jam sesi New York."""
    rng = np.random.default_rng(seed)
    ev = []
    for d in range(days):
        day = start + timedelta(days=d)
        if day.weekday() >= 5:
            continue
        for _ in range(rng.poisson(0.6)):
            t = day.replace(hour=int(rng.choice([12, 13, 14, 18])),
                            minute=int(rng.choice([0, 30])))
            ev.append({"title": "US Data", "country": "USD", "impact": "High",
                       "time": pd.Timestamp(t), "forecast": "", "previous": ""})
    return ev


def simulate(days: int, seed: int, with_news: bool = True):
    h1 = synth_gold(days + 30, seed)          # +30 hari untuk warm-up indikator
    h4, d1 = resample(h1, "4h"), resample(h1, "1d")
    events = fake_calendar(h1.index[0].to_pydatetime(), days + 30, seed) \
        if with_news else []

    stage = Counter()
    grades = Counter()
    sends, state = [], {}
    warm = 24 * 30

    for i in range(warm, len(h1)):
        ts = h1.index[i].to_pydatetime()
        stage["jam dievaluasi"] += 1

        if not x.market_open(ts):
            stage["pasar tutup"] += 1
            continue
        stage["pasar buka"] += 1

        e = h1.iloc[max(0, i - 399):i + 1]
        b = h4[h4.index <= ts].tail(400)
        m = d1[d1.index <= ts].tail(400)
        if len(b) < 210 or len(m) < 60:
            stage["warm-up (dilewati)"] += 1
            continue
        stage["dievaluasi"] += 1

        sig = x.build_signal(b, e, m, events, ts, {})

        if abs(sig.composite) < x.THRESHOLD:
            stage["skor di bawah ambang"] += 1
            continue
        stage["lolos ambang skor"] += 1

        if sig.blackout:
            stage["kena blackout news"] += 1
            continue

        failed = [c.name for c in sig.checks if c.mandatory and not c.passed]
        if failed:
            stage["gagal syarat wajib"] += 1
            for f in failed:
                stage[f"  └ {f}"] += 1
            continue

        if sig.n_confirms < x.MIN_CONFIRMS:
            stage["konfirmasi kurang"] += 1
            continue
        stage["lolos semua gate"] += 1

        ok, _ = x.should_send(sig, state, ts)
        if not ok:
            stage["kena cooldown"] += 1
            continue

        stage["TERKIRIM"] += 1
        grades[sig.grade] += 1
        sends.append(ts)
        state["last"] = {"time": ts.isoformat(), "direction": sig.direction,
                         "entry": sig.entry, "id": sig.signal_id()}

    return stage, grades, sends, days


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    seeds = [1, 7, 42, 101, 2024]
    per_day, per_week_active, all_grades = [], [], Counter()
    agg = Counter()

    print(f"Simulasi {days} hari kalender × {len(seeds)} skenario pasar\n")
    for s in seeds:
        stage, grades, sends, d = simulate(days, s)
        agg.update(stage)
        all_grades.update(grades)
        trading_days = stage["dievaluasi"] / 24
        rate = stage["TERKIRIM"] / trading_days if trading_days else 0
        per_day.append(rate)
        active = len({t.date() for t in sends})
        per_week_active.append(active / (trading_days / 5) if trading_days else 0)
        print(f"  seed {s:>4}: {stage['TERKIRIM']:>3} sinyal / "
              f"{trading_days:.0f} hari trading = {rate:.2f} per hari "
              f"· {active} hari ada sinyal")

    print(f"\n{'═' * 58}\nRata-rata: {np.mean(per_day):.2f} sinyal/hari trading "
          f"(rentang {min(per_day):.2f}–{max(per_day):.2f})")
    print(f"Setara     {np.mean(per_day) * 5:.1f} sinyal/pekan, "
          f"{np.mean(per_day) * 21:.0f}/bulan")

    print(f"\nDistribusi grade:")
    tot = sum(all_grades.values()) or 1
    for g in ("A", "B", "C"):
        print(f"  Grade {g}: {all_grades[g]:>4} ({all_grades[g] / tot * 100:.0f}%)")

    print(f"\nCorong penyaringan (total {len(seeds)} skenario):")
    order = ["jam dievaluasi", "pasar tutup", "pasar buka", "warm-up (dilewati)", "dievaluasi", "skor di bawah ambang",
             "lolos ambang skor", "kena blackout news", "gagal syarat wajib",
             "konfirmasi kurang", "lolos semua gate", "kena cooldown", "TERKIRIM"]
    base = agg["dievaluasi"] or 1
    for k in order:
        if k in agg:
            print(f"  {k:<26} {agg[k]:>7}  ({agg[k] / base * 100:5.1f}% dievaluasi)")
    for k in sorted(agg):
        if k.startswith("  └"):
            print(f"    {k:<24} {agg[k]:>7}")


if __name__ == "__main__":
    main()
