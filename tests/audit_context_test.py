"""Regression checks for initial equity and closed-bar diagnostics."""
import ast
import contextlib
import io
from pathlib import Path
from types import SimpleNamespace
import pandas as pd
import numpy as np
source = Path(__file__).resolve().parents[1] / 'scripts/scalp_direction_audit.py'
names = {'stats', 'analyze_dxy', 'analyze_regime'}
body = [n for n in ast.parse(source.read_text()).body if isinstance(n, ast.FunctionDef) and n.name in names]
ns = {'pd': pd, 'np': np}
exec(compile(ast.Module(body=body, type_ignores=[]), str(source), 'exec'), ns)
def output(fn, *args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf): fn(*args)
    return buf.getvalue()
trades = pd.DataFrame({'t': pd.date_range('2026-01-01', periods=2, tz='UTC'), 'r': [-1., -1.], 'outcome': ['LOSS']*2, 'dir': ['BUY']*2})
assert 'maxDD=2.00R' in output(ns['stats'], trades, 'test'), 'Initial loss missing from DD'
print('initial-equity drawdown passed')
idx = pd.date_range('2026-01-01', periods=202, freq='15min', tz='UTC')
bars = pd.DataFrame({'close': 100 + np.arange(202, dtype=float)**2}, index=idx)
signal = pd.DataFrame({'t': [idx[-1]], 'r': [1.], 'outcome': ['WIN'], 'dir': ['BUY']})
seen = []
ns['dxy_slope_dir'] = lambda frame: seen.append(frame.index[-1]) or 1
output(ns['analyze_dxy'], bars, bars, signal, 'test')
assert seen == [idx[-1]-pd.Timedelta(hours=1)], seen
atr = pd.Series([1.]*201+[100.], index=idx)
ns['xs'] = SimpleNamespace(atr=lambda frame: atr)
text = output(ns['analyze_regime'], bars, signal, 'test')
assert 'LOW ' in text and 'HIGH:' not in text, text
signal['t'] = idx[-1] + pd.Timedelta(minutes=15)
assert 'HIGH:' in output(ns['analyze_regime'], bars, signal, 'test')
print('DXY/ATR unfinished candle excluded; exact close included')
