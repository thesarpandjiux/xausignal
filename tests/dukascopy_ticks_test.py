"""Deterministic synthetic fixtures only; never market observations."""
import importlib.util
from pathlib import Path
import struct
import unittest

PATH = Path(__file__).resolve().parents[1] / 'scripts/dukascopy_ticks.py'


class TickTests(unittest.TestCase):
    def test_schema_scale(self):
        self.assertTrue(PATH.exists(), 'tick parser missing')
        spec = importlib.util.spec_from_file_location('ticks', PATH)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        rows, quality = module.parse_ticks(struct.pack('>3i2f', 123, 3500123, 3500000, 1.5, 2.5))
        self.assertEqual(rows, [(123, 3500123, 3500000, 1.5, 2.5)])
        self.assertEqual(module.PRICE_SCALE, 1000)
        self.assertEqual(quality['ticks'], 1)

    def test_reject_invalid_records(self):
        spec = importlib.util.spec_from_file_location('ticks', PATH)
        assert spec and spec.loader
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        good = (123, 3500123, 3500000, 1.5, 2.5)
        bad = [b'', b'x', struct.pack('>3i2f', *good) + b'x']
        for row in [(-1, *good[1:]), (3600000, *good[1:]),
                    (123, 1, 2, 1., 1.), (123, 0, 0, 1., 1.),
                    (123, 2, 1, float('nan'), 1.),
                    (123, 2, 1, 1., float('inf')),
                    (123, 2, 1, -1., 1.)]:
            bad.append(struct.pack('>3i2f', *row))
        bad.append(struct.pack('>3i2f', *good) + struct.pack('>3i2f', 122, *good[1:]))
        for data in bad:
            with self.subTest(data=data), self.assertRaises(ValueError):
                m.parse_ticks(data)
        rows, q = m.parse_ticks(struct.pack('>3i2f', *good) * 2)
        self.assertEqual(q['duplicate_records'], 1)
        self.assertEqual(q['equal_timestamps'], 1)
        self.assertEqual(q['crossed_quotes'], 0)

    def test_bounded_hour(self):
        spec = importlib.util.spec_from_file_location('ticks', PATH)
        assert spec and spec.loader
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        self.assertTrue(hasattr(m, 'sample_hour'), 'input validation missing')
        from datetime import datetime, timezone
        now = datetime(2026, 9, 8, tzinfo=timezone.utc)
        start, url = m.sample_hour('2026-09-07', '12', now)
        self.assertEqual(url, 'https://datafeed.dukascopy.com/datafeed/XAUUSD/2026/08/07/12h_ticks.bi5')
        for day, hour in [('2026-02-30', '12'), ('2026-09-08', '0'),
                          ('1999-01-01', '0'), ('2026-09-07', '24'),
                          ('20260907', '12'), ('2026-09-07', '-1'),
                          ('2026-09-07', '1;id')]:
            with self.subTest(day=day, hour=hour), self.assertRaises(ValueError):
                m.sample_hour(day, hour, now)

    def test_artifact_pipeline(self):
        spec = importlib.util.spec_from_file_location('ticks', PATH)
        assert spec and spec.loader
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        self.assertTrue(hasattr(m, 'collect'), 'collector missing')
        import lzma
        import tempfile
        import hashlib
        import json
        from unittest.mock import patch
        from io import BytesIO
        raw = lzma.compress(struct.pack('>3i2f', 123, 3500123, 3500000, 1.5, 2.5), format=lzma.FORMAT_ALONE)
        class Response(BytesIO):
            status = 200
            headers = {'Content-Type': 'application/octet-stream'}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'sample'
            with patch.object(m.urllib.request, 'urlopen', return_value=Response(raw)):
                m.collect('2026-09-07', '12', out)
            manifest = json.loads((out / 'manifest.json').read_text())
            self.assertEqual(manifest['quality']['ticks'], 1)
            self.assertEqual(manifest['spread_usd']['min'], .123)
            self.assertIn('3500.000,3500.123', (out / 'ticks.csv').read_text())
            for name, digest in manifest['sha256'].items():
                self.assertEqual(hashlib.sha256((out / name).read_bytes()).hexdigest(), digest)
            with self.assertRaises(FileExistsError):
                m.collect('2026-09-07', '12', out)
        for bad in [b'', b'html', raw[:-3], raw + b'junk']:
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / 'bad'
                with patch.object(m.urllib.request, 'urlopen', return_value=Response(bad)):
                    with self.assertRaises((ValueError, lzma.LZMAError, EOFError)):
                        m.collect('2026-09-07', '12', out)
                self.assertFalse((out / 'manifest.json').exists())


if __name__ == '__main__':
    unittest.main()
