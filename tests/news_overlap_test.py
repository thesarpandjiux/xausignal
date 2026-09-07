"""Offline regression: overlapping news must preserve entry protection."""
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import xau_scalp as sc

now = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
def event(title, minutes):
    return dict(title=title, time=now + timedelta(minutes=minutes), impact='High', country='USD')

old = event('ISM Manufacturing PMI', -90)
cpi = event('CPI', 10)
assert sc.news_window([old, cpi], now) == ('blackout', cpi), 'Expired event masked CPI blackout'
# Protective windows must beat aggressive mode, regardless of event order.
aggressive = event('ISM Manufacturing PMI', -20)
quiet = event('CPI', -5)
for events in ([aggressive, cpi], [cpi, aggressive], [quiet, cpi]):
    assert sc.news_window(events, now) == ('blackout', cpi)
assert sc.news_window([aggressive, quiet], now) == ('quiet', quiet)
assert sc.news_window([old, aggressive], now) == ('aggressive', aggressive)
assert sc.news_window([old], now) == ('none', None)
assert sc.news_alert_due([old, cpi], now, {}) == (True, cpi), 'Expired event masked countdown'
next_event = event('NFP', 20)
state = {f"alerted_{cpi['time'].isoformat()}": True}
assert sc.news_alert_due([old, cpi, next_event], now, state) == (True, next_event)
assert sc.news_alert_due([cpi], now, state) == (False, None)
print('news window and countdown overlap checks passed')
