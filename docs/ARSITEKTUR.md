# Arsitektur & Keputusan Desain

Dokumen ini menjelaskan *kenapa* sistem dibangun begini, bukan sekadar *apa*.
Setiap keputusan di bawah punya alasan, dan sebagian lahir dari kesalahan yang
baru ketahuan setelah diuji.

## Peta modul

```
datafeed.py     lapis data: cache, fallback berjenjang, deteksi rate-limit
   ↓
xau_signal.py   indikator → skor → confluence gating → grading → format → kirim
   ↓
journal.py      catat hasil nyata tiap sinyal, hitung statistik
frequency.py    simulator frekuensi & corong penyaringan
```

## Alur satu evaluasi

```
1. Ambil OHLC H1 / H4 / D1        (cache → TwelveData → Dukascopy → yfinance)
2. Ambil kalender ekonomi          (cache 6 jam → ForexFactory)
3. Hitung 6 komponen teknikal      → skor -100..+100
4. Hitung sentimen news            → skor -100..+100 (opsional)
5. Komposit = 0.6×teknikal + 0.4×news
6. |komposit| < 40                 → NO-TRADE, selesai
7. Hitung SL/TP dari struktur swing
8. Uji 5 syarat wajib              → satu gagal = NO-TRADE
9. Hitung 5 syarat konfirmasi      → <3 = NO-TRADE; 5/4/3 = grade A/B/C
10. Cari win rate di calibration.json
11. Cek cooldown & jam pasar       → kirim ke Telegram
12. Catat ke signals.csv
```

---

## Keputusan penting

### Indikator dihitung lokal, bukan lewat API

Twelve Data menyediakan endpoint indikator siap pakai, tapi tiap panggilan
memakan kuota dan mengikat sistem pada layanan yang bisa berubah. Menghitung
sendiri dengan pandas berarti nol kuota tambahan, dan backtest bisa jalan
tanpa jaringan sama sekali.

### RSI bersifat *regime-aware*

Di pasar *trending*, RSI 70+ adalah konfirmasi kekuatan — bukan sinyal jual.
*Mean reversion* hanya diterapkan saat pasar *ranging* (|skor tren| < 50).

Versi awal menghukum RSI tinggi tanpa syarat. Akibatnya tren naik kuat pun
tidak pernah menembus ambang: komponen RSI terus membatalkan komponen tren.
Ini masalah klasik sistem skor aditif — indikator dengan asumsi bertentangan
saling menetralkan sampai sistemnya bisu.

### Bobot direnormalisasi saat news tidak tersedia

Memberi skor 0 dengan bobot 0.40 menyeret komposit turun 40% secara palsu —
itu bukan netral, melainkan bias ke NO-TRADE. Ketika sentimen tidak tersedia,
bobot dialihkan penuh ke teknikal.

### Confluence gating, bukan sekadar ambang skor

Skor aditif membuat satu komponen kuat bisa menyeret sinyal lolos sendirian.
Lima **syarat wajib** bisa mem-veto berapa pun skornya:

| Syarat | Mencegah |
|---|---|
| Searah tren H4 | melawan arus |
| Tidak overextended (≤2.5 ATR dari EMA20) | mengejar harga yang sudah lari |
| R:R ke TP1 ≥ 1.5 | trade dengan matematika buruk |
| ATR 0.6–2.2× rata-rata | pasar mati atau chaos pasca-rilis |
| Bebas blackout news | masuk 20 menit sebelum CPI |

Terbukti berfungsi: sinyal dengan skor **+57.7** ditolak karena harga sudah
2.8 ATR di atas EMA20 — persis entry yang terlihat meyakinkan di chart tapi
langsung kena retracement. Dua penolak tersering adalah R:R dan overextension,
artinya sistem paling sering berkata "arahnya jelas, tapi Anda sudah telat".

### Grade = jumlah konfluensi, terpisah dari skor

Versi awal mensyaratkan Grade A punya 5/5 konfirmasi **dan** skor ≥60. Itu
mustahil: konfirmasi "entry dekat level" berarti harga sedang pullback, dan
pullback menurunkan komponen posisi range. Saat semua konfirmasi selaras,
skor tertinggi yang tercapai hanya 56.2.

Sekarang grade murni dari jumlah konfluensi. Skor tetap terukur karena tabel
kalibrasi membucketkan per grade **dan** per rentang skor — biarkan data yang
memutuskan dimensi mana yang memprediksi kemenangan.

**Urutan A > B > C adalah hipotesis, bukan fakta.** Pada dua simulasi berbeda
urutannya keluar terbalik. Fase 2 ada untuk menjawabnya dengan data nyata.

### SL dikunci ke pita 1.0–2.5 ATR

SL murni berbasis struktur menghasilkan jarak yang berayun 0.8–4.0 ATR. Yang
terlalu sempit tersapu noise sejam biasa; yang terlalu lebar membuat "1R" tidak
sebanding antar sinyal — dan kalibrasi win-rate jadi membandingkan hal berbeda.
Menang di risiko 0.8 ATR bukan hal yang sama dengan menang di risiko 4 ATR.

### Antar TP wajib berjarak ≥0.5R

Tanpa aturan ini pernah muncul TP2 dan TP3 terpaut 28 sen — tiga target, dua
di antaranya titik yang sama.

### Blackout asimetris, dan kenapa bot ini tidak trading news

Bot mengevaluasi candle H1 yang sudah tutup, sekali per jam. Bila NFP rilis
19:30, bot baru melihatnya 20:05 — 35 menit setelah pergerakan. Ditambah spread
emas yang melebar dari ~$0,20 ke $5–10 saat rilis, dan slippage yang membuat
stop tereksekusi jauh dari tempatnya. Menghapus blackout tidak menghasilkan
trade news; ia menghasilkan sinyal dari data basi pada spread terburuk.

Namun kedua sisi jendela tidak setara:

- **Sebelum rilis** arah benar-benar tidak diketahui → blokir 60 menit
- **Sesudah rilis** arah sudah terungkap; yang tersisa hanya spread lebar,
  yang normal kembali dalam 15–30 menit → blokir 30 menit

Versi awal memblokir simetris ±60 menit, membuang jam-jam kelanjutan tren yang
justru sering menjadi pergerakan terbaik emas.

**Catatan yang belum terselesaikan:** gerbang volatilitas (`VOL_RANGE` 0,6–2,2×)
kemungkinan masih menolak jam pertama pasca-rilis, karena ATR biasanya melonjak
2–4× lipat. Jadi blackout yang lebih pendek belum tentu menghasilkan lebih
banyak sinyal. Ini harus diukur dengan `learn.py walkforward`, bukan ditebak.

### Kalender gagal = blackout, bukan "aman"

Saat limit unduhan terlampaui, ForexFactory membalas **halaman HTML berisi
"Request Denied" dengan status HTTP 200**, bukan kode error. Parser gagal,
exception tertangkap, kalender jadi daftar kosong — dan bot melanjutkan seolah
tidak ada event apa pun.

Dua lapis penangkal: `datafeed.py` memeriksa `content-type` sebelum parsing,
dan `get_calendar()` mengembalikan flag `trusted`. Bila `False`, sistem
memperlakukannya sama seperti event high-impact sungguhan.

Prinsipnya: **kalender yang diam bukan berarti pasar aman, hanya berarti kita
buta.** Sistem harus membedakan "tidak ada event" dari "tidak tahu ada event".

### Confidence hanya dari data, tidak pernah dikarang

`lookup_confidence()` menolak menampilkan persentase bila sampel <20, dan
menulis "belum terkalibrasi". Angka 100% dari satu sampel adalah kebohongan
yang berbahaya.

### Seri sama kena TP dan SL = kalah

Data H1 tidak memberi tahu mana yang lebih dulu. Menebak ke arah menguntungkan
membuat statistik terlalu manis, jadi `journal.py` selalu menghitungnya kalah.

---

## Batasan yang diketahui

| Batasan | Dampak |
|---|---|
| Backtest in-sample | angka win rate optimistis |
| Tidak ada DXY / yield riil | driver emas terkuat belum masuk |
| Bobot komponen ditentukan manual | belum dioptimasi terhadap data |
| Spread & slippage diabaikan | ekspektasi nyata lebih rendah |
| Dukascopy H1 sedikit tertunda | tidak masalah untuk candle tertutup; jadi masalah bila turun ke M15 |

## Tuas penyetelan

| Konstanta | Default | Efek |
|---|---|---|
| `THRESHOLD` | 40 | 50 memangkas hampir separuh sinyal |
| `MIN_CONFIRMS` | 3 | 4 menyisakan grade A dan B saja |
| `MAX_EXTENSION_ATR` | 2.5 | lebih kecil = lebih ketat soal mengejar harga |
| `MIN_RR` | 1.5 | menaikkan berarti menolak lebih banyak |
| `COOLDOWN_HOURS` | 4 | jarak minimum antar sinyal searah |
| `SL_MIN/MAX_ATR` | 1.0 / 2.5 | pita jarak stop |

Ubah **satu saja setiap kali** dan ukur dampaknya. Kalau dua diubah bersamaan,
Anda tidak akan pernah tahu mana yang berpengaruh.
