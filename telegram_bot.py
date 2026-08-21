#!/usr/bin/env python3
"""
telegram_bot.py — terima perintah dari Telegram.

Dua mode:
    python telegram_bot.py poll                    sekali cek lalu keluar
    python telegram_bot.py listen                  long-polling terus-menerus (VPS/Pi)
    python telegram_bot.py listen --seconds=280    dengar 280 detik lalu keluar (CI)
    python telegram_bot.py analisa                 jalankan /analisa & kirim (webhook)

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


STATE_FILE = BASE / "tg_state.json"


def load_tg_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_tg_state(d: dict) -> None:
    BASE.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(d, default=str))


def cmd_analisa() -> str:
    """
    Batasnya berbasis CANDLE, bukan cooldown buta.

    Sistem membaca candle H1 yang sudah tutup, jadi selama belum ada candle
    baru, jawabannya identik — memanggil API lagi hanya membakar kuota
    (3 permintaan per /analisa, jatah gratis 800/hari). Menolak dengan alasan
    "sabar dulu" tidak informatif; menyebut kapan jawabannya benar-benar
    berubah jauh lebih berguna.
    """
    st = load_tg_state()
    now = datetime.now(timezone.utc)
    wib = timezone(timedelta(hours=7))

    last_candle = st.get("candle")
    if last_candle:
        try:
            c = datetime.fromisoformat(last_candle)
            # Bar berikutnya muncul 1 jam setelah stempel bar terakhir, bukan 2:
            # penyedia data sering menyertakan candle yang MASIH BERJALAN, jadi
            # mengasumsikan bar terakhir pasti sudah tutup membuat batas terlalu
            # panjang. Tambah 2 menit toleransi keterlambatan publikasi.
            berikutnya = c + timedelta(hours=1, minutes=2)
            if now < berikutnya:
                sisa = max(1, int((berikutnya - now).total_seconds() / 60))
                lalu = int((now - datetime.fromisoformat(st["waktu"])).total_seconds() / 60)
                return (f"⏳ <b>Belum ada data baru</b>\n\n"
                        f"Analisis terakhir {lalu} menit lalu, memakai candle H1 "
                        f"pukul {c.astimezone(wib):%H:%M} WIB.\n\n"
                        f"Sistem membaca candle per jam, jadi hasilnya "
                        f"<b>tidak akan berubah</b> sampai candle berikutnya "
                        f"terbit sekitar <b>{berikutnya.astimezone(wib):%H:%M} WIB</b> "
                        f"({sisa} menit lagi).\n\n"
                        f"<i>Ringkasan terakhir:</i>\n{st.get('ringkasan', '—')}\n\n"
                        f"<i>/status dan /laporan tetap bisa kapan saja.</i>")
        except Exception:
            pass

    try:
        macro, _ = x.get_ohlc(x.TF_MACRO)
        bias, _ = x.get_ohlc(x.TF_BIAS)
        entry, src = x.get_ohlc(x.TF_ENTRY)
        events, cal_ok = x.fetch_calendar()
    except Exception as e:
        return f"❌ Gagal mengambil data:\n<code>{str(e)[:300]}</code>"

    sig = x.build_signal(bias, entry, macro, events, now, x.load_calibration(),
                         calendar_trusted=cal_ok, data_source=src)
    teks = _format_analisa(sig, now, src)

    save_tg_state({**st, "waktu": now.isoformat(),
                   "candle": entry.index[-1].isoformat(),
                   "ringkasan": f"{sig.direction} · skor {sig.composite:+.1f} "
                                f"· ${sig.price:,.2f}"})
    return teks


def _format_analisa(sig, now, src) -> str:
    if sig.direction != "NO-TRADE":
        return (x.format_message_simple(sig)
                + "\n\n<i>Diminta manual. Sinyal ini juga dikirim otomatis.</i>")

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


def run(mode: str, seconds: int = 0) -> int:
    """
    mode='poll'   : sekali cek lalu keluar
    mode='listen' : long-polling; `seconds` > 0 membatasi durasi (untuk CI)

    Long-polling membuat Telegram MENDORONG pesan begitu Anda kirim, jadi
    balasan datang dalam hitungan detik selama jendela terbuka — bukan
    menunggu siklus cron berikutnya.
    """
    if not os.getenv("TELEGRAM_BOT_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID"):
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID belum diset", file=sys.stderr)
        return 1

    # getUpdates dan webhook saling meniadakan. TAPI jangan asal hapus:
    # webhook itu bisa milik aplikasi lain yang sedang berjalan, dan
    # menghapusnya diam-diam tiap 5 menit akan merusaknya tanpa jejak.
    try:
        info = api("getWebhookInfo")
        url = (info or {}).get("url", "")
        if url:
            print("=" * 58, file=sys.stderr)
            print("BOT INI SUDAH DIPAKAI APLIKASI LAIN", file=sys.stderr)
            print(f"  webhook aktif : {url[:60]}", file=sys.stderr)
            print(f"  tertunda      : {info.get('pending_update_count', 0)} pesan",
                  file=sys.stderr)
            print("", file=sys.stderr)
            print("Telegram hanya mengizinkan SATU konsumen per bot.", file=sys.stderr)
            print("Fitur perintah dilewati agar aplikasi itu tidak rusak.",
                  file=sys.stderr)
            print("", file=sys.stderr)
            print("Solusi: buat bot terpisah lewat @BotFather untuk bot sinyal ini,",
                  file=sys.stderr)
            print("lalu perbarui TELEGRAM_BOT_TOKEN. Pengiriman sinyal TIDAK",
                  file=sys.stderr)
            print("terpengaruh — hanya /analisa dan /status yang butuh polling.",
                  file=sys.stderr)
            print("", file=sys.stderr)
            print("Kalau webhook itu memang tidak terpakai lagi:", file=sys.stderr)
            print("  TG_TAKEOVER_WEBHOOK=1  untuk menghapusnya.", file=sys.stderr)
            print("=" * 58, file=sys.stderr)

            if os.getenv("TG_TAKEOVER_WEBHOOK", "").strip() not in ("1", "true", "yes"):
                return 0            # bukan kegagalan — keputusan sadar
            print("[info] TG_TAKEOVER_WEBHOOK aktif, menghapus webhook",
                  file=sys.stderr)
            api("deleteWebhook", drop_pending_updates=False)
    except Exception as e:
        print(f"[warn] getWebhookInfo: {e}", file=sys.stderr)

    offset = load_offset()
    once = mode == "poll"
    conflicts = 0
    batas = time.time() + seconds if seconds else None

    while True:
        if batas and time.time() >= batas:
            save_offset(offset)
            print("durasi habis, keluar")
            return 0
        try:
            # Jangan minta timeout lebih lama dari sisa durasi, supaya
            # job tidak digantung melewati batasnya.
            tmo = 0 if once else POLL_TIMEOUT
            if batas:
                tmo = max(1, min(tmo, int(batas - time.time())))
            ups = api("getUpdates", offset=offset, timeout=tmo,
                      allowed_updates=["message"])
            conflicts = 0
        except Exception as e:
            msg = str(e)
            # 409 = instance lain masih memegang koneksi. Ini biasanya
            # sementara: permintaan long-poll sebelumnya belum tertutup di
            # sisi Telegram. Tunggu sebentar, jangan langsung menyerah.
            if "409" in msg or "Conflict" in msg:
                conflicts += 1
                if conflicts <= 3:
                    print(f"[warn] 409 conflict, coba lagi ({conflicts}/3)",
                          file=sys.stderr)
                    time.sleep(5 * conflicts)
                    continue
                print("[warn] masih 409 setelah 3 percobaan — kemungkinan ada "
                      "instance lain berjalan. Dilewati.", file=sys.stderr)
                # Jangan buat run merah: konflik sementara bukan kegagalan.
                return 0 if once else 1

            print(f"[warn] getUpdates: {msg}", file=sys.stderr)
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


def cmd_kirim_analisa() -> int:
    """Jalankan /analisa lalu kirim hasilnya. Dipicu Cloudflare Worker."""
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not chat:
        print("TELEGRAM_CHAT_ID belum diset", file=sys.stderr)
        return 1
    # Worker sudah membalas "sedang dianalisis", jadi batas per-candle tidak
    # relevan di sini — pengguna memang sudah menunggu jawaban.
    save_tg_state({})
    reply(chat, cmd_analisa())
    print("analisa terkirim")
    return 0


def main() -> int:
    args = sys.argv[1:]
    mode = args[0] if args else "poll"
    if mode == "analisa":
        return cmd_kirim_analisa()
    if mode not in ("poll", "listen"):
        print(__doc__)
        return 1

    seconds = 0
    for a in args[1:]:
        if a.startswith("--seconds="):
            seconds = int(a.split("=", 1)[1])

    if mode == "listen":
        print(f"Mendengarkan{f' {seconds} detik' if seconds else '… Ctrl+C untuk berhenti'}")
    return run(mode, seconds)


if __name__ == "__main__":
    sys.exit(main())
