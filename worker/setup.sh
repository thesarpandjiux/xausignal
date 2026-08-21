#!/bin/bash
# setup.sh — pasang webhook Cloudflare Workers, sekali jalan.
#
# Mengambil sendiri: token Telegram & chat ID dari ../.env, nama repo dari
# git remote, dan membuat WEBHOOK_SECRET acak.
#
# Anda hanya perlu menyediakan SATU hal: Personal Access Token GitHub.
#
# Pakai:  cd worker && ./setup.sh

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

ok()   { echo "  [ok] $*"; }
bad()  { echo "  [X]  $*" >&2; }
step() { echo; echo "── $* ──"; }

echo "═══════════════════════════════════════════════"
echo "  Pasang webhook Telegram di Cloudflare Workers"
echo "═══════════════════════════════════════════════"

# ── 1. Prasyarat ────────────────────────────────────────────────────────────
step "Memeriksa prasyarat"

command -v node >/dev/null || { bad "node belum terpasang → https://nodejs.org"; exit 1; }
ok "node $(node --version)"

command -v curl >/dev/null || { bad "curl tidak ditemukan"; exit 1; }
ok "curl tersedia"

[ -f worker.js ] || { bad "worker.js tidak ada. Jalankan dari folder worker/"; exit 1; }
ok "worker.js ada"

# ── 2. Baca kredensial dari .env ────────────────────────────────────────────
step "Membaca ../.env"

ENVF="../.env"
[ -f "$ENVF" ] || { bad "$ENVF tidak ada. Jalankan dulu: cp .env.example .env"; exit 1; }

# `|| true` penting: tanpa itu, grep yang tidak menemukan apa pun akan
# memicu `set -e` dan skrip berhenti TANPA pesan — kegagalan senyap, persis
# yang paling membingungkan untuk didiagnosa.
getenv() {
  grep -E "^\s*(export\s+)?$1=" "$ENVF" 2>/dev/null | tail -1 \
    | sed -E "s/^[^=]*=//; s/^[\"']//; s/[\"'].*$//; s/[[:space:]]*#.*$//; s/[[:space:]]*$//" \
    || true
}

BOT_TOKEN=$(getenv TELEGRAM_BOT_TOKEN || true)
CHAT_ID=$(getenv TELEGRAM_CHAT_ID || true)

[ -n "$BOT_TOKEN" ] || { bad "TELEGRAM_BOT_TOKEN kosong di .env"; exit 1; }
[ -n "$CHAT_ID" ]   || { bad "TELEGRAM_CHAT_ID kosong di .env"; exit 1; }
ok "token  : ${BOT_TOKEN:0:8}…${BOT_TOKEN: -4}"
ok "chat ID: $CHAT_ID"

# Pastikan token benar-benar hidup sebelum lanjut
BOTNAME=$(curl -s "https://api.telegram.org/bot${BOT_TOKEN}/getMe" \
  | sed -n 's/.*"username":"\([^"]*\)".*/\1/p' || true)
[ -n "$BOTNAME" ] || { bad "Token ditolak Telegram. Periksa .env"; exit 1; }
ok "bot    : @$BOTNAME"

# ── 3. Nama repo dari git ───────────────────────────────────────────────────
step "Mendeteksi repo GitHub"

REMOTE=$(cd .. && git remote get-url origin 2>/dev/null || true)
GH_REPO=$(echo "$REMOTE" | sed -E 's#.*github\.com[:/]##; s#\.git$##')
[ -n "$GH_REPO" ] || { bad "Remote GitHub tidak ditemukan di folder induk"; exit 1; }
ok "repo   : $GH_REPO"

sed -i.bak -E "s#^GH_REPO = .*#GH_REPO = \"$GH_REPO\"#" wrangler.toml && rm -f wrangler.toml.bak
ok "wrangler.toml diperbarui"

# ── 4. Personal Access Token ────────────────────────────────────────────────
step "Personal Access Token GitHub"

echo "  Dibutuhkan agar Worker bisa memicu analisis di GitHub Actions."
echo
echo "  Buat di: https://github.com/settings/tokens/new"
echo "    • Note   : xausignal-worker"
echo "    • Scope  : centang 'repo' saja"
echo "    • Expire : bebas (kalau kedaluwarsa, /analisa berhenti)"
echo
printf "  Tempel token (tidak akan tampil): "
read -rs GH_TOKEN
echo

[ -n "$GH_TOKEN" ] || { bad "Token kosong"; exit 1; }

# Uji token SEBELUM deploy — lebih baik gagal di sini daripada nanti diam-diam
CODE=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST "https://api.github.com/repos/$GH_REPO/dispatches" \
  -H "Authorization: Bearer $GH_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  -d '{"event_type":"analisa"}')

case "$CODE" in
  204) ok "token valid — analisis uji dipicu" ;;
  401) bad "Token ditolak (401). Salah salin?"; exit 1 ;;
  403) bad "Token tidak punya izin (403). Scope 'repo' sudah dicentang?"; exit 1 ;;
  404) bad "Repo '$GH_REPO' tidak ditemukan, atau token tidak punya akses."
       bad "Sudah push analisa.yml ke GitHub?"; exit 1 ;;
  *)   bad "Balasan tak terduga: HTTP $CODE"; exit 1 ;;
esac

# ── 5. Login Cloudflare ─────────────────────────────────────────────────────
step "Cloudflare"

if npx --yes wrangler whoami 2>/dev/null | grep -qi "you are logged in\|account id"; then
  ok "sudah login"
else
  echo "  Browser akan terbuka untuk login Cloudflare…"
  npx --yes wrangler login
fi

# ── 6. Simpan secrets ───────────────────────────────────────────────────────
step "Menyimpan secrets"

WEBHOOK_SECRET=$(openssl rand -hex 16 2>/dev/null || head -c32 /dev/urandom | od -An -tx1 | tr -d ' \n')

put() { printf '%s' "$2" | npx --yes wrangler secret put "$1" >/dev/null 2>&1 && ok "$1"; }
put TELEGRAM_BOT_TOKEN "$BOT_TOKEN"
put TELEGRAM_CHAT_ID   "$CHAT_ID"
put GH_TOKEN           "$GH_TOKEN"
put WEBHOOK_SECRET     "$WEBHOOK_SECRET"

# ── 7. Deploy ───────────────────────────────────────────────────────────────
step "Deploy Worker"

OUT=$(npx --yes wrangler deploy 2>&1) || { echo "$OUT"; bad "Deploy gagal"; exit 1; }
URL=$(echo "$OUT" | grep -oE 'https://[a-zA-Z0-9.-]+\.workers\.dev' | head -1)
[ -n "$URL" ] || { echo "$OUT"; bad "URL Worker tidak terbaca dari keluaran"; exit 1; }
ok "$URL"

# ── 8. Daftarkan webhook ────────────────────────────────────────────────────
step "Mendaftarkan webhook ke Telegram"

RES=$(curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"$URL\",\"secret_token\":\"$WEBHOOK_SECRET\",\"allowed_updates\":[\"message\"],\"drop_pending_updates\":true}")

echo "$RES" | grep -q '"ok":true' || { bad "setWebhook gagal: $RES"; exit 1; }
ok "webhook terdaftar"

sleep 2
INFO=$(curl -s "https://api.telegram.org/bot${BOT_TOKEN}/getWebhookInfo")
echo "$INFO" | grep -q "\"url\":\"$URL\"" && ok "terverifikasi di sisi Telegram"

ERR=$(echo "$INFO" | sed -n 's/.*"last_error_message":"\([^"]*\)".*/\1/p')
[ -n "$ERR" ] && echo "  [!]  error terakhir: $ERR"

# ── Selesai ─────────────────────────────────────────────────────────────────
cat <<EOF

═══════════════════════════════════════════════
  Selesai. Coba sekarang di Telegram:

      /status      → balasan ~1 detik
      /analisa     → "menganalisis…" lalu hasil ~1 menit

  JANGAN jalankan ./listen.sh lagi — webhook dan
  polling saling meniadakan (409 Conflict).

  Sinyal otomatis tiap jam tetap berjalan seperti biasa.

  Melihat log Worker : cd worker && npx wrangler tail
  Melepas webhook    : curl -X POST \\
      "https://api.telegram.org/bot\$TOKEN/deleteWebhook"
═══════════════════════════════════════════════
EOF
