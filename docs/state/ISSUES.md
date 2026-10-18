# ISSUES — development issues, open and recently closed

Managed with the `issue-log` skill. The `## Open` section is read at the start of every
session, so it stays short. Severity: **safety** · **data-correctness** · **correctness** ·
**reliability** · **cosmetic**.

`safety` and `data-correctness` block the step they were found in. Everything else is
logged and work continues — no drive-by fixes inside a step.

---

## Open

### ISS-002 — No option-chain history exists
- **Found:** 2026-10-14, during planning
- **Severity:** data-correctness
- **Symptom:** IV rank needs ~1 year of per-instrument IV history; none exists
- **Impact:** both v1 setups gate on IV rank. Until 6 months accrue, ranks come from a
  realised-vol proxy and must be tagged as such
- **Recoverable:** **no** — this series cannot be reconstructed from price data
- **Status:** open · mitigated by Step 1.1 starting immediately; a vendor purchase would
  close it outright

### ISS-003 — Read-only Dhan API key availability unconfirmed
- **Found:** 2026-10-15, writing docs/11
- **Severity:** safety
- **Symptom:** v1 broker sync needs read access; unclear whether a read-only key is issuable
- **Impact:** if not, mitigation is architectural only — no order adapter in the v1
  codebase, enforced by `import-linter`
- **Finding 2026-10-15:** DhanHQ documents no read-only or data-only token scope. Trading
  APIs (free) and Data APIs (paid) are separate product lines, but the access token is one
  credential and the docs describe no scope choice at generation. **Assume the token can
  place orders.**
- **Status:** open, downgraded to confirmation only · the architectural mitigation (FR-13.6,
  no order adapter in the v1 codebase) is now the primary control, not the fallback
- **Owner:** user — worth one question to Dhan support, but do not block Step 1.1 on it

### ISS-004 — Expiry dates shift off Tuesday for holidays
- **Found:** 2026-10-16, live verification of Step 1.1a
- **Severity:** data-correctness
- **Symptom:** docs/06 states "NIFTY weekly expiry, Tuesday". Dhan's live expiry list for
  NIFTY reads 2026-10-19 (Mon), 10-27 (Tue), 11-03 (Tue), 11-09 (Mon), 11-17 (Tue),
  11-23 (Mon). BANKNIFTY: 10-27 (Tue), 11-23 (Mon), 12-29 (Tue).
- **Cause:** when the Tuesday is a trading holiday the expiry moves to the previous
  trading day. 2026-10-20 is Dussehra and 2026-11-10 is Diwali Balipratipada, which
  accounts for the Monday dates exactly.
- **Impact:** any code that *computes* an expiry from a weekday rule would pick a contract
  that does not exist. The DTE gates in both setups, and the expiry-eve flatten rule in
  v2, would be wrong on those weeks.
- **Mitigation already in place:** `DhanOptionChainAdapter.available_expiries()` reads the
  list from the exchange and never computes it. The adapter was written that way on
  principle; this is the evidence that the principle was right.
- **Status:** open - needs a docs/06 wording fix ("Tuesday, moving earlier for holidays")
  and a rule that nothing anywhere computes an expiry date.

### ISS-005 — Access token expires every 24 hours
- **Found:** 2026-10-16, step 1.1
- **Severity:** reliability
- **Symptom:** the snapshotter cannot run unattended. A web-generated token lasts 24
  hours; the API key + secret flow needs a browser login.
- **Impact:** without a fix, capture depends on a manual paste every morning - and a
  missed day is IV history that cannot be recovered.
- **Options:** (a) daily manual paste, (b) PIN + TOTP via
  `auth.dhan.co/app/generateAccessToken`, which is fully unattended but puts the account
  PIN and TOTP seed in `.env`, (c) the browser consent flow, which needs one login per
  token but keeps the PIN off disk.
- **Decision:** (c), chosen by the user 2026-10-16.
- **Measured 2026-10-17:** a consent-flow token lasts **exactly 24 hours** - issued
  23:46 IST on the 16th, expiring 23:46 IST on the 17th. A token therefore covers all
  three of one session's captures (09:30, 12:30, 15:15) with hours to spare, so the
  scheduler runs a full trading day unattended after a single login.
- **Residual:** one browser login per 24 hours. `ensure_valid_token` refreshes when
  under an hour remains, so the login lands whenever the app is next started or a
  capture begins with a stale token - not on a fixed clock. Unattended operation across
  multiple days still needs (b); that remains the user's security call, not a default.
- **Status:** mitigated - not closed, because an unattended multi-day run is still
  blocked · **Owner:** user

### ISS-006 — Dhan's chain response cannot tell you which session it describes
- **Found:** 2026-10-17, step 1.2
- **Severity:** correctness of any volume-derived metric
- **Symptom:** two feed behaviours that combine badly.
  1. Volume is session-cumulative, and on a closed day Dhan keeps serving the *previous*
     session's figures rather than zeroes. A Saturday survey of NIFTY measured ₹5,126 cr
     of premium turnover - real numbers, from Friday.
  2. The chain's `exchange_time` is the time Dhan answered the request, not the time the
     figures describe. A survey at 00:24 IST on Saturday reported `exchange_time`
     00:24 IST on Saturday over Friday's volume. Age computed as `-0.0` hours.
- **Impact:** an out-of-hours run looks completely healthy. Nothing in the response
  distinguishes live figures from days-old ones, so any ranking, IV rank or turnover
  average built from volume can silently describe a session nobody intended.
- **Fix in place:** freshness is decided from the trading calendar in the caller, never
  from the feed. `trading-desk survey` refuses to run on a non-trading day before it
  spends half an hour on requests (`--force` overrides), and the generated universe
  records `measured_on` so a stale file is visible afterwards. `LiquidityMeasure`
  deliberately carries no timestamp, with the reason in its docstring.
- **Still to watch:** the snapshotter writes `exchange_time` into every Parquet row. That
  is correct as a record of when the row was received, but it must never be read as
  "when this quote was traded". Anything later that computes a time-weighted figure from
  the archive needs to use the IST partition date instead.
- **Status:** mitigated · **Owner:** me

### ISS-007 — Stale `last` prints give deep-ITM strikes impossible IVs
- **Found:** 2026-10-17, re-running Phase 0/1 verification during step 1.2
- **Severity:** data quality; contained, but poisons any chain-wide IV aggregate
- **Symptom:** 49 of 4,942 archived rows carry an implied volatility above 200%, peaking
  at **419%** on MARUTI 9800 CE with spot at 11,489. The `last` on that row is 3,980
  against an intrinsic value of 1,689 - 2.4x intrinsic, which no rational price reaches.
  Several names show the same shape at 3.9-4.8x intrinsic.
- **Cause:** deep in-the-money strikes that did not trade keep a stale last-traded price
  from a session when spot was far higher. Dhan's solver backs an IV out of that price.
  **All 49 rows have volume 0.** Their bid and ask are live and sane, so a two-sided-quote
  filter does not catch them - only the `last` is wrong.
- **Why the data is still correct as captured:** it faithfully records what the exchange
  published, and CLAUDE.md keeps trimming out of captured data. Two things also make it
  harmless where it matters:
  - **ATM IV is unaffected.** Measured over the same archive: 6.2% to 35.5% across 52 ATM
    strikes. The IV-rank series is built from ATM, so it is clean.
  - **Turnover is unaffected.** Premium turnover is `last x volume`, and every affected
    row has volume 0, so a stale price multiplies to nothing.
- **What must not be done:** no mean, median or percentile of IV across a whole chain
  without excluding zero-volume strikes. An unfiltered chain-wide average on this archive
  is wrong by roughly 40 percentage points of standard deviation.
- **Fix in place:** `verify 1_1b` now bounds **ATM** IV (< 100%) rather than chain-wide IV,
  and separately asserts that every implausible IV sits on a strike that did not trade -
  so if a *traded* strike ever shows one, that is a new problem and the check says so.
- **Not changed, deliberately:** `LiquidityMeasure.legs_priced` counts a leg as priced when
  IV is non-null, which includes these rows. Requiring a two-sided quote instead drops
  coverage to 27-33% even for NIFTY, which would invalidate the 25% floor set the same
  day. Recalibrate both together after the first real survey, not one without the other.
- **Status:** open, contained · **Owner:** me

### ISS-006b — Two entry points could archive a non-trading day
- **Found:** 2026-10-17, same re-run
- **Severity:** would have been unrecoverable archive corruption
- **Symptom:** `scripts/snapshot_once.py` and `scripts/verify/step_1_1b.py` both built a
  `ChainSnapshotter` and captured with no calendar check. Only the CLI had one. Running
  either on a Saturday would have written a `date=2026-10-17` partition holding Friday's
  quotes - and because partitions are keyed by IST date, that phantom session would be
  indistinguishable from a real one in the IV-rank history.
- **Caught by:** running `make verify-phase PHASE=0` and the 1.x steps after step 1.1c,
  which had not been done since the guard was added to the CLI only.
- **Fix:** the guard moved into `ChainSnapshotter.run()`, which raises `MarketClosedError`
  on a non-trading day. Every entry point inherits it; `force=True` is the single
  deliberate override and the scheduler never sets it. mypy flagged all four call sites
  the moment the constructor changed, which is how the two unguarded ones surfaced.
- **Lesson recorded:** a guard placed in one caller is not a guard. It belongs at the point
  of the write.
- **Status:** closed · **Owner:** me

### ISS-008 — Muhurat session hours are not modelled
- **Found:** 2026-10-18, step 1.3a
- **Severity:** low, but silently wrong if it fires
- **Symptom:** `config/exchange_rules.yaml` carries one session, 09:15-15:30 IST. Muhurat
  trading - one evening a year, around 18:00-19:00 - runs entirely outside it.
  `config/holidays.yaml` already lists 2026-11-08 as a special session.
- **Impact:** every Muhurat tick would be rejected as `outside_session` and that day would
  produce no bars. The rejections are recorded, so the data is not corrupted and the loss
  is visible - but nothing currently reads them and nobody would notice on the night.
- **Why not fixed now:** the exchange publishes Muhurat timings by circular a few weeks
  ahead, and inventing plausible hours is worse than having none. It needs a
  `special_session_hours` block keyed by date, filled from the circular.
- **Due before:** 2026-11-08.
- **Status:** open · **Owner:** me (needs the NSE circular, same one as the holiday list)

## Closed

### ISS-001 — Dhan websocket instrument cap unknown
- **Found:** 2026-10-14, during planning
- **Severity:** correctness
- **Symptom:** Step 1.2's universe size (~27 instruments) was unverified against the live
  subscription limit
- **Fixed:** 2026-10-15 — not a constraint. DhanHQ v2 allows **5,000 instruments per
  websocket connection** and **5 concurrent connections per user**. v1 needs ~27 plus
  option chains, which is ~0.5% of one connection's capacity.
- **Verified:** official docs, https://dhanhq.co/docs/v2/live-market-feed/ — "up to 5000
  instruments on each connection", max 100 instruments per subscription message, a 6th
  connection disconnects the first with code 805.
- **Consequence:** Step 1.2's universe is not capped by the feed. The real constraint on
  universe size is option-chain snapshot volume and LLM cost per cycle, not subscriptions.
