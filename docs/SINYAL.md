# Membaca Sinyal

## Mode ringkas (`SIMPLE_MODE=1`)

Direkomendasikan untuk pemakaian sehari-hari. Sistem sudah menyaring untuk
Anda — setiap pesan yang sampai sudah lolos seluruh gerbang, jadi tidak ada
keputusan tambahan yang perlu diambil.

```
🟢 XAUUSD — BELI                          ← arah
27 Mar 2025 01:00 WIB

Masuk di  : $3,347.39 – $3,349.78         ← rentang entry wajar
Stop loss : $3,342.89                     ← WAJIB dipasang bersama entry
Target 1  : $3,360.70  ← tutup separuh di sini
Target 2  : $3,365.95
Ukuran    : 0.17 lot                      ← dari ACCOUNT_BALANCE & RISK_PCT

Kenapa: Tren naik kompak di grafik 4 jam dan harian, harga pas
menyentuh support, momentum mendukung.

⚠️ Kalau harga tutup di bawah $3,342.89, keluar. Jangan ditahan.

Grade A · skor +41 · menang 64% dari 95 sinyal serupa
```

Bila tidak ada peluang: *"Tidak ada sinyal. Tidak perlu melakukan apa-apa."*

### Cara pakai, tiga baris

1. Pesan BELI/JUAL datang → itu saja yang perlu diperhatikan
2. **Pasang SL ke platform bersamaan dengan entry**, bukan nanti
3. Target 1 kena → tutup separuh, geser SL ke harga entry

---

## Mode lengkap

Menampilkan seluruh diagnostik. Berguna saat memeriksa apakah sistem berperilaku
wajar, atau saat mencari bug.

### Grade

Grade adalah **jumlah konfirmasi yang terpenuhi**, tidak lebih.

| Konfirmasi | Artinya |
|---|---|
| Momentum searah | MACD sejalan dengan arah sinyal |
| RSI searah | RSI di sisi yang tepat dari 50 |
| Bias D1 searah | tren harian setuju dengan tren H4 |
| Entry dekat level | harga ≤1.2 ATR dari support/resistance |
| News tidak melawan | sentimen makro tidak berlawanan |

- **Grade A** = 5/5 · **Grade B** = 4/5 · **Grade C** = 3/5 · di bawah 3 = tidak dikirim

Yang paling sering gagal adalah "Entry dekat level" — gampang menemukan tren
jelas, susah menemukan tren jelas yang sedang pullback. Itu sebabnya Grade A
hanya ~10% dari sinyal.

> **Grade A belum tentu lebih baik dari Grade B.** Itu hipotesis, dan pada dua
> simulasi urutannya keluar terbalik. Yang membuktikan hanya `journal.py report`
> dengan data Anda sendiri.

### Skor vs grade

Dua hal berbeda:

- **Skor** = seberapa kuat arahnya (−100..+100)
- **Grade** = seberapa banyak indikator yang sepakat

Sinyal bisa sangat kuat tapi sepihak (skor tinggi, grade C), atau sedang-sedang
saja tapi bulat (skor +41, grade A). Keduanya diukur terpisah.

### Syarat wajib

Kelimanya harus lolos. Satu gagal → tidak ada sinyal. Ditampilkan agar Anda
bisa melihat *kenapa* sebuah setup ditolak.

### Blackout

```
⛔ BLACKOUT — Non-Farm Employment Change (dalam 25 mnt)
```

Setup teknikalnya mungkin bagus, tapi ditahan. Emas adalah aset paling reaktif
terhadap NFP, CPI, dan FOMC — sinyal 30 menit sebelum rilis bukan sinyal,
melainkan lotere.

Blackout juga aktif bila **kalender tidak bisa diambil**, karena tanpa kalender
sistem tidak tahu apakah rilis besar sedang mendekat.

### Win rate

```
📊 Win rate historis: 64% (n=95 sinyal serupa)
```

Diambil dari `calibration.json` hasil backtest, dibucketkan per grade dan per
rentang skor. Bila sampel <20, sistem menolak menyebut angka dan menulis
**"belum terkalibrasi"** — itulah keadaan default sebelum Anda menjalankan
`--backtest`.

---

## Manajemen posisi

| Situasi | Tindakan |
|---|---|
| Sinyal masuk | pasang entry + SL bersamaan |
| Target 1 kena | tutup separuh, SL ke harga entry |
| Target 2 kena | tutup sisanya |
| SL kena | sudah selesai, jangan dibuka lagi |
| 48 jam tanpa gerak | tutup manual, sinyal kedaluwarsa |

**Jangan pernah geser SL menjauh.** Ini cara paling umum akun pemula habis,
dan selalu terasa masuk akal saat dilakukan.
