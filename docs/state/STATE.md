# STATE — where the project is right now

**Rewritten in place every session. Never appended to.** History belongs in `DEVLOG.md`.

---

**Updated:** 2026-10-19
**Phase:** 0, 1 and 2 complete. **Next is Phase 3 — the agents, starting at 3.1.**
**Package name:** `trading_desk` (decided 2026-10-15)

**Three decisions are waiting on you before anything else closes: ISS-021 (delta
bands), ISS-022 (capital figure) and one real contract note.** They are the only
reason three verifications fail; no defect is outstanding.

## Done

- **Planning** — `docs/00` … `docs/15`, `CLAUDE.md`, `.claude/skills/` (5 skills)
- **Phase 0 (0.1–0.6)** — toolchain, 4 import-linter contracts, typed settings with
  secret redaction, value objects in integer paise, Docker Postgres
  (TimescaleDB + pgvector) with alembic, 15 ports each with a fake, network blocked
  in the suite. Every guard induced, not assumed.
- **Phase 1 (1.1–1.9)** — the data spine. Live Dhan chain adapter and Parquet archive,
  the scheduled snapshotter, universe service, bar builder, 940-bar historical
  backfill, NSE filings (1,538 stored, free), SEBI news via RSS, calendars, the
  append-only journal (10 database triggers, including the TRUNCATE path), IV rank
  with a realised-vol proxy, and backup with an automated restore drill.
- **Phase 2 (2.1–2.8)** — the decision core, all pure `domain/`. Research and proposal
  models that cannot express a quantity, indicators and signals, Setup A (9 gates) and
  Setup B (5 gates), the vertical-spread builder that raises rather than emit a naked
  short, Black-76 greeks, the 13-check risk gate, the cost model and the first dry run.

## Next action

**Phase 3, step 3.1 — the LLM adapter and cost governor.** It needs one number from
you first: **a monthly LLM spend cap in rupees.** The governor refuses a call that
would cross it, so the cap is the feature, not a setting added later.

Unrelated and still outstanding from Phase 1, whenever a trading day allows:

```
uv run trading-desk survey      # ~30 minutes, 218 underlyings
make verify STEP=1_1b           # the live capture check
```

The survey refuses to run on a weekend or holiday (ISS-006: the feed serves the
previous session's volume without saying so). `make login` only if the token has aged
out.

## Blocked on you

1. **ISS-022 — what is your capital figure?** (`docs/07` is blank.) Flat 0.25% risk
   means a NIFTY spread risking ₹10,500 per lot needs **₹42 lakh** equity to take one
   lot; at ₹10 lakh the gate correctly returns zero and the briefing never produces a
   tradable size. The most consequential open number in the project.
2. **ISS-021 — Setup A's delta bands do not fit real chains.** Only 10 of 25 universe
   names have a strike in both the 0.50–0.55 and 0.25–0.30 bands. My recommendation:
   select the *nearest strike to a delta target* with a documented maximum deviation.
   Four options written up in `ISSUES.md`. `verify 2_5` fails until this is decided.
3. **One real Dhan contract note** from any options trade you have already done, so the
   cost model reconciles to the rupee (FR-10.3). `verify 2_8` fails until then.
   Costs currently total ₹272.30 round trip — **13% of a real spread's best case** —
   and the largest component, ₹150 of slippage, is an assumption (ISS-023).
4. **A monthly LLM cost cap**, for step 3.1.
5. **Setup thresholds sign-off** (`config/setups.yaml`, `verified: false`) — `verify
   2_3` fails on it by design.
6. **IV-rank threshold definition.** `docs/06` says 60 / 70 / 35 but not whether those
   are IV *rank* or IV *percentile*. Different numbers. This implements percentile.
7. **Liquidity thresholds** (`config/exchange_rules.yaml`, `verified: false`) —
   turnover ₹50L, OI 50,000, 25% priced legs, 25 stocks. Set permissive on purpose;
   tighten once the first survey shows the real spread. `verify 1_2` fails on it.
8. **Universe backfill go-ahead.** Only NIFTY and RELIANCE have daily bars, so the
   other 24 names produce no IV rank at all (correctly — they refuse rather than
   guess). Needs a `backfill` command and ~24 × 940 bars of live Dhan history.
9. **Offsite backup provider** — backups are local only, on the same disk as the
   database. That survives a dropped table, not a lost laptop, and the option-chain
   archive cannot be re-fetched at any price. A versioned bucket (S3, B2, R2) plus
   credentials. `verify 1_9` fails on `copied_offsite` by design.
10. **Perform one restore by hand** (`docs/15` §4, ~15 minutes, scratch DB, no risk).
    `docs/10` says 1.9 is done when *you* have restored once.
11. **Dhan Data API subscription (₹499 + GST/month)** — required for live feed, option
    chain and historical bars beyond what is already captured.
12. **Licensed news API — a purchase decision (ISS-010).** No commercial Indian
    financial publisher permits this use; 16 feeds surveyed, clauses quoted in
    `config/news_sources.yaml`. SEBI + NSE filings cover regulatory disclosure for
    free, so **nothing is blocked today**.
13. **Optional, free: write to RBI** asking permission to cache and deep-link their
    feeds (ISS-015). Their terms prohibit both without written permission. One email.
14. **NSE poller User-Agent (ISS-013)** — robots.txt permits the path but their bot
    protection only answers a browser string. Kept on the basis that it is mandated
    public disclosure; say if you would rather drop it.
15. Drawdown confirmation (`docs/07`), realistic weekly hours (under 5 → cut to
    index-only, `docs/10` §7), decision-capture mechanism (`docs/11` §1.2).

## Verification sweep — 2026-10-19

| Step | Result | Note |
|---|---|---|
| 0.1 toolchain | PASS 6/6 | |
| 0.3 config & secrets | PASS 7/7 | |
| 0.4 value objects | PASS 10/10 | clock check is an AST walk |
| 0.5 infrastructure | PASS 8/8 | compares against the head revision |
| 0.6 ports & fakes | PASS 6/6 | 15 ports, 15 fakes |
| 1.1a chain adapter | PASS 5/5 | live NIFTY chain, 235 strikes, greeks present |
| 1.1b archive & snapshotter | **7 of 8** | `live_capture` needs a trading day |
| 1.2 universe service | **6 of 7** | `thresholds_reviewed` needs your sign-off |
| 1.3a schema & bar builder | PASS 8/8 | |
| 1.4 historical backfill | PASS 8/8 | 940 daily bars/name; close agrees with chain spot |
| 1.5a exchange filings | PASS 8/8 | 1,538 filings stored live from NSE, free |
| 1.5b news articles | PASS 7/7 | 25 SEBI articles live; 5 guards induced. RBI excluded |
| 1.6 calendars | PASS 6/6 | expiry list audits the holiday file; found Guru Nanak missing |
| 1.7 journal schema | PASS 8/8 | 5 tables, 10 triggers; UPDATE/DELETE/TRUNCATE all refused |
| 1.8 IV rank | **7 of 8** | proxy path live; `universe_has_bar_history` — 2 of 26 names |
| 1.9 backup & restore | **6 of 7** | real restore matched 3,448 rows; `copied_offsite` by design |
| 2.1 research models | PASS 8/8 | no-size introspection induced at top level and nested |
| 2.2 indicators & signals | PASS 8/8 | 6 signals registered, **0 backtested** |
| 2.3 Setup A gates | **7 of 8** | 9 gates, boundaries both sides; `thresholds_signed_off` |
| 2.4 Setup B gates | PASS 8/8 | middle band tested hardest; naked short unreachable |
| 2.5 structure builder | **6 of 7** | real AXISBANK spread built; **ISS-021** |
| 2.6 greeks | PASS 7/7 | matches vollib to 1.8e-15; Dhan delta gap 0.015 mean, real data |
| 2.7 risk gate | PASS 9/9 | 13 checks, property-tested; no confidence input by shape |
| 2.8 costs & dry run | **8 of 9** | lookahead induced; `contract_note` pending (**ISS-023**) |

`make check`: **673 passed, 4 contracts kept, mypy --strict clean.**

**Note on reading this table:** the runner used to print `FAIL (1 of 7)` meaning *one
failed*. Both result lines now count passes (ISS-017).

**Token:** re-issued 2026-10-18 16:36 IST, valid 24.0h — **expired ~16:36 today**, so
`make login` before any live call. **Delete `DHAN_ACCESS_TOKEN` from `.env`** — nothing
reads it, and a stale copy there is what made the harness authenticate differently from
the app.

## Open risks being watched

- Chain snapshotter not yet running: **every day of delay is permanently lost IV history**
- Historical option-chain data purchasable from a vendor? Would remove the 6-month proxy
- **ISS-020 open with an unknown mechanism** — one TimescaleDB restore failed and has
  never reproduced. The pre/post_restore calls stay because they are cheap and
  documented, not because they are proven necessary.
