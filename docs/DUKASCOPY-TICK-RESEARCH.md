# Sampel tick Dukascopy — riset offline saja

Workflow `dukascopy-tick-research.yml` hanya `workflow_dispatch`, satu jam UTC,
timeout 5 menit, permission `contents: read`, tanpa secret/jadwal/integrasi trading.
Tidak mengubah strategi, cron produksi, order atau Telegram.

```sh
python3 tests/dukascopy_ticks_test.py -v
python3 scripts/dukascopy_ticks.py --date 2026-09-07 --hour 12 --output /path/new-sample
gh workflow run dukascopy-tick-research.yml -f date=2026-09-07 -f hour=12
```

Batas: tanggal >= 2003-05-06, jam 0..23 sudah selesai; satu request HTTPS,
16 MiB compressed / 64 MiB decoded. Direktori output wajib baru. Kegagalan
HTTP/TLS/LZMA/validasi menggagalkan run; tidak ada fallback data sintetis.
Artifact sukses: `ticks.bi5`, `ticks.csv`, `manifest.json`, `SHA256SUMS`.
Manifest menyimpan URL, UTC boundary [start,end), timestamp aktual, SHA256,
provenance script/commit/run, ukuran, quality counts dan spread USD/oz.
Hash manifest tersedia di SHA256SUMS (bukan hash rekursif dalam dirinya).

## Verifikasi format, bukan tebak digit broker

Sumber library dukascopy-node v1.46.0, commit tetap
`a86e466ffe7476a65886e802d859a9c9bb2c0901`:

- [Decoder](https://github.com/Leo4815162342/dukascopy-node/blob/a86e466ffe7476a65886e802d859a9c9bb2c0901/src/decompressor/index.ts): LZMA, 20 byte, big-endian `>3i2f`.
- [Normalizer](https://github.com/Leo4815162342/dukascopy-node/blob/a86e466ffe7476a65886e802d859a9c9bb2c0901/src/data-normaliser/index.ts): ms dari awal jam, ask, bid, askVolume, bidVolume; harga dibagi decimalFactor.
- [Metadata](https://github.com/Leo4815162342/dukascopy-node/blob/a86e466ffe7476a65886e802d859a9c9bb2c0901/src/utils/instrument-meta-data/generated/instrument-meta-data.json): `xauusd.decimalFactor = 1000`.

URL bulan zero-based: September = `08`. Volume disimpan raw vendor, bukan lot
Exness. Fixture unit test eksplisit sintetis dan tidak dimasukkan artifact.

Parser menolak empty/malformed, timestamp di luar jam/menurun, harga <=0,
ask<bid, volume negatif/NaN/Inf. Duplicate/equal timestamp dan spread nol
dihitung, tidak dibuang. Quality counts nol berarti seluruh sampel lolos
validasi ketat; sampel invalid tidak menghasilkan manifest sukses.

Quote Dukascopy **bukan** quote/execution Exness Standard XAUUSDm.
Satu jam bukan bukti profitabilitas; biaya, slippage, delay manual 15/30/60 detik
belum dimodelkan. Tidak resampling, gap filling, atau implementasi strategi.
