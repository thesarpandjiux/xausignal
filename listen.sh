#!/bin/bash
# listen.sh — jalankan pendengar perintah Telegram di komputer Anda.
#
# Kenapa perlu: jadwal GitHub Actions tidak dijamin. GitHub sendiri menyatakan
# job terjadwal bisa tertunda saat beban tinggi dan sebagian yang mengantre
# DIBUANG. Untuk perintah interaktif, itu tidak memadai.
#
# Pembagian yang masuk akal:
#   Sinyal   → GitHub Actions. Butuh KEANDALAN 24/7, bukan kecepatan.
#              Terlambat 15 menit tidak mengubah apa pun.
#   Perintah → komputer Anda. Butuh KECEPATAN, bukan keandalan.
#              Anda hanya bertanya saat komputer menyala.
#
# Pakai:
#     ./listen.sh              jalankan sampai Ctrl+C
#     ./listen.sh --background jalankan di latar, log ke ~/.xau_signal/listen.log
#
# Aman dijalankan bersamaan dengan GitHub Actions: bila keduanya aktif,
# yang satu akan mundur dengan pesan 409 dan mencoba lagi.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

if [ ! -f .env ]; then
  echo "[X] .env belum ada. Jalankan: cp .env.example .env"
  exit 1
fi

PY=$(command -v python3 || true)
[ -z "$PY" ] && { echo "[X] python3 tidak ditemukan"; exit 1; }

if [ "${1:-}" = "--background" ]; then
  LOG="$HOME/.xau_signal/listen.log"
  mkdir -p "$(dirname "$LOG")"
  nohup "$PY" telegram_bot.py listen >> "$LOG" 2>&1 &
  echo "[ok] berjalan di latar (PID $!)"
  echo "     log     : tail -f $LOG"
  echo "     hentikan: pkill -f 'telegram_bot.py listen'"
  exit 0
fi

echo "Pendengar perintah Telegram aktif."
echo "Balasan datang dalam hitungan detik. Ctrl+C untuk berhenti."
echo "----------------------------------------------------------"
exec "$PY" telegram_bot.py listen
