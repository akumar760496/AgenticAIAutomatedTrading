# STATE — where the project is right now

**Rewritten in place every session. Never appended to.** This file must stay under ~40
lines so it can be read at the start of every session for almost nothing. History belongs
in `DEVLOG.md`.

---

**Updated:** 2026-10-15
**Phase:** 0 — Foundation (not started)
**Active step:** none — planning complete, awaiting inputs
**Package name:** `ativa` (placeholder — needs your decision)

## Done

- All planning docs: `docs/00` … `docs/12`
- Agent operating rules: `CLAUDE.md`, `.claude/skills/` (5 skills)

## Next action

Step 0.1 — repository and toolchain. Needs no market access, no credentials.
Then Step 0.3 — config and secrets scaffold.

## Blocked on you

1. Dhan API access enabled + **websocket instrument cap** (shapes Step 1.2's universe size)
2. Package name to replace `ativa`
3. Threshold sign-off: 1.5× ATR, IV rank 60/70/35, credibility 0.7 (`docs/06` §2)
4. Capital figure and drawdown confirmation (`docs/07` blanks)
5. Realistic weekly hours (under 5 → cut to index-only, `docs/10` §7)
6. Decision-capture mechanism confirmation (`docs/11` §1.2)

## Open risks being watched

- Chain snapshotter not yet running: **every day of delay is permanently lost IV history**
- Historical option-chain data purchasable from a vendor? Would remove the 6-month proxy
