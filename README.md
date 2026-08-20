# XAUUSD Signal Bot

Bot sinyal trading emas (XAUUSD) berbasis analisis teknikal multi-timeframe dan
kalender ekonomi, dikirim ke Telegram. Seluruh sumber data gratis.

> **Status: belum tervalidasi.** Sistem ini belum diuji pada data XAUUSD asli.
> Angka *win rate* baru bermakna setelah Anda menjalankan `--backtest` dan fase
> observasi. Baca [IMPLEMENTASI.md](IMPLEMENTASI.md) sebelum memakai uang nyata.

---

## Apa yang dikerjakan

Tiap jam, setelah candle H1 tutup, bot mengevaluasi pasar melalui lima lapis:

```
Data (H1/H4/D1)  →  Skor teknikal  →  Confluence gating  →  Grading  →  Telegram
                          ↑                    ↑
                    Sentimen news      5 syarat wajib (veto)
                    + kalender         5 konfirmasi (dihitung)
```

Sekitar **4 dari 100 jam pasar** menghasilkan sinyal — rata-rata 1 per hari
trading. Selebihnya bot diam.

## Contoh sinyal

```
🟢 XAUUSD — BELI
27 Mar 2025 01:00 WIB

Masuk di  : $3,347.39 – $3,349.78
Stop loss : $3,342.89
Target 1  : $3,360.70  ← tutup separuh di sini
Target 2  : $3,365.95
Ukuran    : 0.17 lot

Kenapa: Tren naik kompak di grafik 4 jam dan harian, harga pas
menyentuh support, momentum mendukung.

⚠️ Kalau harga tutup di bawah $3,342.89, keluar. Jangan ditahan.

Grade A · skor +41 · menang 64% dari 95 sinyal serupa
```

Mode lengkap (tanpa `SIMPLE_MODE`) menampilkan seluruh rincian syarat, komponen
teknikal, dan agenda ekonomi.

## Sumber data — semuanya gratis

| Lapis | Layanan | Registrasi | Batas |
|---|---|---|---|
| Harga (live) | Twelve Data Basic | API key gratis | 800 req/hari |
| Harga (riwayat) | Dukascopy | tidak perlu | — |
| Harga (cadangan) | yfinance `GC=F` | tidak perlu | tidak resmi, rawan 429 |
| Kalender | ForexFactory JSON | tidak perlu | 2 unduhan / 5 menit |
| Sentimen news | LLM OpenAI-compatible | opsional | — |
| Pengiriman | Telegram Bot API | bot token | — |

Semua respons di-cache ke disk. Pemakaian nyata jauh di bawah semua batas.

## Pasang

```bash
git clone https://github.com/USERNAME/xausignal.git
cd xausignal
pip install -r requirements.txt

cp .env.example .env      # isi token Anda
source .env

python datafeed.py        # cek sumber data hidup
python xau_signal.py --dry-run
```

> ⚠️ **Jangan pernah commit `.env`.** Berkas itu berisi bot token Anda dan sudah
> masuk `.gitignore`. Token yang bocor ke repo publik dipanen bot dalam hitungan
> menit.

## Perintah

| Perintah | Fungsi |
|---|---|
| `python datafeed.py` | cek sumber data hidup atau mati |
| `python xau_signal.py --dry-run` | lihat sinyal tanpa mengirim |
| `python xau_signal.py --simple` | mode ringkas |
| `python xau_signal.py --backtest` | bangun tabel kalibrasi |
| `python xau_signal.py --json` | payload lengkap |
| `python journal.py update` | nilai hasil sinyal |
| `python journal.py report` | statistik nyata |
| `python frequency.py 90` | perkiraan frekuensi sinyal |
| `python learn.py power` | berapa data dibutuhkan untuk menyimpulkan |
| `python learn.py walkforward` | apakah sistem stabil lintas periode |
| `python learn.py ablation` | komponen mana yang berguna |
| `python learn.py calibration` | apakah confidence-nya jujur |

## Jadwal

```cron
2 * * * * cd ~/xausignal && python3 xau_signal.py >> ~/.xau.log 2>&1
0 8 * * * cd ~/xausignal && python3 journal.py update >> ~/.xau.log 2>&1
0 3 1 * * cd ~/xausignal && python3 xau_signal.py --backtest >> ~/.xau.log 2>&1
```

## Dokumentasi

| Berkas | Isi |
|---|---|
| [IMPLEMENTASI.md](IMPLEMENTASI.md) | rencana bertahap 5 fase + syarat kelulusan |
| [docs/ARSITEKTUR.md](docs/ARSITEKTUR.md) | keputusan desain dan alasannya |
| [docs/SINYAL.md](docs/SINYAL.md) | arti tiap bagian pesan, grade, skor |
| [docs/CATATAN-BUG.md](docs/CATATAN-BUG.md) | bug yang ditemukan & cara menemukannya |
| [docs/PEMBELAJARAN.md](docs/PEMBELAJARAN.md) | cara memperbaiki sistem tanpa menipu diri |

## Yang belum ada

- Komponen DXY dan yield riil 10Y (driver emas terkuat, belum masuk)
- Trailing stop setelah Target 1
- Validasi *out-of-sample* — backtest saat ini in-sample, jadi optimistis

## Peringatan

Ini alat bantu analisis, bukan nasihat investasi. Sistem sinyal otomatis
secara historis sulit mengalahkan biaya spread dalam jangka panjang. Trading
berisiko dan Anda bisa kehilangan seluruh modal.

Nilai terbesar bot ini kemungkinan bukan pada sinyal belinya, melainkan pada
gerbang *blackout* berita — yang mencegah Anda masuk posisi di menit-menit
paling berbahaya.

## Lisensi

MIT — lihat [LICENSE](LICENSE).
