#!/usr/bin/env python3
"""
telegram_bot.py — terima perintah dari Telegram.

Dua mode:
    python telegram_bot.py poll      sekali jalan, proses pesan tertunda (GitHub Actions)
    python telegram_bot.py listen    long-polling terus-menerus (VPS / Raspberry Pi)

Perintah:
    /analisa   kondisi pasar sekarang + alasan ada/tidaknya sinyal
    /status    bot hidup? kapan terakhir jalan? sinyal terakhir?
    /laporan   statistik jurnal
    /bantuan   daftar perintah

Catatan penting: /analisa TIDAK memberi data lebih baru daripada evaluasi
terjadwal. Sistem membaca candle H1 yang sudah tutup, jadi bertanya di menit
ke-23 memberi hasil identik dengan menit ke-5. Gunanya adalah melihat skor
dan syarat mana yang gagal — bukan mendapat harga lebih segar.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import datafeed  # memuat .env
import xau_signal as x

BASE = Path(os.getenv("XAU_HOME", "~/.xau_signal")).expanduser()
OFFSET_FILE = BASE / "tg_offset.json"
POLL_TIMEOUT = 50          # detik, long-polling


def api(method: str, **params):
    tok = os.getenv("TELEGRAM_BOT_TOKEN")
    if not tok:
        raise RuntimeError("TELEGRAM_BOT_TOKEN tidak diset")
    r = requests.post(f"https://api.telegram.org/bot{tok}/{method}",
                      json=params, timeout=POLL_TIMEOUT + 15)
    r.raise_for_status()
    d = r.json()
    if not d.get("ok"):
        raise RuntimeError(f"Telegram {method}: {d.get('description')}")
    return d["result"]


def reply(chat_id, text):
    api("sendMessage", chat_id=chat_id, text=text[:4096],
        parse_mode="HTML", disable_web_page_preview=True)


def load_offset() -> int:
    try:
        return int(json.loads(OFFSET_FILE.read_text())["offset"])
    except Exception:
        return 0


def save_offset(v: int) -> None:
    BASE.mkdir(parents=True, exist_ok=True)
    OFFSET_FILE.write_text(json.dumps({"offset": v}))


# ─────────────────────────────── Perintah ───────────────────────────────────

def cmd_bantuan() -> str:
    return ("<b>Perintah tersedia</b>\n\n"
            "/analisa — kondisi pasar sekarang, skor, dan syarat mana yang gagal\n"
            "/status — bot hidup? kapan terakhir jalan?\n"
            "/laporan — statistik hasil sinyal\n"
            "/bantuan — pesan ini\n\n"
            "<i>Sinyal otomatis tetap dikirim tiap jam tanpa diminta. "
            "/analisa tidak memberi data lebih baru — sistem membaca candle H1 "
            "yang sudah tutup, jadi hasilnya sama sampai candle berikutnya.</i>")


def cmd_analisa() -> str:
    try:
        macro, _ = x.get_ohlc(x.TF_MACRO)
        bias, _ = x.get_ohlc(x.TF_BIAS)
        entry, src = x.get_ohlc(x.TF_ENTRY)
        events, cal_ok = x.fetch_calendar()
    except Exception as e:
        return f"❌ Gagal mengambil data:\n<code>{str(e)[:300]}</code>"

    now = datetime.now(timezone.utc)
    sig = x.build_signal(bias, entry, macro, events, now, x.load_calibration(),
                         calendar_trusted=cal_ok, data_source=src)

    if sig.direction != "NO-TRADE":
        return (x.format_message_simple(sig)
                + "\n\n<i>Diminta manual. Sinyal ini juga dikirim otomatis.</i>")

    # Tidak ada sinyal — tampilkan kenapa
    wib = now.astimezone(timezone(timedelta(hours=7)))
    L = [f"⚪ <b>Belum ada sinyal</b>",
         f"<i>{wib:%d %b %H:%M} WIB · ${sig.price:,.2f}</i>", ""]

    if sig.blackout:
        L += [f"⛔ {sig.blackout}", ""]

    L += [f"Skor: <b>{sig.composite:+.1f}</b> (butuh ±{x.THRESHOLD})", ""]

    if abs(sig.composite) < x.THRESHOLD:
        L.append("<i>Arah belum cukup jelas. Ini kondisi paling umum —"
                 " sekitar 2 dari 3 jam pasar.</i>")
    elif sig.checks:
        gagal = [c for c in sig.checks if c.mandatory and not c.passed]
        if gagal:
            L.append("<b>Syarat wajib yang gagal</b>")
            L += [f"❌ {c.name} — <i>{c.note}</i>" for c in gagal]
        else:
            L.append(f"<b>Konfirmasi kurang</b> ({sig.n_confirms}/5, "
                     f"minimal {x.MIN_CONFIRMS})")
            L += [f"➖ {c.name} — <i>{c.note}</i>"
                  for c in sig.checks if not c.mandatory and not c.passed]

    L += ["", "<b>Komponen teknikal</b>"]
    for c in sorted(sig.components, key=lambda k: -abs(k.contribution)):
        m = "▲" if c.score > 10 else "▼" if c.score < -10 else "•"
        L.append(f"{m} {c.name}: {c.score:+.0f}")

    ev_high = [e for e in sig.news_events if e["impact"] == "High"]
    if ev_high:
        L += ["", "<b>Berita penting berikutnya</b>"]
        for e in ev_high[:2]:
            t = e["time"].astimezone(timezone(timedelta(hours=7)))
            L.append(f"🔴 {t:%d/%m %H:%M} — {e['title']}")

    L.append(f"\n<i>Sumber: {src}</i>")
    return "\n".join(L)


def cmd_status() -> str:
    L = ["<b>Status bot</b>", ""]

    st = x.load_state().get("last")
    if st:
        t = datetime.fromisoformat(st["time"])
        wib = t.astimezone(timezone(timedelta(hours=7)))
        jam = (datetime.now(timezone.utc) - t).total_seconds() / 3600
        L.append(f"Sinyal terakhir : {st['direction']} @ ${st.get('entry', 0):,.2f}")
        L.append(f"                  {wib:%d %b %H:%M} WIB ({jam:.0f} jam lalu)")
    else:
        L.append("Sinyal terakhir : belum ada")

    log = BASE / "signals.csv"
    if log.exists():
        try:
            import pandas as pd
            df = pd.read_csv(log)
            terkirim = (df["sent"].astype(str) == "True").sum()
            L.append(f"Total evaluasi  : {len(df)}")
            L.append(f"Sinyal terkirim : {terkirim}")
        except Exception:
            pass
    else:
        L.append("Riwayat         : belum ada")

    calib = x.load_calibration()
    if calib.get("_meta"):
        m = calib["_meta"]
        L.append(f"Kalibrasi       : {m.get('total_signals')} sinyal, "
                 f"menang {m.get('overall_win_rate')}%")
    else:
        L.append("Kalibrasi       : belum ada (jalankan --backtest)")

    buka = x.market_open(datetime.now(timezone.utc))
    L += ["", f"Pasar           : {'🟢 buka' if buka else '🔴 tutup'}"]
    return "\n".join(L)


def cmd_laporan() -> str:
    import io
    import contextlib
    import journal
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            journal.cmd_report()
    except Exception as e:
        return f"❌ Gagal: <code>{str(e)[:200]}</code>"
    out = buf.getvalue().strip() or "Jurnal masih kosong."
    return f"<pre>{out[:3800]}</pre>"


HANDLERS = {"/analisa": cmd_analisa, "/status": cmd_status,
            "/laporan": cmd_laporan, "/bantuan": cmd_bantuan,
            "/start": cmd_bantuan, "/help": cmd_bantuan}


# ──────────────────────────── Loop pemrosesan ───────────────────────────────

def handle(update: dict) -> None:
    msg = update.get("message") or update.get("edited_message") or {}
    chat_id = str(msg.get("chat", {}).get("id", ""))
    text = (msg.get("text") or "").strip().lower().split("@")[0]

    # Hanya layani chat yang dikonfigurasi. Tanpa ini, siapa pun yang
    # menemukan nama bot bisa memicunya dan membakar kuota API Anda.
    allowed = os.getenv("TELEGRAM_CHAT_ID", "")
    if chat_id != allowed:
        print(f"[abaikan] chat asing {chat_id}", file=sys.stderr)
        return

    fn = HANDLERS.get(text.split()[0] if text else "")
    if not fn:
        if text.startswith("/"):
            reply(chat_id, "Perintah tidak dikenal. Ketik /bantuan.")
        return

    print(f"[perintah] {text}")
    try:
        reply(chat_id, fn())
    except Exception as e:
        reply(chat_id, f"❌ Error: <code>{str(e)[:300]}</code>")
        print(f"[error] {e}", file=sys.stderr)


def run(mode: str) -> int:
    if not os.getenv("TELEGRAM_BOT_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID"):
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID belum diset", file=sys.stderr)
        return 1

    offset = load_offset()
    once = mode == "poll"

    while True:
        try:
            ups = api("getUpdates", offset=offset,
                      timeout=0 if once else POLL_TIMEOUT,
                      allowed_updates=["message"])
        except Exception as e:
            print(f"[warn] getUpdates: {e}", file=sys.stderr)
            if once:
                return 1
            time.sleep(10)
            continue

        for u in ups:
            offset = u["update_id"] + 1
            handle(u)
        if ups:
            save_offset(offset)

        if once:
            print(f"{len(ups)} pesan diproses")
            return 0


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "poll"
    if mode not in ("poll", "listen"):
        print(__doc__)
        return 1
    if mode == "listen":
        print("Mendengarkan perintah… Ctrl+C untuk berhenti.")
    return run(mode)


if __name__ == "__main__":
    sys.exit(main())
