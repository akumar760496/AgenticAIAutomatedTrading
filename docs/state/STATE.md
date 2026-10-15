# STATE — where the project is right now

**Rewritten in place every session. Never appended to.** This file must stay under ~40
lines so it can be read at the start of every session for almost nothing. History belongs
in `DEVLOG.md`.

---

**Updated:** 2026-10-15
**Phase:** 0 — Foundation (in progress)
**Active step:** 0.3 — configuration and secrets (next)
**Package name:** `trading_desk` (decided 2026-10-15)

## Done

- All planning docs: `docs/00` … `docs/14`
- Agent operating rules: `CLAUDE.md`, `.claude/skills/` (5 skills)
- **Step 0.1** — repo, Python 3.12, ruff/mypy/pytest/hypothesis/pre-commit, 7 layer
  packages, `make check`, verify harness. Verified, both guards induced.
- **Step 0.2** — `.importlinter`, 4 contracts. Verified, violation induced.

## Next action

Step 0.3 — settings via pydantic-settings, `.env.example`, fail-fast on missing keys,
log redaction filter + test. Needs no credentials to build; you fill `.env` later.
Then 0.4 (value objects, `Money`), 0.5 (Docker Postgres + Redis), 0.6 (ports and fakes).

## Blocked on you

1. **Dhan Data API subscription (₹499+tax/month) + access token in `.env`** — the only
   blocker for Step 1.1. Feed cap is a non-issue: 5,000 instruments/connection (ISS-001 closed)
2. Threshold sign-off: 1.5× ATR, IV rank 60/70/35, credibility 0.7 (`docs/06` §2)
3. Capital figure and drawdown confirmation (`docs/07` blanks)
4. Realistic weekly hours (under 5 → cut to index-only, `docs/10` §7)
5. Decision-capture mechanism confirmation (`docs/11` §1.2)

## Open risks being watched

- Chain snapshotter not yet running: **every day of delay is permanently lost IV history**
- Historical option-chain data purchasable from a vendor? Would remove the 6-month proxy
