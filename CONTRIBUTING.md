# Berkontribusi

## Sebelum mengirim PR

```bash
python tests/smoke_test.py
python xau_signal.py --demo
```

Keduanya harus lolos tanpa jaringan.

## Aturan yang tidak bisa ditawar

**1. Jangan pernah menampilkan angka confidence tanpa dasar data.**
`lookup_confidence()` mengembalikan `None` bila sampel <20. Jangan diubah jadi
menampilkan tebakan. Angka yang terlihat meyakinkan tapi tak berdasar lebih
berbahaya daripada tidak ada angka sama sekali.

**2. Kegagalan harus berisik.**
Bila sumber data gagal, sistem harus menahan sinyal — bukan melanjutkan dengan
data kosong. Lihat bug #4 di `docs/CATATAN-BUG.md`.

**3. Setiap invarian yang pernah rusak wajib punya uji.**
Perbaiki bug → tambahkan pemeriksaannya di `tests/smoke_test.py`.

**4. Ubah satu parameter setiap kali.**
Kalau dua diubah bersamaan, dampaknya tidak bisa diatribusikan.

## Menambah komponen teknikal baru

1. Tambahkan `Component` di `technical_score()` dengan bobot
2. Pastikan total bobot tetap dinormalisasi
3. Jalankan `python frequency.py 90` — bandingkan frekuensi sebelum & sesudah
4. Jalankan `--backtest` — bandingkan win rate

Bila frekuensi anjlok drastis, komponen baru mungkin bertentangan dengan yang
sudah ada. Itu pola bug #1 dan #3.

## Rahasia

Jangan pernah commit `.env`, token, atau API key. Kalau terlanjur ter-push,
anggap token itu sudah bocor — cabut dan buat baru, jangan sekadar dihapus dari
riwayat.
