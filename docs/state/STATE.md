# STATE — where the project is right now

**Rewritten in place every session. Never appended to.** This file must stay under ~40
lines so it can be read at the start of every session for almost nothing. History belongs
in `DEVLOG.md`.

---

**Updated:** 2026-10-18
**Phase:** 1 — The data spine (next). **Phase 0 complete.**
**Active step:** 1.5a and 1.5b both complete. 1.5b now ingests SEBI's RSS (compliant,
verified 7/7 live); RBI rejected on its terms (ISS-015).
Next is 1.3b (websocket, needs a live session) or 1.7 (journal schema)
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

**Monday, in this order:**

```
uv run trading-desk survey      # ~30 minutes, 218 underlyings
make verify STEP=1_1b           # the live capture check
```

(`make login` only if the token has aged out — it was re-issued on the 18th.)

Then **step 1.3b — the Dhan websocket adapter**: subscribe the universe, tick→bar through
the builder that landed in 1.3a, reconnect with backoff writing a `data_gaps` row, a
freshness watchdog, and ticks onto Redis. The feed cap is not a constraint (5,000 per
connection, ISS-001). One thing to settle at the adapter edge: whether Dhan sends
cumulative day volume or per-trade quantity — `Tick.quantity` requires the latter.

**Detail on the survey:**

```
uv run trading-desk survey      # ~30 minutes, 218 underlyings
```

It refuses to run on a weekend or holiday (ISS-006: the feed serves the previous
session's volume without saying so), so Monday is the first opportunity. Then read the
diff it prints against the starter list — differences are expected and wanted. Nothing
downstream changes until that file exists; the snapshotter keeps using the starter list.

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
2c. **Licensed news API — still a purchase decision (ISS-010).** No commercial Indian
   financial publisher permits this use; 16 feeds surveyed, clauses quoted in
   `config/news_sources.yaml`. SEBI + NSE filings now cover primary regulatory
   disclosure for free. **Nothing is blocked today** — revisit only if the briefings
   visibly want market commentary
2e. **Optional, free, your call: write to RBI** asking permission to cache and deep-link
   their RSS feeds (ISS-015). Their terms prohibit both without written permission, so
   the feeds are excluded despite being live, fresh and useful. One email unblocks them
2d. **NSE poller User-Agent (ISS-013)** — their robots.txt permits the path but their bot
   protection only answers a browser string. Kept on the basis that it is mandated public
   disclosure; say if you would rather drop it
2b. **Liquidity thresholds** in `config/exchange_rules.yaml` — turnover ₹50L, OI 50,000,
   25% priced legs, 25 stocks. `verified: false`, and `verify 1_2` fails on that check by
   design. Set permissive on purpose; tighten once the first survey shows the real spread
3. Capital figure and drawdown confirmation (`docs/07` blanks)
4. Realistic weekly hours (under 5 → cut to index-only, `docs/10` §7)
5. Decision-capture mechanism confirmation (`docs/11` §1.2)

## Verification sweep — 2026-10-18 (Sunday)

| Step | Result | Note |
|---|---|---|
| 0.1 toolchain | PASS 6/6 | |
| 0.3 config & secrets | PASS 7/7 | |
| 0.4 value objects | PASS 10/10 | clock check is an AST walk |
| 0.5 infrastructure | PASS 8/8 | now compares against the head revision |
| 0.6 ports & fakes | PASS 6/6 | 10 ports, 10 fakes |
| 1.1a chain adapter | PASS 5/5 | live NIFTY chain, 235 strikes, greeks present |
| 1.1b archive & snapshotter | **7 of 8** | `live_capture` needs a trading day |
| 1.2 universe service | **6 of 7** | `thresholds_reviewed` needs your sign-off |
| 1.3a schema & bar builder | PASS 8/8 | |
| 1.4 historical backfill | PASS 8/8 | 940 daily bars/name; close agrees with the chain spot |
| 1.5a exchange filings | PASS 8/8 | 1,538 filings stored live from NSE, free |
| 1.5b news articles | PASS 7/7 | **25 SEBI articles live**; 5 guards induced. RBI excluded (ISS-015) |
| 1.6 calendars | PASS 6/6 | expiry list audits the holiday file; found Guru Nanak missing |

No defects outstanding: one closed market, one decision.

**Note on reading this table:** the runner used to print `FAIL (1 of 7)` meaning *one
failed*. Both result lines now count passes (ISS-017).

**Token:** re-issued 2026-10-18 16:36 IST, valid 24.0h (expires ~16:36 on the 19th).
**Delete `DHAN_ACCESS_TOKEN` from `.env`** — nothing reads it any more, and a stale copy
there is what made the harness authenticate differently from the app.

## Open risks being watched

- Chain snapshotter not yet running: **every day of delay is permanently lost IV history**
- Historical option-chain data purchasable from a vendor? Would remove the 6-month proxy
