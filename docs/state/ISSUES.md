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
