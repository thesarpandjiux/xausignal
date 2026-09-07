"""Exercise actual audit function without market feeds."""
import ast
import math
from pathlib import Path
from typing import Any
source = Path(__file__).resolve().parents[1] / 'scripts/scalp_direction_audit.py'
fn = next(n for n in ast.parse(source.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == 'cost_r')
ns: dict[str, Any] = {'SPREAD': 0., 'SLIPPAGE': 0., 'np': __import__('numpy')}
exec(compile(ast.Module(body=[fn], type_ignores=[]), str(source), 'exec'), ns)
f = ns['cost_r']
assert f('WIN', 2., 4.) == 2.
assert f('LOSS', 2., 4.) == -1.
ns.update(SPREAD=.3, SLIPPAGE=.1)
assert math.isclose(f('WIN', 2., 4.), 1.875), 'Round-trip costs missing'
assert math.isclose(f('LOSS', 2., 4.), -1.125)
for direction in (1, -1):
    for move in (-2., 0., 2.):
        assert math.isclose(f('TIMEOUT', 2., 4., entry=100., exit_price=100.+move, direction=direction), direction*move/4.-.125)
for kwargs in ({}, {'entry':100., 'exit_price':float('nan'), 'direction':1}):
    try: f('TIMEOUT', 2., 4., **kwargs)
    except ValueError: pass
    else: raise AssertionError('Missing/invalid timeout exit must fail')
for risk in (0., -1., float('nan')):
    try: f('LOSS', 2., risk)
    except ValueError: pass
    else: raise AssertionError('Invalid risk must fail')
print('audit round-trip, BUY/SELL timeout, invalid-input checks passed')
