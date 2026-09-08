"""Isolated Dukascopy XAUUSD research; no trading integrations."""
import argparse
import csv
import hashlib
import json
import lzma
import os
from pathlib import Path
import statistics
import urllib.request
import math
import struct

from datetime import datetime, timedelta, timezone
import re

PRICE_SCALE = 1000


def sample_hour(day, hour, now=None):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day) or not re.fullmatch(r'\d{1,2}', hour):
        raise ValueError('expected YYYY-MM-DD and UTC hour 0..23')
    start = datetime.fromisoformat(day).replace(hour=int(hour), tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    if start < datetime(2003, 5, 6, tzinfo=timezone.utc) or start + timedelta(hours=1) > now:
        raise ValueError('require completed hour on/after 2003-05-06 UTC')
    url = (f'https://datafeed.dukascopy.com/datafeed/XAUUSD/{start.year}/'
           f'{start.month - 1:02}/{start.day:02}/{start.hour:02}h_ticks.bi5')
    return start, url


def parse_ticks(data):
    if not data or len(data) % 20:
        raise ValueError('empty or malformed 20-byte tick records')
    rows = list(struct.iter_unpack('>3i2f', data))
    previous = -1
    for ms, ask, bid, av, bv in rows:
        if not 0 <= ms < 3600000 or ms < previous:
            raise ValueError('timestamp outside hour or out of order')
        if bid <= 0 or ask < bid:
            raise ValueError('nonpositive or crossed quote')
        if not all(math.isfinite(v) and v >= 0 for v in (av, bv)):
            raise ValueError('nonfinite or negative volume')
        previous = ms
    return rows, {
        'ticks': len(rows), 'duplicate_records': len(rows) - len(set(rows)),
        'equal_timestamps': sum(a[0] == b[0] for a, b in zip(rows, rows[1:])),
        'zero_spreads': sum(a == b for _, a, b, _, _ in rows),
        'crossed_quotes': 0, 'nonpositive_quotes': 0, 'invalid_volumes': 0,
        'out_of_order': 0, 'out_of_hour': 0, 'malformed_records': 0,
    }


SOURCE = 'https://github.com/Leo4815162342/dukascopy-node/blob/a86e466ffe7476a65886e802d859a9c9bb2c0901/'


def collect(day, hour, output):
    start, url = sample_hour(day, hour)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    # ponytail: one hour only, 16 MiB compressed / 64 MiB decoded; no bulk crawler.
    with urllib.request.urlopen(url, timeout=60) as response:
        if response.status != 200:
            raise ValueError(f'HTTP {response.status}')
        raw = response.read(16 * 1024 * 1024 + 1)
        content_type = response.headers.get('Content-Type')
    if not raw or len(raw) > 16 * 1024 * 1024:
        raise ValueError('empty or oversized response')
    (output / 'ticks.bi5').write_bytes(raw)
    decoder = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE, memlimit=128 * 1024 * 1024)
    decoded = decoder.decompress(raw, max_length=64 * 1024 * 1024 + 1)
    if len(decoded) > 64 * 1024 * 1024 or not decoder.eof or decoder.unused_data:
        raise ValueError('oversized, truncated or trailing LZMA data')
    rows, quality = parse_ticks(decoded)
    def timestamp(ms):
        return (start + timedelta(milliseconds=ms)).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    with (output / 'ticks.csv').open('w', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['timestamp_utc', 'bid', 'ask', 'spread_usd', 'bid_volume_raw', 'ask_volume_raw'])
        for ms, ask, bid, av, bv in rows:
            writer.writerow([timestamp(ms), f'{bid / PRICE_SCALE:.3f}', f'{ask / PRICE_SCALE:.3f}',
                             f'{(ask - bid) / PRICE_SCALE:.3f}', bv, av])
    spreads = sorted((ask - bid) / PRICE_SCALE for _, ask, bid, _, _ in rows)
    manifest = {
        'source': 'Dukascopy', 'instrument': 'XAUUSD', 'url': url,
        'retrieved_at_utc': datetime.now(timezone.utc).isoformat(),
        'http_status': 200, 'content_type': content_type,
        'window_start_inclusive': start.isoformat(),
        'window_end_exclusive': (start + timedelta(hours=1)).isoformat(),
        'first_tick': timestamp(rows[0][0]), 'last_tick': timestamp(rows[-1][0]),
        'schema': '>3i2f: ms_from_hour,ask_integer,bid_integer,ask_volume,bid_volume',
        'price_divisor': PRICE_SCALE, 'volume_units': 'raw vendor units; not Exness lots',
        'schema_sources': [SOURCE + path for path in [
            'src/decompressor/index.ts', 'src/data-normaliser/index.ts',
            'src/utils/instrument-meta-data/generated/instrument-meta-data.json']],
        'quality': quality, 'decoded_bytes': len(decoded), 'compressed_bytes': len(raw),
        'spread_usd': {'min': spreads[0], 'median': statistics.median(spreads),
                       'p95_nearest_rank': spreads[math.ceil(.95 * len(spreads)) - 1],
                       'max': spreads[-1], 'mean': statistics.mean(spreads)},
        'max_intertick_gap_ms': max((b[0] - a[0] for a, b in zip(rows, rows[1:])), default=0),
        'sha256': {name: hashlib.sha256((output / name).read_bytes()).hexdigest()
                   for name in ['ticks.bi5', 'ticks.csv']},
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'github_sha': os.environ.get('GITHUB_SHA'), 'github_run_id': os.environ.get('GITHUB_RUN_ID'),
        'limitations': 'Dukascopy quotes, not Exness execution/spread/slippage. No resampling or gap filling. Duplicates retained. Invalid sample fails closed.',
    }
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2, allow_nan=False) + '\n')
    names = ['ticks.bi5', 'ticks.csv', 'manifest.json']
    (output / 'SHA256SUMS').write_text(''.join(
        f'{hashlib.sha256((output / name).read_bytes()).hexdigest()}  {name}\n' for name in names))
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', required=True)
    parser.add_argument('--hour', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(collect(args.date, args.hour, args.output), indent=2))

