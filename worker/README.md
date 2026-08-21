# Webhook Cloudflare Workers

Balasan seketika, 24/7, tanpa komputer Anda menyala. Gratis (100.000
permintaan/hari — pemakaian bot ini jauh di bawah itu).

## Cara kerja

```
Anda kirim /status   → Telegram → Worker → jawab langsung        (~1 detik)
Anda kirim /analisa  → Telegram → Worker → balas "menganalisis…" (~1 detik)
                                        → picu GitHub Actions
                                        → hasil dikirim          (~40-60 detik)
```

`/status`, `/laporan`, dan `/bantuan` dijawab Worker sendiri dengan membaca
`signals.csv` dan `journal.csv` dari branch `bot-data`.

`/analisa` butuh pandas dan numpy yang tidak tersedia di Workers, jadi
perhitungannya dijalankan GitHub Actions lewat `repository_dispatch` — pemicu
ini berjalan segera, tanpa antrean jadwal seperti cron.

## Pasang otomatis

```bash
cd worker && ./setup.sh
```

Skrip mengambil sendiri token Telegram & chat ID dari `../.env`, nama repo dari
git remote, dan membuat `WEBHOOK_SECRET` acak. Anda hanya menempel satu hal:
Personal Access Token GitHub (buat di
https://github.com/settings/tokens/new, centang scope **`repo`** saja).

Semuanya divalidasi sebelum deploy — token Telegram diuji lewat `getMe`, PAT
diuji dengan memicu dispatch sungguhan, dan webhook diperiksa ulang setelah
didaftarkan.

---

## Pasang manual

### 1. Personal Access Token GitHub

GitHub → Settings → Developer settings → Personal access tokens → **Tokens
(classic)** → Generate new token.

Centang scope **`repo`** saja. Salin tokennya.

### 2. Deploy Worker

```bash
cd worker
nano wrangler.toml          # ganti GH_REPO dengan USERNAME/xausignal

npx wrangler login
npx wrangler secret put TELEGRAM_BOT_TOKEN
npx wrangler secret put TELEGRAM_CHAT_ID
npx wrangler secret put GH_TOKEN          # PAT dari langkah 1
npx wrangler secret put WEBHOOK_SECRET    # string acak bebas, mis. hasil:
                                          #   openssl rand -hex 16
npx wrangler deploy
```

Catat URL yang muncul, misalnya
`https://xausignal-bot.NAMA-ANDA.workers.dev`

### 3. Daftarkan webhook ke Telegram

```bash
curl -X POST "https://api.telegram.org/bot<TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://xausignal-bot.NAMA-ANDA.workers.dev",
    "secret_token": "<WEBHOOK_SECRET YANG SAMA>",
    "allowed_updates": ["message"]
  }'
```

Balasan `{"ok":true,"result":true,...}` berarti berhasil.

### 4. Uji

Kirim `/status` di Telegram. Balasan harus datang dalam 1–2 detik.

Lalu `/analisa` — balasan "menganalisis…" seketika, hasil menyusul ~1 menit.

## Setelah webhook aktif

**Jangan jalankan `listen.sh` lagi.** Webhook dan polling saling meniadakan;
menjalankan keduanya menghasilkan 409 Conflict.

Workflow `commands.yml` juga tidak diperlukan — jadwalnya memang sudah
dimatikan.

Yang tetap berjalan: `signal.yml` mengirim sinyal otomatis tiap jam.

## Verifikasi

```bash
curl -s "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
```

Yang perlu diperhatikan:

| Isi | Artinya |
|---|---|
| `"url"` kosong | webhook belum terdaftar, ulangi langkah 3 |
| `"pending_update_count"` besar | Worker error, cek `npx wrangler tail` |
| `"last_error_message"` ada | penyebab kegagalan terakhir |

## Melepas webhook

Untuk kembali ke polling (`listen.sh`):

```bash
curl -X POST "https://api.telegram.org/bot<TOKEN>/deleteWebhook"
```

## Biaya

Seluruhnya gratis pada tier bawaan:

| Layanan | Batas gratis | Pemakaian bot ini |
|---|---|---|
| Cloudflare Workers | 100.000 req/hari | puluhan |
| GitHub Actions (repo publik) | tanpa batas | ~30 menit/hari |
| Telegram Bot API | tanpa batas | — |

## Keamanan

Worker memverifikasi dua hal setiap permintaan:

1. Header `X-Telegram-Bot-Api-Secret-Token` cocok dengan `WEBHOOK_SECRET` —
   tanpa ini siapa pun yang menemukan URL Worker bisa mengirim perintah palsu
2. `chat_id` cocok dengan milik Anda — chat lain diabaikan diam-diam

`GH_TOKEN` hanya dipakai untuk memicu workflow, tidak pernah dikirim ke
Telegram maupun ditampilkan di log.
