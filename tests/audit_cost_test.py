"""Test actual audit cost function without loading market feeds."""
import ast
from pathlib import Path
source = Path(__file__).resolve().parents[1] / 'scripts/scalp_direction_audit.py'
fn = next(n for n in ast.parse(source.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == 'cost_r')
ns = {'SPREAD': 0.0, 'SLIPPAGE': 0.0}
exec(compile(ast.Module(body=[fn], type_ignores=[]), str(source), 'exec'), ns)
for outcome, expected in [('WIN', 2.0), ('LOSS', -1.0), ('TIMEOUT', 0.0)]:
    actual = ns['cost_r'](outcome, 2.0, 4.0)
    assert actual == expected, (outcome, actual, expected)
# Preserve existing nonzero-cost behavior; round-trip valuation is separate.
ns.update(SPREAD=0.3, SLIPPAGE=0.1)
assert ns['cost_r']('WIN', 2.0, 4.0) == 1.9375
assert ns['cost_r']('LOSS', 2.0, 4.0) == -1.0625
assert ns['cost_r']('TIMEOUT', 2.0, 4.0) == -0.0625
print('audit outcome zero/nonzero-cost checks passed')
