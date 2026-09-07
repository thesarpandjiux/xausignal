# BR1 forward shadow

Authorized paper-only activation. Frozen BR1, no tuning, no supply/demand.
Frozen spec SHA256: `fcbac1b3fdcde4e4288ed06a254d37e2704c84f91e0737278d6b8a252a5c61fa`.

Existing `scalp.yml` runs recorder after existing live/journal steps. No new schedule,
worker change, Telegram credentials or send calls. Live builder/module untouched.
Separate `br1_state.json` in existing serialized `bot-scalp` state branch; atomic JSON
contains activation SHA/time, pending, native last/active setup, decisions and candidates.
GitHub artifact `br1-forward-RUN_ID` exposes same state. Never delete/reset state to restart.
Corrupt JSON fails rather than resetting dedup. Existing state persistence must succeed;
artifact alone does not prove persisted restart continuity.

Activation recorded before network, first existing closed bar skipped. Only newest
closed M5 is evaluated, never missed historical entry bars. Missing intervals cancel
pending and consume current bar; no catch-up entries. Source changes cancel. Inputs
are closed prefixes; trusted real calendar and production market/feed gates required.
Missing calendar/feed fails closed. Additional finite, ordered, unique and closed-feed
freshness validation. H1 SELL extreme veto asymmetric; aggressive exception retained.

Cross previous M5 <= current available M15 max(close[-21:-1]) < current close arms BUY;
SELL mirrored. First pending only, level/origin frozen; no same-bar retest, next six
consecutive bars/30 minutes inclusive. Strict wrong-side close, date/session/news/H1/gap
cancels. Touch plus strict reclaim scores structure 1 + native momentum + native sweep,
minimum 2; failed score waits. Eligible consumes even if native 45-minute single-last
cooldown / .75 current M15 ATR continuation rejects. Native M15 inside reset retained.
Native builder reused with private function globals replacing structure only: no global
monkeypatch, no live mutation. Current M5 ATR .8 normal/.6 aggressive, TP1 2R.

Execution latency differs from frozen offline close proxy: decision happens when runner
observes data, entry price remains latest closed-M5 close proxy, NOT executable fill.
Record both bar close and actual decision time/lag. Exit measurement starts first full M5
opening at/after decision (`ceil(5min)`), not candle containing decision; horizon exactly
12 bars, SL first on tie, timeout marked final close. Cost overlay .3 spread + .1 EACH
SIDE slippage = .5/risk subtracted from gross R. Never veto 2-hour resolver. Missing exit
coverage stays pending, source mismatch stays pending; no fabricated results. Overlaps
allowed, no portfolio accounting, broker bid/ask or commission model. Zero candidates
is valid, not profitability evidence.

Checks: `python tests/br1_test.py`; existing CI includes it alongside all prior tests.
Daily review cron unchanged. This deploys recorder only, never trading strategy.
