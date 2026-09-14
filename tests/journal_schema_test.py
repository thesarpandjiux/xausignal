#!/usr/bin/env python3
"""Offline schema regression; no network, no Telegram."""
import csv
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import journal
import xau_signal as xs
import xau_scalp as sc


def fixture(path):
    old = ['2026-09-07T12:00:00+00:00', 'old', 'BUY', 'A', '3', '3', '0',
           '4500', '4500', '4490', '4515', '', '', '1.5', '3', 'True',
           'twelvedata', 'scalp']
    new = old.copy()
    new[1] = 'new'
    rows = [xs.SIGNAL_COLS[:-1], old, new + ['reason, preserved']]
    with path.open('w', newline='') as f:
        csv.writer(f).writerows(rows)
    return rows


def test_reader_repairs_mixed_width_without_loss():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / 'signals.csv'
        rows = fixture(path)
        with patch.object(journal, 'SIGNALS', path):
            loaded = journal.load_signals()
        assert list(loaded['id']) == ['old', 'new']
        repaired = list(csv.reader(path.open()))
        assert repaired == [xs.SIGNAL_COLS, rows[1] + [''], rows[2]]
        before = path.read_bytes()
        with patch.object(journal, 'SIGNALS', path):
            journal.load_signals()
        assert path.read_bytes() == before


def test_scalp_writers_repair_even_on_duplicate():
    sig = SimpleNamespace(direction='BUY', signal_id=lambda: 'old', setup_id='old')
    for filename, writer, ident in [('signals.csv', sc.log_signal, 'old'),
                                    ('signals_shadow.csv', sc.log_shadow_signal, 'SH-old')]:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / filename
            rows = fixture(path)
            rows[1][1] = ident
            with path.open('w', newline='') as f:
                csv.writer(f).writerows(rows)
            attr = 'LOG_FILE' if filename == 'signals.csv' else 'SHADOW_LOG_FILE'
            with patch.object(sc, 'BASE', Path(d)), patch.object(sc, attr, path):
                writer(sig, True) if filename == 'signals.csv' else writer(sig)
            assert list(csv.reader(path.open())) == [xs.SIGNAL_COLS, rows[1] + [''], rows[2]]


def test_bad_schema_and_failed_replace_preserve_original():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / 'signals.csv'
        rows = fixture(path)
        for bad in [rows + [rows[2] + ['unknown']], [['unknown'] + rows[0][1:]] + rows[1:]]:
            with path.open('w', newline='') as f:
                csv.writer(f).writerows(bad)
            before = path.read_bytes()
            try:
                xs._migrate_signal_log(path)
                assert False, 'must reject unknown schema without discarding fields'
            except ValueError:
                pass
            assert path.read_bytes() == before
        fixture(path)
        before = path.read_bytes()
        with patch('os.replace', side_effect=OSError('disk failure')):
            try:
                xs._migrate_signal_log(path)
                assert False, 'replace failure must propagate'
            except OSError:
                pass
        assert path.read_bytes() == before
        assert list(Path(d).iterdir()) == [path]


if __name__ == '__main__':
    for name, test in list(globals().items()):
        if name.startswith('test_'):
            test()
            print('PASS', name)
