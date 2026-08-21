# Catatan Bug

Delapan cacat serius ditemukan selama pengembangan. Semuanya sudah diperbaiki.
Dokumen ini disimpan karena polanya lebih berguna daripada daftarnya.

**Tidak satu pun ketemu dengan membaca kode.** Semuanya ketemu dengan
menjalankan sistem dan memandangi hasilnya.

---

## 1. RSI membatalkan tren

**Gejala** — tren naik kuat tidak pernah menembus ambang sinyal.

**Sebab** — RSI dihukum sebagai *overbought* di atas 70 tanpa syarat. Di tren
naik, RSI memang tinggi, jadi komponen RSI terus membatalkan komponen tren.

**Perbaikan** — RSI jadi *regime-aware*: konfirmasi saat trending, mean
reversion hanya saat ranging.

**Pola** — dua indikator dengan asumsi bertentangan, dijumlahkan, saling
menetralkan sampai sistemnya bisu.

---

## 2. Bobot news menyeret skor turun

**Gejala** — komposit selalu di bawah ambang saat LLM tidak dikonfigurasi.

**Sebab** — skor news 0 dengan bobot 0.40 memotong komposit 40%. Itu bukan
netral, melainkan bias sistematis ke NO-TRADE.

**Perbaikan** — bobot direnormalisasi penuh ke teknikal saat news tidak ada.

**Pola** — "tidak ada data" diperlakukan sebagai "data bernilai nol".

---

## 3. Grade A mustahil tercapai

**Gejala** — Grade A muncul 0% dari 346 sinyal simulasi.

**Sebab** — Grade A mensyaratkan 5/5 konfirmasi **dan** skor ≥60. Konfirmasi
"entry dekat level" berarti pullback, dan pullback menurunkan komponen posisi
range. Saat semua konfirmasi selaras, skor tertinggi hanya 56.2.

**Perbaikan** — grade murni dari jumlah konfluensi; skor diukur terpisah lewat
bucket kalibrasi.

**Pola** — sama seperti #1: dua kriteria bertentangan, di-AND-kan, hasilnya
himpunan kosong. Tanpa pengukuran, bot akan berjalan berbulan-bulan tanpa
Grade A dan tidak ada yang menyadarinya.

---

## 4. Kalender gagal senyap

**Gejala** — tidak ada. Itu justru masalahnya.

**Sebab** — saat limit unduhan terlampaui, ForexFactory membalas halaman HTML
"Request Denied" dengan **status HTTP 200**. `r.json()` melempar exception,
exception tertangkap, fungsi mengembalikan daftar kosong — dan bot melanjutkan
seolah tidak ada agenda ekonomi sama sekali.

Artinya bot bisa mengirim sinyal BELI lima menit sebelum NFP, dengan penuh
keyakinan, karena kalendernya kosong.

**Perbaikan** — periksa `content-type` sebelum parsing, dan kembalikan flag
`trusted`. Bila `False`, perlakukan sebagai blackout.

**Pola** — kegagalan paling berbahaya adalah yang tidak berisik. Sistem harus
membedakan "tidak ada event" dari "tidak tahu ada event".

---

## 5. TP berdempetan & jarak SL liar

**Gejala** — di satu contoh: TP2 $3,365.67 dan TP3 $3,365.95, terpaut 28 sen.
Di contoh lain: SL 0.8 ATR pada satu sinyal, 4.0 ATR pada sinyal berikutnya.

**Sebab** — target ketiga diisi kelipatan 3.5R yang kebetulan mendarat di atas
TP2, tanpa pemeriksaan jarak. Dan SL berbasis struktur murni tidak punya
batas bawah maupun atas.

**Dampak** — SL 0.8 ATR lebih sempit dari pergerakan normal satu jam, jadi
tersapu terus. Dan karena "1R" tidak sebanding antar sinyal, kalibrasi win-rate
membandingkan hal yang berbeda.

**Perbaikan** — SL dikunci ke pita 1.0–2.5 ATR; antar TP wajib ≥0.5R.
Diverifikasi pada 332 sinyal: nol pelanggaran.

**Pola** — hanya terlihat setelah output nyata dipandangi, bukan saat kode
dibaca.

---

## 6. Cache meleset karena kunci menyertakan jumlah bar

**Gejala** — permintaan 200 bar setelah menyimpan 400 bar tetap menembak API.

**Sebab** — kunci cache berisi `bars`, jadi `ohlc_1h_400` dan `ohlc_1h_200`
dianggap dua entri berbeda.

**Perbaikan** — kunci per interval saja, lalu potong sesuai kebutuhan.

**Pola** — pemborosan kuota yang tidak menimbulkan error, jadi tidak terlihat
sampai diukur.

---

## 7. Kredensial tidak terbaca (zsh & cron)

**Gejala** — token dimasukkan ke `~/.bashrc`, bot tetap bilang kredensial kosong.

**Sebab** — dua lapis. macOS memakai **zsh**, yang tidak pernah membaca
`~/.bashrc`. Dan bahkan bila diperbaiki ke `~/.zshrc`, **cron tidak membaca
berkas shell mana pun** — environment cron nyaris kosong.

Bug kedua lebih jahat: bot jalan sempurna saat diuji manual, lalu gagal senyap
begitu dijadwalkan.

**Perbaikan** — `datafeed.load_env()` membaca `.env` langsung dari disk saat
impor. Bekerja di zsh, bash, dan cron. Variabel yang sudah ada di environment
tidak ditimpa.

**Pola** — asumsi tentang lingkungan pengguna yang tidak pernah diuji. Saya
menulis `.bashrc` di dokumen tanpa mempertimbangkan pengguna memakai Mac.

---

## 8. Parser .env menelan tanda kutip

**Gejala** — Telegram membalas `404 Not Found`, Twelve Data `401 Unauthorized`,
padahal token sudah diisi benar.

**Sebab** — parser melepas tanda kutip SEBELUM membuang komentar. Pada baris:

```
export TELEGRAM_BOT_TOKEN="789:AAF"      # dari @BotFather
```

karakter terakhir baris adalah `r`, bukan kutip. Syarat "diawali dan diakhiri
kutip" tidak terpenuhi, jadi kutipnya tidak dilepas. Token tersimpan sebagai
`"789:AAF"` lengkap dengan petik, lalu ditempel ke URL:

```
https://api.telegram.org/bot"789:AAF"/sendMessage   → 404
```

Nilai kosong `""` jadi lebih jahat lagi: menjadi string dua karakter yang
dianggap "sudah terisi", sehingga API key palsu dikirim ke server → 401.

**Perbaikan** — buang komentar lebih dulu; bila nilai diawali kutip, ambil
sampai kutip penutup (sehingga `#` di dalam nilai tetap aman). Ditambah
peringatan di `datafeed.py` bila nilai masih mengandung kutip, spasi, atau
pagar.

**Pola** — urutan operasi yang salah pada kasus yang tidak diuji. Uji awal saya
memakai `.env` tanpa komentar di belakang, padahal template `.env.example`
buatan saya sendiri PENUH komentar di belakang. Saya menguji bentuk yang tidak
akan pernah dipakai orang.

---

## Kesimpulan

Enam dari delapan bug menghasilkan sistem yang **tetap berjalan tanpa error**.
Tidak ada exception, tidak ada log merah — hanya perilaku yang salah secara
diam-diam.

Karena itu `IMPLEMENTASI.md` mewajibkan fase observasi: jalankan `--dry-run`
beberapa hari, baca pesannya, dan tanyakan *"kalau ini uang saya, apakah saya
mau masuk?"* Kalau jawabannya tidak, di situ ada bug yang belum ketahuan.
