"""Synthetic regression of actual audit loop: gate before state mutation."""
import ast
from pathlib import Path
from types import SimpleNamespace
import pandas as pd
import numpy as np
src = Path(__file__).resolve().parents[1] / 'scripts/scalp_direction_audit.py'
nodes = [n for n in ast.parse(src.read_text()).body if isinstance(n, ast.FunctionDef) and n.name in ('run', 'cost_r')]
ns = dict(pd=pd, np=np, SPREAD=0., SLIPPAGE=0., AUDIT_RR=2., HORIZON_BARS=1, STRUCTURE_LOOKBACK=20)
ns['xs'] = SimpleNamespace(atr=lambda f: pd.Series(10., index=f.index))
ns['sc'] = SimpleNamespace(ASIA_BLOCK_UTC_UNTIL=7, MIN_TRIGGERS=2, ATR_SL_MULT=.8, trend_h1=lambda f: (1, True), liquidity_sweep=lambda f,d: SimpleNamespace(passed=False))
ns['momentum_passed'] = lambda f,d: True
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(src), 'exec'), ns)
def frame(idx):
    return pd.DataFrame({'open':100., 'high':101., 'low':99., 'close':100.}, index=idx)
h1 = frame(pd.date_range('2025-12-30', periods=150, freq='1h', tz='UTC'))
m15 = frame(pd.date_range('2026-01-04', periods=130, freq='15min', tz='UTC'))
m5 = frame(pd.date_range(end='2026-01-05 07:05Z', periods=124, freq='5min'))
old = ns['run'](h1, m15, m5, lambda h,m:1)
assert old.t.iloc[0] == pd.Timestamp('2026-01-05 06:55Z')
assert old[old.t.dt.hour >= 7].empty
for dedup in (None, .75):
    got = ns['run'](h1, m15, m5, lambda h,m:1, setup_dedup=dedup, block_asia=True)
    assert list(got.t) == [pd.Timestamp('2026-01-05 07:00Z')], got
    assert got.outcome.iloc[0] == 'TIMEOUT' and got.r.iloc[0] == 0.
print('Asia before cooldown/dedup: 06:55 blocked, 07:00 eligible; legacy unchanged')
