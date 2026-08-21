#!/bin/bash
# install-macos.sh — pasang bot sebagai layanan launchd di macOS.
#
# Kenapa launchd, bukan cron:
#   • cron TIDAK menjalankan jadwal yang terlewat saat Mac tidur.
#     launchd menjalankannya begitu Mac bangun.
#   • cron di macOS butuh Full Disk Access yang membingungkan.
#   • launchd mencatat log dan bisa restart otomatis.
#
# Pakai:  ./install-macos.sh          pasang
#         ./install-macos.sh status   cek
#         ./install-macos.sh logs     lihat log
#         ./install-macos.sh remove   copot

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL_SIGNAL="com.xausignal.signal"
LABEL_JOURNAL="com.xausignal.journal"
AGENTS="$HOME/Library/LaunchAgents"
LOGDIR="$HOME/.xau_signal/logs"

PY="$(command -v python3 || true)"
[ -z "$PY" ] && { echo "❌ python3 tidak ditemukan."; exit 1; }

# ── Sub-perintah ────────────────────────────────────────────────────────────

case "${1:-install}" in
  status)
    echo "Status layanan:"
    for L in "$LABEL_SIGNAL" "$LABEL_JOURNAL"; do
      if launchctl list | grep -q "$L"; then
        line=$(launchctl list | grep "$L")
        code=$(echo "$line" | awk '{print $2}')
        printf "  ✅ %-26s exit terakhir: %s\n" "$L" "$code"
        [ "$code" != "0" ] && echo "     ⚠️  exit bukan 0 — cek: $0 logs"
      else
        printf "  ❌ %-26s belum terpasang\n" "$L"
      fi
    done
    echo
    echo "Sinyal terakhir tercatat:"
    tail -n 3 "$HOME/.xau_signal/signals.csv" 2>/dev/null || echo "  (belum ada)"
    exit 0 ;;

  logs)
    echo "── stdout (20 baris terakhir) ──"
    tail -n 20 "$LOGDIR/signal.log" 2>/dev/null || echo "(kosong)"
    echo
    echo "── stderr (20 baris terakhir) ──"
    tail -n 20 "$LOGDIR/signal.err" 2>/dev/null || echo "(kosong)"
    exit 0 ;;

  remove)
    for L in "$LABEL_SIGNAL" "$LABEL_JOURNAL"; do
      launchctl unload "$AGENTS/$L.plist" 2>/dev/null || true
      rm -f "$AGENTS/$L.plist"
      echo "  dicopot: $L"
    done
    echo "✅ Selesai. Data di ~/.xau_signal tidak dihapus."
    exit 0 ;;
esac

# ── Pemeriksaan sebelum pasang ──────────────────────────────────────────────

echo "Memeriksa prasyarat…"
[ -f "$DIR/xau_signal.py" ] || { echo "❌ xau_signal.py tidak ada di $DIR"; exit 1; }
[ -f "$DIR/.env" ] || { echo "❌ .env belum dibuat. Jalankan: cp .env.example .env"; exit 1; }

if ! grep -qE '^\s*(export\s+)?TELEGRAM_BOT_TOKEN=.{10,}' "$DIR/.env"; then
  echo "❌ TELEGRAM_BOT_TOKEN di .env terlihat kosong."; exit 1
fi

echo "  ✅ python3   $PY"
echo "  ✅ folder    $DIR"
echo "  ✅ .env      ada"

echo
echo "Uji jalan sekali (tidak mengirim)…"
if ! (cd "$DIR" && "$PY" xau_signal.py --dry-run >/dev/null 2>&1); then
  echo "❌ Uji gagal. Perbaiki dulu:  cd $DIR && python3 xau_signal.py --dry-run"
  exit 1
fi
echo "  ✅ berjalan normal"

mkdir -p "$AGENTS" "$LOGDIR"

# ── Tulis plist ─────────────────────────────────────────────────────────────

write_plist() {
  local label="$1" args="$2" interval="$3" out="$4"
  cat > "$AGENTS/$label.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$label</string>

    <key>ProgramArguments</key>
    <array>
        <string>$PY</string>
        <string>$DIR/$args</string>
    </array>

    <key>WorkingDirectory</key><string>$DIR</string>

    <!-- StartInterval, bukan StartCalendarInterval: launchd menjalankan
         tugas yang terlewat begitu Mac bangun dari tidur. -->
    <key>StartInterval</key><integer>$interval</integer>

    <key>RunAtLoad</key><false/>

    <key>StandardOutPath</key><string>$LOGDIR/$out.log</string>
    <key>StandardErrorPath</key><string>$LOGDIR/$out.err</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
        <key>HOME</key><string>$HOME</string>
    </dict>

    <key>ProcessType</key><string>Background</string>
    <key>LowPriorityIO</key><true/>
</dict>
</plist>
PLIST
  launchctl unload "$AGENTS/$label.plist" 2>/dev/null || true
  launchctl load "$AGENTS/$label.plist"
}

echo
echo "Memasang layanan…"
write_plist "$LABEL_SIGNAL"  "xau_signal.py" 3600  "signal"
echo "  ✅ evaluasi sinyal   tiap 1 jam"
write_plist "$LABEL_JOURNAL" "journal.py"    43200 "journal"
echo "  ✅ penilaian hasil   tiap 12 jam"

# ── Penutup ─────────────────────────────────────────────────────────────────

cat <<EOF

════════════════════════════════════════════════════════
✅ Terpasang. Bot berjalan otomatis mulai sekarang.

Perintah:
  $0 status     cek layanan
  $0 logs       lihat log
  $0 remove     copot

════════════════════════════════════════════════════════
⚠️  PENTING — MacBook tidur

launchd menjalankan tugas yang TERLEWAT begitu Mac bangun, jadi
Anda tidak kehilangan sinyal sepenuhnya. Tapi sinyal itu datang
TERLAMBAT — bisa berjam-jam setelah candle-nya tutup, dan harganya
sudah bergerak jauh dari level entry.

Untuk fase observasi ini tidak masalah: Anda memang belum
mengeksekusi apa pun, dan jurnal tetap menilai berdasarkan waktu
sinyal yang sebenarnya.

Kalau nanti masuk fase uang nyata, pertimbangkan:

  1. Biarkan Mac menyala saat pasar buka (Senin 04:00 – Sabtu 04:00 WIB)
     Cek: pmset -g assertions

  2. Jadwalkan bangun otomatis:
     sudo pmset repeat wakeorpoweron MTWRF 06:00:00

  3. Pindahkan ke perangkat yang selalu hidup — Raspberry Pi,
     VPS murah, atau Mac Mini lama. Ini pilihan paling andal.

════════════════════════════════════════════════════════

Sinyal pertama dievaluasi dalam 1 jam. Untuk menguji sekarang:

  cd $DIR && python3 xau_signal.py --force

EOF
