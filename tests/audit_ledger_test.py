#!/usr/bin/env python3
"""Synthetic ledger checks; no historical performance claims."""
import sys
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import scalp_direction_audit as audit

p = pd.DataFrame({'t': pd.to_datetime(['2026-01-05T12:00Z', '2026-01-05T13:00Z']),
                  'dir': ['BUY', 'SELL'], 'r': [-1.25, 2.0]})
l = pd.DataFrame({'t': pd.to_datetime(['2026-01-05T12:00Z', '2026-01-05T14:00Z']),
                  'dir': ['BUY', 'BUY'], 'r': [-1.0, .5]})
assert hasattr(audit, 'compare_ledgers'), 'missing exact ledger comparison'
r = audit.compare_ledgers(p, l)
assert r['counts'] == {'common': 1, 'production_only': 1, 'legacy_only': 1}
assert r['metrics']['production']['ALL']['total_r'] == .75
assert r['metrics']['production']['ALL']['max_entry_order_dd_r'] == 1.25
assert r['metrics']['production']['ALL']['pf'] == 1.6
assert r['metrics']['common_production']['BUY']['total_r'] == -1.25
assert r['metrics']['common_legacy']['BUY']['total_r'] == -1.0
assert r['metrics']['production_only']['BUY']['n'] == 0
try:
    audit.compare_ledgers(pd.concat([p, p.iloc[:1]]), l)
    assert False, 'duplicates accepted'
except ValueError:
    pass
assert audit.compare_ledgers(p.iloc[:0], l.iloc[:0])['counts']['common'] == 0
print('audit_ledger_test: PASS')
