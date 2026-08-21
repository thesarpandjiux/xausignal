# Menjalankan Bot 24/7

Cron berjalan di mesin Anda. Komputer mati, cron mati — tidak ada konfigurasi
yang mengubah itu. Anda butuh host yang selalu hidup.

## Perbandingan

| Pilihan | Biaya | Setup | Keandalan | Cocok untuk |
|---|---|---|---|---|
| **GitHub Actions** | gratis | 10 menit | sedang | fase observasi |
| **Oracle Cloud Always Free** | gratis | 1–2 jam | tinggi | jangka panjang |
| **Raspberry Pi** | ±Rp 800rb | 1 jam | tinggi | kendali penuh |
| **VPS berbayar** | ±Rp 30rb/bln | 30 menit | tinggi | praktis |
| **MacBook + launchd** | gratis | 5 menit | rendah | uji coba saja |

---

## 1. GitHub Actions — tercepat

Repo Anda sudah ada di GitHub, jadi tidak perlu akun atau mesin baru.

### Pasang rahasia

Buka repo → **Settings** → **Secrets and variables** → **Actions**.

Tab **Secrets** → New repository secret:

| Nama | Isi |
|---|---|
| `TELEGRAM_BOT_TOKEN` | token dari @BotFather |
| `TELEGRAM_CHAT_ID` | ID dari @userinfobot |
| `TWELVEDATA_API_KEY` | API key Anda |

Tab **Variables** → New repository variable:

| Nama | Isi |
|---|---|
| `ACCOUNT_BALANCE` | `10000` |
| `RISK_PCT` | `0.5` |

Secrets terenkripsi dan tidak muncul di log. Jangan pernah menaruhnya di
Variables — isi Variables terlihat terbuka.

### Aktifkan

```bash
git add .github/workflows/signal.yml
git commit -m "ci: jalankan bot tiap jam di GitHub Actions"
git push
```

Buka tab **Actions** → workflow **signal** → **Run workflow** untuk menguji
sekarang. Setelah itu berjalan otomatis tiap jam.

### Cara state disimpan

Tiap run GitHub Actions dimulai dari mesin kosong. Tanpa penanganan khusus,
`signals.csv` dan `state.json` hilang tiap jam — cooldown tidak berfungsi dan
sinyal yang sama terkirim berulang-ulang.

Workflow menyimpannya ke branch terpisah bernama **`bot-data`**. Untuk mengunduh
riwayat Anda:

```bash
git fetch origin bot-data
git checkout bot-data -- signals.csv journal.csv
```

### Yang perlu diketahui

**Jadwal bersifat "best effort".** Saat beban GitHub tinggi, run bisa tertunda
5–20 menit, kadang terlewat sama sekali. Untuk fase observasi ini dapat
diterima. Untuk uang nyata, kurang ideal.

**Workflow mati setelah 60 hari repo tidak aktif.** GitHub menonaktifkan
schedule otomatis. Cukup push apa pun sebulan sekali, atau tekan Run workflow
manual.

**Repo publik = menit tanpa batas.** Repo privat dibatasi 2.000 menit/bulan;
pemakaian bot ini sekitar 1.000 menit/bulan, jadi masih muat tapi mepet.

---

## 2. Oracle Cloud Always Free — paling andal yang gratis

VM ARM sungguhan, hidup terus, gratis selamanya. Cron biasa bekerja normal dan
state tersimpan di disk tanpa trik apa pun.

```bash
ssh ubuntu@IP-ANDA
sudo apt update && sudo apt install -y python3-pip git
git clone https://github.com/USERNAME/xausignal.git && cd xausignal
pip3 install -r requirements.txt
cp .env.example .env && nano .env
python3 datafeed.py

crontab -e
```

```cron
5 * * * * cd ~/xausignal && /usr/bin/python3 xau_signal.py >> ~/.xau.log 2>&1
0 */12 * * * cd ~/xausignal && /usr/bin/python3 journal.py update >> ~/.xau.log 2>&1
0 3 1 * * cd ~/xausignal && /usr/bin/python3 xau_signal.py --backtest >> ~/.xau.log 2>&1
```

**Perlu diperhatikan:** pendaftaran meminta verifikasi kartu kredit (tidak
ditagih). Oracle berhak menarik kembali instance yang menganggur — bot ini
cukup aktif sehingga umumnya aman. Pilih region terdekat (Singapura) untuk
latensi rendah.

Bonus: dari VM ini Dukascopy kemungkinan besar tidak diblokir, sehingga backtest
Anda dapat riwayat panjang tanpa VPN.

---

## 3. Raspberry Pi — kendali penuh

Pi Zero 2 W (±Rp 400rb) sudah lebih dari cukup. Listriknya sekitar Rp 3.000
per bulan. Setup sama persis seperti Oracle di atas.

Kelebihan: data Anda tidak ke mana-mana, tidak ada pihak ketiga yang bisa
mematikannya. Kekurangan: mati kalau listrik rumah padam.

---

## 4. MacBook + launchd — sementara

`./install-macos.sh` memasang bot lewat launchd, bukan cron.

Bedanya penting: **launchd menjalankan tugas yang terlewat begitu Mac bangun**,
sementara cron melewatkannya begitu saja. Jadi sinyal tidak hilang total, hanya
datang terlambat — kadang berjam-jam setelah candle-nya tutup, saat harga sudah
jauh dari level entry.

Untuk fase observasi ini tidak masalah, karena Anda memang belum mengeksekusi
apa pun dan jurnal tetap menilai berdasarkan waktu sinyal sebenarnya. Untuk uang
nyata, pindah ke pilihan 1–3.

```bash
./install-macos.sh          # pasang
./install-macos.sh status   # cek
./install-macos.sh logs     # lihat log
./install-macos.sh remove   # copot
```

Menjaga Mac tetap bangun saat pasar buka:

```bash
sudo pmset repeat wakeorpoweron MTWRF 06:00:00
pmset -g assertions        # cek apa yang menahan tidur
```

---

## Saran

**Sekarang, untuk Fase 1:** GitHub Actions. Sepuluh menit, gratis, dan cukup
andal untuk mengumpulkan 30 sinyal pertama. Keterlambatan 15 menit tidak
mengubah kesimpulan apa pun saat Anda belum memakai uang.

**Nanti, sebelum Fase 3:** pindah ke Oracle Cloud atau Raspberry Pi. Begitu ada
uang di dalamnya, sinyal yang terlewat atau terlambat mulai berbiaya nyata.

Jangan pasang dua sekaligus — dua host berjalan bersamaan akan mengirim sinyal
ganda dan mengacaukan jurnal Anda.
