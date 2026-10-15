# ISSUES — development issues, open and recently closed

Managed with the `issue-log` skill. The `## Open` section is read at the start of every
session, so it stays short. Severity: **safety** · **data-correctness** · **correctness** ·
**reliability** · **cosmetic**.

`safety` and `data-correctness` block the step they were found in. Everything else is
logged and work continues — no drive-by fixes inside a step.

---

## Open

### ISS-001 — Dhan websocket instrument cap unknown
- **Found:** 2026-10-14, during planning
- **Severity:** correctness
- **Symptom:** Step 1.2's universe size (~27 instruments) is unverified against the plan's
  live-subscription limit
- **Impact:** if the cap is below ~30, Setup A's breadth shrinks and the universe ranking
  must select fewer names
- **Status:** open · **Blocks:** 1.2, 1.3 · **Owner:** user (ask Dhan)

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
- **Status:** open · **Owner:** user (ask Dhan)

## Closed

_None yet._
