# STATE — where the project is right now

**Rewritten in place every session. Never appended to.** This file must stay under ~40
lines so it can be read at the start of every session for almost nothing. History belongs
in `DEVLOG.md`.

---

**Updated:** 2026-10-16
**Phase:** 1 — The data spine (next). **Phase 0 complete.**
**Active step:** 1.1c done — next is 1.2 (universe service)
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
- **Step 0.5** — Docker Postgres (TimescaleDB 2.30.2 + pgvector 0.8.7) and Redis,
  alembic baseline, `make db-up/db-migrate/db-reset/db-shell`. Verified 8/8, database-down
  induced, clean rebuild from migrations proven.
- **Step 0.6** — 9 ports, 9 fakes, network blocked in the test suite. Verified 6/6,
  network access and an order method both induced. **Phase 0 is complete.**
- **Step 1.1a** — Dhan client with the 3s rate limiter, token provider, chain adapter,
  starter universe. Verified 5/5 **against live Dhan data**.
- **Step 1.1b** — Parquet chain archive and the snapshotter with capture-health reporting.
  Verified 7/7 live. **The archive is accumulating: 2 indices captured today.**

## Next action

**Step 1.2 — the universe service.** Rank by option turnover and open interest instead
of the hand-written starter list, so the 25 names earn their place in the universe.

**Done in 1.1c, verified live 2026-10-17:** `trading-desk run` authenticates on startup
and captures at 09:30, 12:30 and 15:15 IST on trading days, skipping weekends and
holidays with a stated reason. Entry points: `make run`, `make snapshot`, `make login`.

**The token question is settled enough to proceed.** A consent-flow token lasts exactly
24 hours (measured, not documented), so one login covers a full session's three captures.
`ensure_valid_token` refreshes when under an hour remains. Running unattended across
several days would still need PIN+TOTP — a security decision left open, not a blocker for
a schedule that is started once a day. See ISS-005.

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
