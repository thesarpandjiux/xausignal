#!/usr/bin/env python3
"""Synthetic export roundtrip and observed production state checks."""
import sys
import tempfile
import json
import hashlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import scalp_direction_audit as audit
from audit_replay_test import fixture
assert hasattr(audit, 'export_comparison'), 'missing reproducible export'
h, m, f = fixture()
with tempfile.TemporaryDirectory() as d:
    out = Path(d)
    audit.export_comparison(h, m, f, out, horizon=1)
    report = json.loads((out / 'comparison.json').read_text())
    assert report['counts']['production_only'] == 1
    assert json.loads((out / 'production.json').read_text())[0]['dir'] == 'BUY'
    traces = json.loads((out / 'production_trace.json').read_text())
    assert traces[0]['accepted'] is True
    assert traces[0]['state'] == {}
    for name, sha in json.loads((out / 'sha256.json').read_text()).items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == sha
    assert (out / 'ohlc_5m.csv').exists()
print('audit_export_test: PASS')
