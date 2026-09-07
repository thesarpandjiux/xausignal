"""Executable window contract; synthetic candles ONLY test boundaries, never replay inputs."""
import importlib.util
from pathlib import Path
import pandas as pd
p=Path(__file__).with_name('fetch_window.py')
assert p.exists(), 'Missing explicit-end fetch/selection implementation'
spec=importlib.util.spec_from_file_location('fetch_window',p); m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
i=pd.date_range('2026-07-01', '2026-08-12 01:00',freq='5min',tz='UTC')
f=pd.DataFrame({'open':1.,'high':2.,'low':0.,'close':1.},index=i)
s=m.select_closed(f,'5m')
assert len(s)==5000 and s.index[-1]==pd.Timestamp('2026-08-11 23:55Z')
assert s.index.is_unique and s.index.is_monotonic_increasing
try:m.select_closed(f.iloc[:10],'5m')
except AssertionError:pass
else:raise AssertionError('Insufficient candles must fail')
print('PASS explicit exclusive end, latest5000, closed-bar boundary, insufficient-data rejection')
