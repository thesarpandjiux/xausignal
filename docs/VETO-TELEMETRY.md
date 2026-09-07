# H1 veto observation (paper only)

Existing five-minute `scalp.yml` runs collection inside `xau_scalp.py`, using
same fetched closed H1/M15/M5 frames. Existing `/tmp/botscalp` checkout and
`git add -A` save to `bot-scalp` include files below. No new workflow/schedule.

- `veto_state.json`: schema=1 authoritative atomic snapshot; `paper_state`
  isolated active_setup/last, `candidates` keyed by production setup_id,
  `decisions` list. Corrupt JSON raises, never resets dedup.
- `scalp_decisions.csv`: time (UTC offset), source_sha, source, final_reason,
  live_reason, live_ok, h1_veto, other_blockers, paper_blocker, setup_id,
  news_mode, news_event (JSON), gates (JSON), coverage (JSON on evaluated ticks).
- `veto_candidates.csv`: id (setup_id), time, source_sha, source, direction,
  grade, entry, sl, tp1, tp2, rr1, blocker (H1 provenance), other_gates (JSON),
  horizon_hours, fees, outcome, gross_r, closed_at, coverage.

Final reasons: DATA, MARKET_CLOSED, ASIA, H1_VETO, STRUCTURE, SCORE,
NEWS_BLACKOUT, NEWS_QUIET, DEDUP, ELIGIBLE. `live_reason` preserves existing
production text; H1_VETO is not proof of veto-only. Require candidate file
membership: no other production blockers and no isolated paper dedup blocker.
News-untrusted is NEWS_BLACKOUT with null news_event. Asia retains precedence.
`gates` are narrow-bypass trigger results, not new H1 scoring.

Only existing asymmetric SELL H1 veto bypassed in paper builder. Asia, score,
SL/TP, news and production should_send remain unchanged. Production state is
read-only; paper setup and 45-minute cooldown independent. Same setup ID never
re-entered; continuation/reset reuses production helpers. Opposite/continuation
paper setups can overlap: not position sizing or executable portfolio P&L.

Outcome reuses journal.resolve with explicit 2-hour horizon, no global setting
change. Full M5 bars opening at/after evaluation rounded up to next 5-minute
boundary only; last observed close is entry proxy, not fill. Up-to-five-minute
entry lag is recorded. Both TP1/SL in same bar = LOSS; timeout marks final close.
Gross R only: fees/spread/slippage NOT modeled, no net-profit claim. TP2 stored
but not evaluated. Source mismatch, gaps, unavailable bars stay PENDING with
blank gross_r. Old uncovered candidates may remain pending indefinitely; no
backfill fetch added. Evaluator uses contiguous prefix, may settle early hit.
No inferred outcomes for missing intervals or pre-entry partial candle.

JSON writes atomically before derived CSVs. If CSV generation fails, next tick
rebuilds from JSON. Errors emit `::error::veto telemetry failed` in runner logs;
live delivery continues unchanged. Reports must inspect runner errors and use
JSON when CSVs lag. Data failure ticks log DATA then retain original exception.
No hypothetical Telegram messages, live cooldown changes, threshold edits.

## Existing daily/weekly cron prompt instructions (manual update only)

For daily `f1f5fba033c8` and weekly `5f905ae83b91`, add:

Read `bot-scalp` files `scalp_decisions.csv`, `veto_candidates.csv`, and
`veto_state.json` via GitHub API. Filter UTC timestamps into report's WIB
window. Count final_reason and cross-tab h1_veto with other_blockers; never
call all H1_VETO rows veto-only. Count unique candidate id, not repeated ticks.
Report candidates WIN/LOSS/TIMEOUT/PENDING separately, gross R only among
resolved, fees unmodeled; disclose source, source_sha, 2h horizon, entry lag,
coverage gaps and overlapping paper observations. Check latest scalp runner
annotations/errors and state freshness before trusting CSVs. Empty header means
zero observed candidates, not unavailable logging. Missing/stale files mean
coverage unavailable, never zero losses. No profitability or policy changes
from small sample. Existing signals_shadow/journal_shadow are post-veto and
must not substitute for veto_candidates. No new cron needed.
