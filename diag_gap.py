#!/usr/bin/env python3
"""
diag_gap.py — kenapa live swing (400 bar TwelveData, regime bullish) hampir
tak pernah lolos, sementara backtest 5000 bar Dukascopy lolos 31%.

Menjalankan sistem pada dua jendela data yang SAMA (Dukascopy), lalu membandingkan:
  FULL  = 5000 bar (campuran bull/bear, seperti backtest)
  TAIL  = 400 bar terakhir (regime bull terkini, seperti live)

Untuk setiap sinyal yang MENYEBERANG ambang tapi di-veto, catat NAMA syarat
wajib yang menggagalkannya. Ini langsung menjawab gerbang mana yang mencekik.

Read-only. Tidak mengubah apa pun.
"""
from __future__ import annotations
import sys
from collections import Counter, defaultdict

import xau_signal as x
import datafeed

THRESH = x.THRESHOLD
MIN_CONF = x.MIN_CONFIRMS


def scan(h1, h4, d1, lo, hi, label):
    crossed_veto = Counter()      # nama syarat wajib -> jumlah
    crossed_confirm = 0           # crossed tapi confirms < MIN
    sent = Counter()              # grade -> jumlah
    comp_by_dir = defaultdict(list)
    n = 0
    for i in range(lo, hi):
        e = h1.iloc[max(0, i - 399):i + 1]
        ts = e.index[-1]
        b = h4[h4.index <= ts].tail(400)
        m = d1[d1.index <= ts].tail(400)
        if len(b) < 210 or len(m) < 60:
            continue
        sig = x.build_signal(b, e, m, [], ts.to_pydatetime(), {})
        n += 1
        if sig.direction == "NO-TRADE":
            if sig.composite >= THRESH or sig.composite <= -THRESH:
                failed = [c.name for c in sig.checks if c.mandatory and not c.passed]
                if failed:
                    for f in failed:
                        crossed_veto[f] += 1
                else:
                    crossed_confirm += 1
        else:
            sent[sig.grade] += 1
            comp_by_dir[sig.direction].append(sig.composite)

    print(f"\n=== {label} ===")
    print(f"evaluasi {n} · lolos {sum(sent.values())} · crossed-dan-divetokan "
          f"{sum(crossed_veto.values()) + crossed_confirm}")
    print(f"  veto wajib (crossed): {dict(crossed_veto.most_common())}")
    print(f"  crossed tapi confirms<{MIN_CONF}: {crossed_confirm}")
    print(f"  grade lolos: {dict(sent)}")
    for d in ("BUY", "SELL"):
        c = comp_by_dir[d]
        if c:
            print(f"  {d}: n={len(c)} min={min(c):+.1f} max={max(c):+.1f} "
                  f"rata={sum(c)/len(c):+.1f}")
        else:
            print(f"  {d}: n=0")
    return n, sum(sent.values())


def main():
    h1 = datafeed.get_ohlc("1h", 5000, prefer="dukascopy").df
    h4 = datafeed.get_ohlc("4h", 3000, prefer="dukascopy").df
    d1 = datafeed.get_ohlc("1d", 1500, prefer="dukascopy").df
    n_full = len(h1)
    print(f"Data H1: {n_full} bar · {h1.index[0]:%b %Y} → {h1.index[-1]:%b %Y}")

    WARM = 260
    # TAIL = 400 evaluasi terakhir (setara jendela live TwelveData)
    scan(h1, h4, d1, WARM, n_full, "FULL (5000 bar, campuran regime)")
    scan(h1, h4, d1, max(WARM, n_full - 400), n_full, "TAIL (400 bar terakhir, bull terkini)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
