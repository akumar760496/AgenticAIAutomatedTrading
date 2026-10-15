# STATE — where the project is right now

**Rewritten in place every session. Never appended to.** This file must stay under ~40
lines so it can be read at the start of every session for almost nothing. History belongs
in `DEVLOG.md`.

---

**Updated:** 2026-10-15
**Phase:** 0 — Foundation (in progress)
**Active step:** 0.5 — local infrastructure, Docker Postgres + Redis (next)
**Package name:** `trading_desk` (decided 2026-10-15)

## Done

- All planning docs: `docs/00` … `docs/14`
- Agent operating rules: `CLAUDE.md`, `.claude/skills/` (5 skills)
- **Step 0.1** — repo, Python 3.12, ruff/mypy/pytest/hypothesis/pre-commit, 7 layer
  packages, `make check`, verify harness. Verified, both guards induced.
- **Step 0.2** — `.importlinter`, 4 contracts. Verified, violation induced.
- **Step 0.3** — typed settings per concern, fail-fast naming missing keys, secret
  redaction filter, `tests/conftest.py` credential isolation. Verified 7/7, failure
  induced. **Dhan credentials are live in `.env` and load correctly.**
- **Step 0.4** — `Money`/`Price`/`Quantity`/`Percent` (integer paise, floats rejected),
  `InstrumentRef`, 8 enums, clock port + adapter + `FrozenClock` fake, domain purity test.
  Verified 10/10, purity violation induced.

## Next action

Step 0.5 — `docker-compose.yml` with Postgres (TimescaleDB + pgvector) and Redis,
alembic baseline, `make db-up` / `db-migrate` / `db-reset`. Then 0.6 (ports and fakes),
which closes Phase 0 and opens Step 1.1, the chain snapshotter.

## Blocked on you

1. **Dhan Data API subscription (₹499 + GST/month)** — credentials are in `.env`, but the
   paid plan is still required for live feed, option chain and historical bars (Step 1.1)
2. Threshold sign-off: 1.5× ATR, IV rank 60/70/35, credibility 0.7 (`docs/06` §2)
3. Capital figure and drawdown confirmation (`docs/07` blanks)
4. Realistic weekly hours (under 5 → cut to index-only, `docs/10` §7)
5. Decision-capture mechanism confirmation (`docs/11` §1.2)

## Open risks being watched

- Chain snapshotter not yet running: **every day of delay is permanently lost IV history**
- Historical option-chain data purchasable from a vendor? Would remove the 6-month proxy
