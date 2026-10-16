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
