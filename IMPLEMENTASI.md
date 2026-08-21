# Implementasi Bertahap — XAUUSD Signal Bot

Panduan ini dibagi lima fase. **Jangan lompat fase.** Tiap fase punya syarat
kelulusan yang harus dipenuhi sebelum lanjut. Kalau syarat tidak terpenuhi,
jawabannya adalah mundur atau berhenti — bukan memaksa maju.

Alasannya satu: sistem ini belum pernah diuji di data XAUUSD asli. Tampilannya
rapi dan angkanya meyakinkan, tapi kerapian bukan bukti. Fase-fase di bawah
ada untuk mengubah "kelihatannya bagus" jadi "terbukti bagus" — atau untuk
mengetahui sedini mungkin bahwa ternyata tidak bagus, sebelum ada uang hilang.

| Fase | Isi | Durasi | Uang |
|---|---|---|---|
| 0 | Pasang & verifikasi | 1 hari | — |
| 1 | Observasi tanpa uang | 4–8 minggu | — |
| 2 | Evaluasi & kalibrasi | 1 hari | — |
| 3 | Uang kecil | 4–8 minggu | risiko 0,5% |
| 4 | Perbaikan bertahap | terus-menerus | risiko ≤1% |

---

## Fase 0 — Pasang & verifikasi (1 hari)

Tujuan: memastikan pipa datanya hidup sebelum menaruh harapan apa pun padanya.

### Langkah

```bash
mkdir ~/xausignal && cd ~/xausignal
# salin: xau_signal.py, datafeed.py, journal.py, frequency.py, requirements.txt
pip install -r requirements.txt
```

Siapkan tiga kredensial (semuanya gratis, ±10 menit):

1. **Bot token** — chat @BotFather di Telegram, kirim `/newbot`, ikuti alurnya
2. **Chat ID** — chat @userinfobot, dia balas ID Anda
3. **API key** — daftar di twelvedata.com/register (opsional; tanpa ini sistem
   otomatis pakai Dukascopy yang tidak butuh registrasi)

```bash
cp .env.example .env
nano .env          # isi TELEGRAM_BOT_TOKEN dan TELEGRAM_CHAT_ID
```

Bot membaca `.env` sendiri. **Jangan** taruh di `~/.bashrc` atau `~/.zshrc`:

- macOS memakai **zsh**, jadi `~/.bashrc` tidak pernah dibaca sama sekali
- **cron tidak membaca keduanya** — environment-nya nyaris kosong, sehingga bot
  akan jalan mulus di terminal lalu gagal senyap saat dijadwalkan

Berkas `.env` bekerja di ketiganya.

### Verifikasi

```bash
python datafeed.py          # sumber mana yang hidup
python xau_signal.py --dry-run
```

### ✅ Syarat lulus Fase 0

- [ ] Minimal **2 dari 3** sumber harga hijau di `datafeed.py`
- [ ] Kalender ekonomi terambil, `trusted=True`
- [ ] `--dry-run` menghasilkan pesan tanpa error
- [ ] Satu pesan uji benar-benar sampai di Telegram
- [ ] Harga yang ditampilkan **cocok dengan TradingView** (selisih wajar <$3)

Poin terakhir sering terlewat dan paling penting. Kalau harganya meleset jauh,
seluruh analisis di atasnya tidak ada artinya.

---

## Fase 1 — Observasi tanpa uang (4–8 minggu)

Tujuan: mengumpulkan bukti. Ini fase terpanjang dan paling membosankan, dan
juga yang paling menentukan.

**Tidak ada uang sungguhan di fase ini. Sama sekali.**

### Nyalakan cron

```bash
crontab -e
```

```cron
# Evaluasi tiap jam, 2 menit setelah candle H1 tutup
2 * * * * cd ~/xausignal && /usr/bin/python3 xau_signal.py >> ~/.xau.log 2>&1

# Nilai hasil sinyal tiap pagi
0 8 * * * cd ~/xausignal && /usr/bin/python3 journal.py update >> ~/.xau.log 2>&1
```

### Yang Anda lakukan tiap hari

Ketika sinyal masuk, **jangan buka posisi**. Lakukan ini saja:

1. Buka TradingView, lihat grafik XAUUSD di titik waktu itu
2. Tanya pada diri sendiri: *"Kalau ini uang saya, apakah saya mau masuk?"*
3. Kalau jawabannya **tidak**, catat kenapa

Pertanyaan nomor 2 itu inti Fase 1. Angka win rate akan datang otomatis dari
`journal.py`, tapi penilaian "masuk akal atau tidak" hanya bisa dari mata Anda.
Tiga bug serius di sistem ini ketemu justru dengan cara begitu — bukan dengan
membaca kode, tapi dengan memandangi hasilnya dan merasa ada yang janggal.

### Tiap akhir pekan

```bash
python journal.py report
```

### ✅ Syarat lulus Fase 1

- [ ] Minimal **30 sinyal** sudah selesai (menang/kalah tegas)
- [ ] Win rate **di atas titik impas** dengan selisih >5 poin
- [ ] Ekspektasi **positif** (>+0,10R)
- [ ] Bot berjalan ≥4 minggu tanpa perlu diutak-atik
- [ ] Anda sudah membaca ≥20 sinyal di grafik dan umumnya masuk akal

### ❌ Kalau tidak lulus

| Gejala | Artinya | Tindakan |
|---|---|---|
| <30 sinyal dalam 8 minggu | terlalu ketat | turunkan `THRESHOLD` ke 35, ulangi Fase 1 |
| Ekspektasi negatif | tidak ada edge | **berhenti.** Jangan ke Fase 3 |
| Win rate tinggi tapi ekspektasi negatif | menang kecil, kalah besar | periksa jarak SL |
| Sinyal sering terasa janggal di grafik | ada bug | perbaiki, ulangi dari nol |

Baris kedua adalah yang paling penting dan paling sulit diterima. Ekspektasi
negatif setelah 30+ sinyal berarti sistem ini merugi secara sistematis. Uang
sungguhan tidak akan memperbaikinya — hanya mempercepat kerugiannya.

---

## Fase 2 — Evaluasi & kalibrasi (1 hari)

```bash
python xau_signal.py --backtest     # bangun calibration.json dari data historis
python journal.py report            # hasil nyata Anda
```

Bandingkan keduanya.

### Yang dicari

**1. Apakah backtest dan hasil nyata sejalan?**
Kalau backtest bilang 62% tapi nyatanya 41%, backtest-nya terlalu optimistis
(*overfitting*). Percayai angka nyata, abaikan backtest.

**2. Apakah grade benar-benar berarti?**
Urutan yang diharapkan: A > B > C. Kalau ternyata terbalik — dan ini sangat
mungkin — maka konfluensi penuh bukan penanda kualitas, dan huruf "A" justru
menyesatkan Anda. Sesuaikan ukuran posisi mengikuti data, bukan mengikuti huruf.

**3. Apakah BUY dan SELL seimbang?**
Kalau BUY menang 60% dan SELL menang 35%, sistemnya hanya menangkap tren naik.
Pertimbangkan matikan sinyal SELL sampai sebabnya ketemu.

### ✅ Syarat lulus Fase 2

- [ ] Selisih backtest vs nyata **<15 poin**
- [ ] Anda paham grade mana yang benar-benar unggul (dari data, bukan asumsi)
- [ ] Tidak ada satu arah yang ekspektasinya jelek parah

---

## Fase 3 — Uang kecil (4–8 minggu)

Tujuan: menguji apakah **Anda** bisa menjalankan sistem ini, bukan apakah
sistemnya bekerja. Itu sudah dijawab di Fase 1.

Ini pertanyaan berbeda dan sering lebih sulit. Rasanya sangat lain ketika ada
uang di dalamnya.

### Aturan

```bash
export RISK_PCT=0.5     # setengah persen, bukan satu
```

- Ukuran akun: **maksimal yang Anda ikhlas kehilangan seluruhnya**
- Ambil **hanya grade terbaik** menurut Fase 2
- **Selalu pasang SL bersamaan dengan entry**, bukan nanti
- Target 1 kena → tutup separuh, geser SL ke harga entry
- **Jangan pernah geser SL menjauh.** Sekali saja melanggar, ulangi Fase 3
  dari awal

Aturan terakhir bukan formalitas. Menggeser stop menjauh adalah cara paling
umum akun pemula habis, dan itu selalu terasa masuk akal saat dilakukan.

### Catat manual

Selain jurnal otomatis, catat hal yang tidak bisa diukur mesin: apakah Anda
mengikuti sinyal persis, apakah sempat ragu, apakah pernah melanggar aturan.

### ✅ Syarat lulus Fase 3

- [ ] ≥30 trade dieksekusi
- [ ] Ekspektasi tetap positif dengan uang nyata
- [ ] **Nol pelanggaran aturan**
- [ ] Anda bisa tidur nyenyak saat posisi terbuka

Poin terakhir serius. Kalau ukuran posisinya membuat Anda gelisah, itu terlalu
besar — berapa pun angka statistiknya.

---

## Fase 4 — Perbaikan bertahap

Naikkan risiko ke maksimal 1%. Lalu tambahkan **satu perbaikan saja setiap
kali**, dan ukur dampaknya sebelum menambah yang berikutnya. Kalau dua diubah
bersamaan, Anda tidak akan pernah tahu mana yang berpengaruh.

### Urutan yang saya sarankan

1. **DXY sebagai komponen keenam** — korelasi negatif dolar dengan emas adalah
   hubungan paling stabil di instrumen ini, dan saat ini belum masuk sama sekali
2. **Yield riil 10Y** (seri DFII10 dari FRED, API gratis)
3. **Trailing stop** setelah Target 1
4. **Filter sesi** — kalau data menunjukkan sesi tertentu lebih baik
5. **Kalibrasi ulang bulanan** — `--backtest` tiap awal bulan

### Rawat rutin

```cron
0 3 1 * * cd ~/xausignal && python3 xau_signal.py --backtest >> ~/.xau.log 2>&1
```

---

## Ringkasan perintah

| Perintah | Fungsi |
|---|---|
| `python datafeed.py` | cek sumber data hidup atau mati |
| `python xau_signal.py --dry-run` | lihat sinyal tanpa kirim |
| `python xau_signal.py --simple` | mode ringkas |
| `python xau_signal.py --backtest` | bangun tabel kalibrasi |
| `python xau_signal.py --json` | payload lengkap |
| `python journal.py update` | nilai hasil sinyal |
| `python journal.py report` | statistik nyata |
| `python frequency.py 90` | perkiraan frekuensi sinyal |

## Berkas

| Berkas | Isi |
|---|---|
| `xau_signal.py` | mesin analisis & sinyal |
| `datafeed.py` | lapis data, cache, fallback |
| `journal.py` | pencatat hasil & statistik |
| `frequency.py` | simulator frekuensi |
| `~/.xau_signal/signals.csv` | log tiap evaluasi |
| `~/.xau_signal/journal.csv` | hasil tiap sinyal |
| `~/.xau_signal/calibration.json` | tabel win rate |

---

## Penutup

Fase 1 adalah bagian yang paling ingin dilewati orang, dan paling tidak boleh
dilewati. Godaannya besar: botnya sudah jalan, sinyalnya kelihatan bagus, dan
menunggu dua bulan terasa membuang waktu.

Tapi biaya menunggu adalah dua bulan. Biaya tidak menunggu adalah mengetahui
sistem ini merugi setelah uangnya hilang, bukan sebelum.

*Dokumen ini bukan nasihat investasi. Trading berisiko dan Anda bisa kehilangan
seluruh modal. Tidak ada sistem yang menjamin keuntungan.*
