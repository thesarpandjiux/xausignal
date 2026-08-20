# Cara Sistem Ini Belajar

## Batas keras yang tidak bisa dilanggar

Pada ~20 sinyal per bulan, inilah data yang dibutuhkan untuk menyimpulkan
sesuatu secara statistik (alpha 5%, power 80%):

| Pertanyaan | Total sinyal | Waktu |
|---|---|---|
| Sistem baik (60%) vs buruk (40%)? | 198 | 10 bulan |
| 60% vs 45%? | 348 | 17 bulan |
| 60% vs 50%? | 778 | 39 bulan |
| 60% vs 55%? | 3.066 | **13 tahun** |

Dan pada 30 sinyal, "win rate 60%" yang terukur sebenarnya bisa **42%–78%**.

Konsekuensinya tegas: **hasil live tidak akan pernah cukup untuk menyetel
parameter.** Menyesuaikan bobot berdasarkan 30 trade terakhir bukan
pembelajaran — itu mencocokkan diri pada kebisingan, dan terasa seperti
kemajuan.

Jalankan `python learn.py power` untuk melihat angkanya sendiri.

## Pembagian tugas

| Sumber data | Untuk apa | Ukuran |
|---|---|---|
| Riwayat Dukascopy | **menyetel** parameter | ribuan sinyal |
| Hasil live (`journal.py`) | **memvalidasi** setelan | puluhan sinyal |

Jangan pernah dibalik. Menyetel pada data live adalah cara paling cepat
merusak sistem yang tadinya bekerja.

## Tangga keamanan

Dari paling aman ke paling berbahaya:

### 1. Kalibrasi ulang — paling aman

```bash
python xau_signal.py --backtest      # tiap awal bulan
python learn.py calibration
```

Menyempurnakan perkiraan keyakinan **tanpa mengubah aturan keputusan sama
sekali**. Tidak bisa merusak sistem karena tidak menyentuh batas keputusannya.

### 2. Menghapus komponen — aman

```bash
python learn.py ablation
```

Komponen yang dihapus tanpa membuat hasil memburuk berarti tidak menghasilkan
apa-apa. Tiap parameter tambahan memperbesar peluang sistem tercocok pada
kebisingan, jadi **menghapus lebih aman daripada menambah**.

Perhatikan peringatan "populasi berubah drastis": mematikan komponen mengubah
sinyal *mana* yang lolos, bukan cuma skornya. Kalau jumlah sinyal berubah >40%,
Anda sedang membandingkan dua sistem berbeda, bukan mengukur efek komponen.

### 3. Menambah informasi baru — sedang

Yang berharga adalah **informasi baru**, bukan transformasi baru dari data yang
sama. Osilator kesekian di atas OHLC yang sama tidak menambah apa pun.

Urutan prioritas:

1. **DXY** — korelasi negatif dolar dengan emas, hubungan paling stabil di
   instrumen ini
2. **Yield riil 10Y** — seri `DFII10` dari FRED, API gratis
3. **Posisi COT** — mingguan, dari CFTC
4. **Arus ETF emas** — GLD/IAU

Tambahkan **satu saja**, lalu ukur. Kalau dua sekaligus, dampaknya tidak bisa
diatribusikan.

### 4. Menyetel parameter — hati-hati

Boleh, tapi hanya pada data historis panjang, dan wajib dikonfirmasi
walk-forward:

```bash
python learn.py walkforward
```

Sistem dengan edge nyata akan untung di **sebagian besar periode**. Sistem yang
cocok-kebetulan akan untung besar di satu periode dan rugi di sisanya —
rata-ratanya terlihat bagus, tapi itu menipu.

Bila simpangan antar periode melebihi rata-ratanya, hasil itu bergantung pada
regime pasar dan tidak bisa dipercaya.

### 5. Machine learning — jangan, untuk sekarang

Model ML butuh data ordo puluhan ribu contoh. Sistem ini menghasilkan ~240
sinyal per tahun. Model apa pun akan menghafal kebisingan dengan sangat
meyakinkan.

Kalau suatu saat ada 5.000+ trade tervalidasi, barulah relevan — dan yang
paling masuk akal saat itu bukan memprediksi arah, melainkan memprediksi
*kapan sistem ini sedang tidak bisa diandalkan*.

## Siklus bulanan

```bash
python journal.py report        # hasil nyata bulan ini
python xau_signal.py --backtest # kalibrasi ulang
python learn.py calibration     # grade masih bermakna?
python learn.py walkforward     # masih stabil lintas periode?
```

Bandingkan `journal.py report` dengan `learn.py calibration`. Bila hasil live
jauh lebih buruk dari backtest, backtest-nya terlalu optimistis — percayai
angka live.

## Siklus perbaikan (tiap 3–6 bulan)

1. `learn.py ablation` — ada komponen yang tidak menghasilkan?
2. Hapus atau tambah **satu** hal
3. `learn.py walkforward` — masih untung di sebagian besar periode?
4. Kalau ya, jalankan live 2 bulan dan bandingkan dengan sebelumnya
5. Kalau tidak, kembalikan perubahannya

Langkah 5 adalah bagian yang paling sering dilewati orang, dan paling penting.

## Umpan balik yang tidak bisa diukur mesin

Kolom `note` di `journal.csv` ada untuk penilaian Anda. Setelah membaca 100
sinyal di grafik, mata Anda akan menangkap pola yang tidak ada di data —
"sinyal jam 2 pagi WIB selalu jelek", "SELL saat harga di dekat all-time high
selalu gagal".

Itu hipotesis, bukan kesimpulan. Tapi hipotesis dari pengamatan langsung jauh
lebih berharga daripada hasil optimasi otomatis pada 30 sampel. Ubah jadi
aturan, lalu uji dengan walk-forward.

Tiga dari enam bug di `CATATAN-BUG.md` ketemu persis dengan cara begitu.

## Tanda sistem sedang rusak

| Gejala | Kemungkinan sebab |
|---|---|
| Live jauh lebih buruk dari backtest | overfitting |
| Untung 3 bulan lalu rugi 3 bulan | ketergantungan regime |
| Frekuensi sinyal anjlok tiba-tiba | sumber data bermasalah |
| Semua sinyal satu arah | ada komponen macet |
| Grade A lebih buruk dari C | grading tidak bermakna |

Bila dua atau lebih muncul bersamaan, hentikan trading dan kembali ke fase
observasi.
