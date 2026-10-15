# 10 — Detailed Build Plan, Step by Step

The execution plan at task level. [03-roadmap.md](03-roadmap.md) says *what the phases
are*; this says *what you do on which day, and how you know it worked*.

**How a step is written.**

- **Goal** — one sentence. If the step doesn't reduce to one sentence, it's two steps.
- **Depends on** — steps that must be finished first. Nothing else blocks it.
- **Tasks** — the actual work, in order.
- **Deliverable** — the files or artifacts that exist afterwards.
- **Done when** — the check that proves it. A step with a vague check isn't planned yet.
- **Covers** — requirement IDs from [08-requirements.md](08-requirements.md).
- **Estimate** — focused hours, not elapsed days.

**Calendar assumption.** ~8 hours a week. Start 2026-10-15. That puts Phase 0+1 complete
near 2026-12-15 and the first briefing before 2027-01-15, which are Gate 1's dates in
[07-stop-criteria.md](07-stop-criteria.md). If your real number is 4 hours a week, halve
the scope now — see §7 — rather than discovering it in February.

**Order is not negotiable in two places.** Step 1.1 (the chain snapshotter) comes before
all strategy work because that data can't be backfilled. Phase 2 (deterministic gates)
comes before Phase 3 (agents) because an agent built against an unfinished rulebook will
be rewritten.

---

## Phase 0 — Foundation (Week 1, ~8 hrs)

Nothing here touches a market. The point is that every later step lands on rails.

### Step 0.1 — Repository and toolchain

- **Goal.** A Python project that lints, type-checks and tests on every commit.
- **Depends on.** Nothing.
- **Tasks.**
  1. `git init`. First commit is the existing `docs/` tree, so the plan is in history
     before the code is.
  2. Install Python 3.12 via `uv` — **not** the 3.14 on your machine; several quant
     libraries have no 3.14 wheels yet. `uv python install 3.12`.
  3. `uv init`, create the package directory, pin dependencies in `pyproject.toml`.
  4. Add `ruff`, `mypy` (strict), `pytest`, `pytest-cov`, `hypothesis`, `import-linter`,
     `pre-commit`, `detect-secrets`.
  5. Write `.pre-commit-config.yaml` wiring all of the above plus secret scanning.
  6. Write `.gitignore` — `.env`, `*.parquet`, `data/`, `.venv`, notebook checkpoints.
  7. Create the empty layer packages with `__init__.py`: `domain/`, `ports/`,
     `application/`, `agents/`, `adapters/`, `infra/`, `interfaces/`.
  8. Add a GitHub Actions (or local `make check`) target running lint, types, tests.
- **Deliverable.** `pyproject.toml`, `.pre-commit-config.yaml`, `.gitignore`, `Makefile`,
  empty package tree, CI config.
- **Done when.** `make check` passes on a clean clone, and a deliberate `x: int = "s"`
  makes it fail.
- **Covers.** FR-12.2.
- **Estimate.** 2 hrs.

### Step 0.2 — The dependency rule, enforced

- **Goal.** Architecture violations fail the build rather than being noticed in review.
- **Depends on.** 0.1.
- **Tasks.**
  1. Write `.importlinter` with layered contracts: `domain` imports nothing internal;
     `ports` may import `domain`; `application` may import `domain`+`ports`; `adapters`
     may import `ports`+`domain`; `interfaces` sits outermost. `agents` may import
     `domain`+`ports` only.
  2. Add a forbidden contract: `domain` may not import `requests`, `httpx`, `anthropic`,
     `redis`, `sqlalchemy`, `pandas` — anything with I/O or a clock.
  3. Prove it: temporarily add `import httpx` to a domain module, watch CI fail, revert.
- **Deliverable.** `.importlinter`, wired into `make check`.
- **Done when.** The deliberate violation fails, and the commit removing it passes.
- **Covers.** FR-12.1, FR-5.3.
- **Estimate.** 1.5 hrs.

### Step 0.3 — Configuration and secrets

- **Goal.** One typed settings object; no secret ever in code or git.
- **Depends on.** 0.1.
- **Tasks.**
  1. `pydantic-settings` model: Dhan credentials, Anthropic key, Telegram token/chat ID,
     DB URL, Redis URL, spend cap, run mode.
  2. `.env.example` with every key present and every value blank. This is the file you
     fill in — **never paste credentials into a chat window.**
  3. Fail fast: missing required settings raise at startup with the key name, never a
     `None` that surfaces three layers later.
  4. Log redaction filter: any value matching a configured secret is replaced with
     `***` before a log line is written. Test it.
- **Deliverable.** `infra/config.py`, `.env.example`, redaction filter + test.
- **Done when.** Starting with an empty `.env` prints exactly which keys are missing; a
  test asserting a secret cannot appear in log output passes.
- **Covers.** FR-11.5.
- **Estimate.** 1.5 hrs.

### Step 0.4 — Value objects and the money rule

- **Goal.** It becomes impossible to do arithmetic on money as a float.
- **Depends on.** 0.1.
- **Tasks.**
  1. `Money` (integer paise internally), `Price`, `Quantity`, `Percent` — frozen,
     validated, with explicit arithmetic.
  2. `InstrumentRef` — symbol, exchange, segment, token, and for options expiry/strike/
     right.
  3. Enums: `Direction`, `Horizon`, `SourceTier`, `OptionRight`, `SetupId`, `GateResult`.
  4. A tz-aware UTC clock port and a `utc_now()` helper; nothing calls `datetime.now()`.
  5. Tests: constructing `Money` from a float raises; equality and rounding behave;
     every domain object rejects mutation.
- **Deliverable.** `domain/values.py`, `domain/enums.py`, `ports/clock.py`, tests.
- **Done when.** `Money(10.5)` raises, `Money.from_rupees(Decimal("10.50"))` works, and a
  grep finds no `datetime.now()` outside the clock adapter.
- **Covers.** FR-6.4, FR-12.3, FR-12.4.
- **Estimate.** 2 hrs.

### Step 0.5 — Local infrastructure

- **Goal.** `docker compose up` gives you Postgres (Timescale + pgvector) and Redis.
- **Depends on.** 0.1.
- **Tasks.**
  1. `docker-compose.yml`: one Postgres with both extensions, one Redis, named volumes.
  2. Migration tool (`alembic`), empty baseline migration.
  3. `make db-up`, `make db-migrate`, `make db-reset`.
  4. Connection check script that reports extension versions.
- **Deliverable.** `docker-compose.yml`, `alembic/`, make targets.
- **Done when.** Fresh machine → `make db-up && make db-migrate` → the check script prints
  both extension versions.
- **Covers.** FR-11.6.
- **Estimate.** 1 hr.

### Step 0.6 — Ports and fakes

- **Goal.** Every external dependency has an interface and an in-memory fake, so the test
  suite never touches the network.
- **Depends on.** 0.4.
- **Tasks.**
  1. Define Protocols: `MarketDataPort`, `OptionChainPort`, `NewsPort`, `FilingsPort`,
     `LlmPort`, `NotifierPort`, `JournalPort`, `BrokerPort`, `ClockPort`.
  2. Write a fake for each, driven by fixture files.
  3. `conftest.py` wiring fakes by default, so a test that reaches the network has to
     work at it.
- **Deliverable.** `ports/*.py`, `tests/fakes/*.py`, `conftest.py`.
- **Done when.** The suite runs green with networking disabled.
- **Covers.** FR-12.7, FR-10.1.
- **Estimate.** 2 hrs.

**Phase 0 exit.** `make check` green; CI enforcing layers; `.env` populated locally;
Docker stack up; fakes in place. **No market data yet, and that's correct.**

---

## Phase 1 — The data spine (Weeks 2–5, ~32 hrs)

The irreplaceable phase. Everything here is about capture, and capture is the only thing
in this project that gets worse if you delay it.

### Step 1.1 — The option chain snapshotter (do this first)

- **Goal.** From today, a permanent daily record of the options market for your universe.
- **Depends on.** 0.3, 0.5. **Not** on the rest of Phase 1.
- **Tasks.**
  1. Authenticate against DhanHQ v2; confirm the option chain endpoint returns greeks.
  2. Hardcode a starter list (NIFTY, BANKNIFTY + the 25 placeholder stocks from
     [06-v1-scope.md](06-v1-scope.md) §1) — a provisional list capturing data beats a
     perfect list capturing nothing.
  3. Fetch the full chain per instrument; store strike, right, expiry, bid, ask, last,
     volume, OI, IV, delta, gamma, theta, vega, plus exchange and receipt timestamps.
  4. Write Parquet partitioned `date=YYYY-MM-DD/underlying=SYM/`.
  5. Schedule 09:30, 12:30, 15:15 IST on trading days only (needs a holiday calendar —
     fetch the NSE list, store it as config).
  6. Capture-health log per run: instruments attempted, succeeded, rows written. A
     silent failure here is the one failure that costs you something permanent.
  7. Telegram alert on any missed snapshot. This runs unattended for months.
- **Deliverable.** `adapters/dhan/chain.py`, `application/snapshotter.py`,
  `config/holidays.yaml`, cron/systemd unit, health log.
- **Done when.** 10 consecutive trading days captured with zero gaps and an alert proven
  to fire when you deliberately break it.
- **Covers.** FR-2.4, FR-9.6.
- **Estimate.** 6 hrs.
- **Note.** If Phase 1 stalls for any reason, *this step keeps running*. Even as a crude
  cron script, it is the single most valuable thing in the repo.

### Step 1.2 — Universe service

- **Goal.** The instrument list is derived from liquidity data, not from my memory.
- **Depends on.** 1.1 (needs some chain history), 0.5.
- **Tasks.**
  1. Fetch the Dhan instrument master; store with tokens, lot sizes, expiries.
  2. Ingest the NSE F&O eligibility list and the daily ban list.
  3. Rank F&O stocks by 20-day average **option** turnover and OI — not equity turnover;
     a liquid share with an illiquid option chain is a trap.
  4. Apply exclusions: ban list, F&O entrants under 6 months, under 6 months of our own
     chain history. Log a reason per exclusion.
  5. Emit `config/universe.yaml` with an effective-from date and a diff against the
     previous version.
  6. Point the snapshotter at this file instead of its hardcoded list.
- **Deliverable.** `application/universe_service.py`, `config/universe.yaml`,
  `config/exchange_rules.yaml`.
- **Done when.** The generated top-25 is produced with reasons, and you've reviewed the
  diff against my placeholder list. Expect differences — mine is a guess.
- **Covers.** FR-1.1 to FR-1.6.
- **Estimate.** 5 hrs.

### Step 1.3 — Market data service

- **Goal.** Live ticks into bars, durably, with honest gaps.
- **Depends on.** 1.2.
- **Tasks.**
  1. Timescale hypertables: `ticks`, `bars_1m`, `bars_5m`, `bars_1d`, plus `data_gaps`.
  2. Dhan websocket adapter: subscribe the universe within your plan's instrument cap
     (**confirm the cap first — it may force 1.2's list shorter**).
  3. Tick → bar aggregation with explicit session boundaries in IST.
  4. Reconnect with exponential backoff; on resume, write a `data_gaps` row. Never
     interpolate.
  5. Freshness watchdog publishing a `feed_healthy` flag.
  6. Publish ticks to Redis `md.ticks` for later consumers.
- **Deliverable.** `adapters/dhan/feed.py`, `application/bar_builder.py`, migrations.
- **Done when.** A full session runs unattended; bars reconcile against Dhan's historical
  API within tolerance; a forced disconnect produces a gap row and a clean resume.
- **Covers.** FR-2.1, FR-2.2, FR-2.3, FR-2.5, FR-2.6.
- **Estimate.** 7 hrs.

### Step 1.4 — Historical backfill

- **Goal.** Indicators work on day one instead of after a month of warm-up.
- **Depends on.** 1.3.
- **Tasks.**
  1. Pull daily bars (2+ years) and available intraday history for the universe.
  2. Idempotent loader — re-running must not duplicate rows.
  3. Validate against known corporate actions; flag unexplained jumps rather than
     silently adjusting.
- **Deliverable.** `application/backfill.py`, validation report.
- **Done when.** ATR(14) and the 20-day range are computable for every universe name, and
  the validation report is clean or explained.
- **Covers.** FR-2.7.
- **Estimate.** 3 hrs.

### Step 1.5 — News and filings ingestion

- **Goal.** A searchable archive of everything said about your universe, with sources.
- **Depends on.** 1.2.
- **Tasks.**
  1. Tables: `articles`, `filings`, `sources`, with full raw body retained.
  2. NSE/BSE announcement poller.
  3. News API adapter(s) plus tier-1 RSS. **Check the licence terms for each** — some
     prohibit automated storage.
  4. Content hash plus near-duplicate detection via pgvector embeddings; one event, many
     sources.
  5. Entity resolution to instruments using an alias table, not substring matching
     ("Tata Motors" vs "Tata Steel" vs "TATAMOTORS").
  6. Store published-at and ingested-at separately — the difference is how late you are.
- **Deliverable.** `adapters/news/*.py`, `application/news_ingest.py`, `config/aliases.yaml`.
- **Done when.** A known filing appears within the poll interval; a story carried by 5
  outlets stores as one event with 5 sources; the alias fixture set resolves 100%.
- **Covers.** FR-3.1 to FR-3.4.
- **Estimate.** 7 hrs.

### Step 1.6 — Calendars

- **Goal.** The system knows what's coming, because dates kill option trades.
- **Depends on.** 1.2.
- **Tasks.**
  1. Trading holidays (from 1.1), expiry calendar per instrument — **Nifty weekly on
     Tuesday, Bank Nifty monthly only, stock options monthly only.**
  2. Results calendar per universe name.
  3. Macro events: RBI policy, CPI, IIP, FOMC, Budget.
  4. Query API: "what events fall between these two dates for this instrument".
- **Deliverable.** `application/calendars.py`, `config/events.yaml`, migrations.
- **Done when.** The query API answers correctly for a hand-checked month.
- **Covers.** FR-3.7, FR-3.8, FR-1.2.
- **Estimate.** 3 hrs.

### Step 1.7 — Journal schema

- **Goal.** The evidence base exists before there's anything to record. Not deferred.
- **Depends on.** 0.5.
- **Tasks.**
  1. Migrations for `briefing_ideas`, `user_decisions`, `outcomes`, `override_log`,
     `calibration_snapshots` per [01-architecture.md](01-architecture.md) §8.
  2. `JournalPort` plus a Postgres adapter.
  3. Append-only discipline: corrections are new rows, never updates.
  4. An export-to-Parquet job so analysis never runs against the live DB.
- **Deliverable.** migrations, `adapters/store/journal.py`, export job.
- **Done when.** A fake briefing writes a full round-trip: idea → decision → outcome →
  override reason, and the export reads back identically.
- **Covers.** FR-7.5.
- **Estimate.** 3 hrs.

### Step 1.8 — IV rank computation

- **Goal.** A number you can gate on today, honestly labelled.
- **Depends on.** 1.1, 1.4.
- **Tasks.**
  1. From chain snapshots, compute ATM IV per instrument per day; store the series.
  2. `iv_rank` = percentile of today's ATM IV within the trailing window, with the
     window length and sample count returned alongside.
  3. The proxy: realised-vol percentile from 1.4's history, for use until 6 months of
     real IV accrues. **Every value carries `source: actual | proxy` and sample count.**
  4. Refuse to return a rank at all below a minimum sample count — a rank from 11 days of
     data is a lie with a decimal point.
- **Deliverable.** `domain/vol.py` (pure maths), `application/iv_rank.py`, tests.
- **Done when.** Ranks computed for the full universe, proxy-tagged, with low-sample cases
  returning "insufficient history" rather than a number.
- **Covers.** FR-5.6.
- **Estimate.** 4 hrs.

### Step 1.9 — Backup and restore drill

- **Goal.** The irreplaceable data survives your laptop.
- **Depends on.** 1.1, 1.7.
- **Tasks.**
  1. Nightly `pg_dump` plus Parquet sync to object storage (versioned bucket).
  2. Restore into a scratch database and row-count-compare.
  3. Alert on a missed backup.
- **Deliverable.** backup script, documented restore procedure, drill result in the repo.
- **Done when.** You have personally restored from backup once and it matched.
- **Covers.** FR-11.7.
- **Estimate.** 2 hrs.

**Phase 1 exit (Gate 1, target 2026-12-15).** Snapshotter running 20+ days unattended;
universe generated from data; bars reconciling; news archive filling; journal tables live;
IV rank returning honest values; one successful restore drill.

---

## Phase 2 — The rulebooks as code (Weeks 6–7, ~16 hrs)

Still no AI. The rulebook is written and tested as plain functions first, because an agent
built against an unfinished rulebook gets thrown away.

### Step 2.1 — Domain models for research

- **Goal.** The types that make bad proposals impossible to express.
- **Depends on.** 0.4.
- **Tasks.**
  1. `Evidence`, `Thesis` (min 2 evidence items, enforced by the validator), `ProposedLeg`,
     `EntryCondition`, `StopBasis`, `TargetBasis`, `TradeProposal` (`rank: Literal[1,2]`),
     `ResearchCycleResult`.
  2. **Assert the absence of a size field with a test that introspects the model** — so a
     future "helpful" addition fails CI.
  3. All frozen; all timestamps tz-aware.
- **Deliverable.** `domain/models/*.py`, tests.
- **Done when.** A single-source thesis raises; adding a `quantity` field to
  `TradeProposal` fails the introspection test.
- **Covers.** FR-4.5 to FR-4.8.
- **Estimate.** 3 hrs.

### Step 2.2 — Indicator and signal library

- **Goal.** Pure, tested market maths.
- **Depends on.** 1.4.
- **Tasks.** ATR(14), 20-day range, volume ratio vs 20-day average, realised vol, trend
  regime classifier. Pure functions over arrays; no DB access. Golden-file tests against
  hand-computed values. A signal registry so only pre-backtested signals can be named.
- **Deliverable.** `domain/indicators.py`, `domain/signals/registry.py`, tests.
- **Done when.** Each indicator matches a hand-computed fixture to the expected precision.
- **Covers.** FR-5.10.
- **Estimate.** 3 hrs.

### Step 2.3 — Setup A gates

- **Goal.** Nine independently testable functions implementing the shares rulebook.
- **Depends on.** 2.1, 2.2, 1.5, 1.6, 1.8.
- **Tasks.**
  1. One function per gate: catalyst recency, catalyst-type whitelist, credibility ≥0.7,
     **not-already-priced (move since catalyst < 1.5×ATR)**, technical confirm, IV rank
     <60, liquidity (OI ≥500 lots, spread ≤3% of mid), expiry window 10–25 DTE with no
     results inside it unless results *are* the catalyst, governance veto.
  2. Each returns a `GateResult` carrying the computed value, the threshold and the
     verdict — never a bare boolean.
  3. A combinator returning every gate's result, not short-circuiting, so the briefing can
     show all of them.
  4. Boundary tests at exactly the threshold on each side.
  5. Thresholds read from `config/setups.yaml`.
- **Deliverable.** `domain/setups/setup_a.py`, `config/setups.yaml`, tests.
- **Done when.** Every gate has pass/fail/boundary tests; a hand-built historical example
  you agree with produces the verdict you expect.
- **Covers.** FR-5.1, FR-5.4, FR-5.5.
- **Estimate.** 5 hrs.

### Step 2.4 — Setup B gates

- **Goal.** The index volatility-regime rulebook, including its large no-trade band.
- **Depends on.** 2.1, 2.2, 1.8, 1.6.
- **Tasks.**
  1. Regime classifier: IV rank ≥70 → credit (B1); 35–70 → **no trade**; ≤35 + trending →
     debit (B2).
  2. Hard gates: flat by the session before expiry; no entry if a macro event falls in the
     window; max one index position; **naked shorts unreachable in code, not merely off**.
  3. Nifty weekly (Tuesday) vs Bank Nifty monthly handled from `exchange_rules.yaml`, not
     hardcoded.
  4. Test the middle band explicitly — it's the most common outcome and the easiest to
     accidentally break.
- **Deliverable.** `domain/setups/setup_b.py`, tests.
- **Done when.** IV rank 50 returns no-trade with a reason; no code path can construct an
  unhedged short, asserted by test.
- **Covers.** FR-5.2, FR-5.9.
- **Estimate.** 4 hrs.

### Step 2.5 — Options structure builder

- **Goal.** Turn "bullish on X, 15 DTE" into specific strikes.
- **Depends on.** 2.1, 1.1.
- **Tasks.** Select expiry from DTE window; pick long leg by delta band (0.50–0.55) and
  short leg ~1 ATR away (0.25–0.30); compute net debit/credit, max loss, max gain,
  breakeven; validate against lot size and freeze limits; reject if the chain is too thin
  to fill.
- **Deliverable.** `domain/options/structure.py`, tests over fixture chains.
- **Done when.** Fixture chains produce the leg plans the spec describes, and a deliberately
  thin chain is rejected.
- **Covers.** FR-5.7.
- **Estimate.** 4 hrs.

### Step 2.6 — Greeks and cross-check

- **Goal.** Independently computed greeks, with divergence surfaced.
- **Depends on.** 2.5.
- **Tasks.** Black-76 / Black-Scholes via `py_vollib`; implied vol solver; compare against
  Dhan's published greeks and flag divergence beyond tolerance.
- **Deliverable.** `domain/options/greeks.py`, divergence report.
- **Done when.** Our greeks agree with Dhan's inside tolerance across a live chain, and
  disagreements are investigated rather than averaged.
- **Covers.** FR-5.8.
- **Estimate.** 3 hrs.

### Step 2.7 — The risk gate

- **Goal.** All 13 checks, property-tested, running advisory in v1.
- **Depends on.** 2.5, 0.4.
- **Tasks.**
  1. Implement each check per [01-architecture.md](01-architecture.md) §4, returning a
     structured result.
  2. Sizing: flat 0.25% of equity, max loss capped by the structure's own max loss. **No
     confidence input exists in the signature** — that's FR-7.8 enforced by shape.
  3. `hypothesis` property tests: size is never negative; never exceeds the per-trade cap
     for any input; never rounds up through a lot boundary; never approves with stale data.
  4. Four fail-closed tests: stale data, feed down, exception, broker error.
- **Deliverable.** `domain/risk/gate.py`, `domain/risk/sizing.py`, property tests.
- **Done when.** The property suite is green and the fail-closed tests pass. `make check`
  gates the build on it.
- **Covers.** FR-6.1 to FR-6.5, FR-6.7, FR-9.4.
- **Estimate.** 6 hrs.

### Step 2.8 — Cost model and historical dry run

- **Goal.** Know what the rulebooks would have said, with real costs.
- **Depends on.** 2.3, 2.4, 1.4.
- **Tasks.**
  1. Cost model: brokerage, STT, stamp duty, exchange fees, SEBI fees, GST, plus a
     slippage assumption. **Reconcile against one of your real contract notes.**
  2. Run the gates over available history (shallow at first — that's expected) and produce
     the list of what would have triggered.
  3. Point-in-time guard: a test proving the runner cannot see data timestamped after the
     decision moment.
- **Deliverable.** `domain/costs.py`, `application/dry_run.py`, first findings notebook.
- **Done when.** Costs match a real contract note to the rupee, the lookahead test fails on
  injected future data, and you've read the trigger list.
- **Covers.** FR-10.2, FR-10.3, FR-10.4.
- **Estimate.** 5 hrs.

**Phase 2 exit.** Both rulebooks are code with tests. You can answer "would this have
fired last Tuesday?" without an LLM in the loop. **If the dry run shows the setups almost
never fire, or fire constantly, fix that now** — it's a threshold problem, and discovering
it after the agents are built means rebuilding them.

---

## Phase 3 — The agents and the briefing (Weeks 8–11, ~32 hrs)

Now the AI, fitted into a system that already works without it.

### Step 3.1 — LLM adapter and cost governor

- **Goal.** Every model call typed, traced, capped and cheap where it can be.
- **Depends on.** 0.3, 0.6.
- **Tasks.** `LlmPort` implementation with structured output and retries; model tiering
  (`claude-haiku-5-5` for triage/dedup/classification, `claude-opus-5` for synthesis);
  token/cost accounting per call and per cycle; **a hard monthly cap that refuses calls
  rather than alerting afterwards**; Langfuse tracing; a recorded-response fake so tests
  cost nothing.
- **Deliverable.** `adapters/llm/*.py`, cost ledger, traces.
- **Done when.** A test run reports cost per node; the cap, set to ₹1, blocks the next call.
- **Covers.** FR-4.11, FR-4.13, FR-11.2, FR-11.4.
- **Estimate.** 5 hrs.

### Step 3.2 — News & Sentiment agent

- **Goal.** Turn the raw archive into scored, deduped, entity-linked catalysts.
- **Depends on.** 1.5, 3.1.
- **Tasks.** Six sub-agents — ingestor, deduper, entity linker, catalyst classifier,
  credibility scorer, impact analyst. Hand-label 200 news items as the test set
  (**your work, and the step most likely to slip**). Classifier must hit ≥90% on it.
  Credibility scores from documented rules, each score carrying its rule. Adversarial
  fixtures including a planted "ignore your instructions" string.
- **Deliverable.** `agents/news/*.py`, prompts, labelled fixture set, scores.
- **Done when.** ≥90% classification on the held-out set; the injection fixture changes no
  behaviour; dedup verified on a real multi-outlet story.
- **Covers.** FR-3.5, FR-3.6, FR-3.9, FR-4.2, FR-12.6.
- **Estimate.** 8 hrs.

### Step 3.3 — The other four specialists

- **Goal.** Fundamental, Technical/Quant, Options Strategist, Portfolio Context.
- **Depends on.** 2.2, 2.3, 2.4, 2.5, 3.1.
- **Tasks.** Each agent reads only from its allowed ports, returns a typed result, and
  **calls the deterministic gate functions rather than reasoning about thresholds** — the
  agent gathers inputs, the gate decides. Schema validation failure fails the cycle.
- **Deliverable.** `agents/fundamental/`, `agents/technical/`, `agents/options/`,
  `agents/portfolio/`.
- **Done when.** Each runs standalone against fakes and returns valid typed output; a
  malformed-output fixture fails the cycle rather than degrading it.
- **Covers.** FR-4.2, FR-4.4.
- **Estimate.** 8 hrs.

### Step 3.4 — Chief Strategist graph

- **Goal.** One orchestrated, resumable, fully traced research cycle.
- **Depends on.** 3.2, 3.3.
- **Tasks.** LangGraph graph with typed state and one `cycle_id`; parallel fan-out to the
  five specialists; synthesis and ranking producing **at most two** proposals; "no ideas"
  as a first-class result with a reason; every rejected candidate returned with its
  killing gate; per-node checkpointing so a crash resumes; delegation tree recorded.
- **Deliverable.** `agents/strategist/graph.py`, `application/research_cycle.py`.
- **Done when.** A full cycle runs against fakes end to end; a killed process resumes
  without repeating finished nodes; every artifact joins on `cycle_id`.
- **Covers.** FR-4.1, FR-4.3, FR-4.9, FR-4.10, FR-4.12.
- **Estimate.** 7 hrs.

### Step 3.5 — Briefing builder and Telegram delivery

- **Goal.** The product, on your phone.
- **Depends on.** 3.4, 2.7.
- **Tasks.** Render the five sections (market view; ≤2 ideas with *every gate value*;
  rejects with killing gates; open thesis status; cost); include the hypothetical position
  size from the advisory risk gate; format for a phone so the decision is visible without
  scrolling; Telegram adapter with delivery confirmation and a fallback if sending fails.
- **Deliverable.** `application/briefing.py`, `adapters/telegram/notifier.py`, templates.
- **Done when.** You receive a real briefing from real data and can act on it without
  opening a laptop.
- **Covers.** FR-7.2, FR-7.3, FR-6.2, FR-7.12.
- **Estimate.** 5 hrs.

### Step 3.6 — Decision capture

- **Goal.** Your answer and your reason, reliably recorded. **The weakest link in v1.**
- **Depends on.** 3.5, 1.7.
- **Tasks.** Telegram inline buttons for took/skipped/modified; a follow-up prompt for the
  reason; write to `user_decisions` and `override_log`; **a nag if a briefing has
  unanswered ideas by 10:00**; a weekly completeness report ("you answered 4 of 5").
- **Deliverable.** `interfaces/telegram/handlers.py`, nag job, completeness report.
- **Done when.** Ten consecutive briefings have a recorded decision and reason for every
  idea.
- **Covers.** FR-7.4.
- **Estimate.** 4 hrs.

### Step 3.7 — Scheduler and the post-close cycle

- **Goal.** It runs without you starting it.
- **Depends on.** 3.4, 3.5.
- **Tasks.** APScheduler: 08:15 research, 15:45 review, snapshot times, nightly jobs; all
  trading-day aware; failure alerts; a 15:45 cycle that resolves open ideas and reports the
  day.
- **Deliverable.** `application/scheduler.py`, `application/review_cycle.py`.
- **Done when.** Five consecutive sessions run unattended on schedule.
- **Covers.** FR-7.1, FR-9.6.
- **Estimate.** 3 hrs.

### Step 3.8 — Scoring, calibration, monitors

- **Goal.** The scorekeeper, live from the first briefing.
- **Depends on.** 3.7, 1.7, 2.8.
- **Tasks.** Score every idea on hypothetical P&L after full costs, taken or not; outcome
  resolution rules per setup (target hit, stop hit, time stop, invalidated); Brier score,
  reliability curve and resolution against a naive base-rate baseline; **the sizing lock —
  a test proving sizing cannot consume confidence until ≥50 resolved ideas beat baseline**;
  the no-trade-rate monitor with its 40–60% band and an alert outside it; a
  briefing-engagement metric.
- **Deliverable.** `application/scoring.py`, `application/calibration.py`, monitors.
- **Done when.** Calibration numbers are produced on synthetic resolved ideas; the sizing
  lock test passes; the no-trade alert fires on a synthetic run below 20%.
- **Covers.** FR-7.6 to FR-7.10.
- **Estimate.** 6 hrs.

### Step 3.9 — Deploy and observe

- **Goal.** Runs on something that isn't your laptop lid.
- **Depends on.** 3.7, 1.9.
- **Tasks.** Docker Compose on a small always-on box (**no static IP needed — nothing
  places orders in v1**); structured JSON logs with `cycle_id` correlation; metrics and a
  dashboard for feed health, cycle duration, gate pass rates and cost; alerting on cycle
  failure, feed loss, schema failure, cost cap; the three-schema-failures-in-a-month halt.
- **Deliverable.** deployment config, dashboard, alert rules, runbook.
- **Done when.** The stack survives a reboot unattended and you get a briefing the next
  morning without touching anything.
- **Covers.** FR-11.1, FR-11.3, FR-11.6, FR-9.5, FR-9.7.
- **Estimate.** 5 hrs.

**Phase 3 exit (target 2027-01-15).** A real briefing arrives daily, unattended, and your
decisions are being recorded. **This is the product. Everything after is evaluation.**

---

## Phase 3.5 — Run it (3 months, the actual work)

No new features. This phase is where the project succeeds or fails, and it's mostly
patience.

**Daily, ~10 minutes.** Read the briefing. Decide. **Record the reason** — if you skip
this, the quarter is wasted. Glance at capture health.

**Weekly, ~1 hour.** Review the week's ideas and outcomes. Read the rejected lists: which
gate is doing the killing? Note any briefing you didn't read, and why not — that's a
product signal, not a personal failing. Fix bugs only; resist features.

**Monthly, ~3 hours (first Sunday).** Hit rate by setup and by rank. Expectancy after
costs. Brier vs baseline. No-trade rate. Review the override log: **what rule have you been
applying that the system doesn't know about?** That question is the entire point of the
quarter. Change at most one threshold, write down why, and date it.

**Rules for this phase.**
1. No new setups. No new data sources. No new agents.
2. Threshold changes are logged with a date and reason, and reset the evaluation window.
3. If you find yourself wanting to automate execution in week 3, that's the urge Gate 2
   exists to overrule.

**Exit.** Gate 2 of [07-stop-criteria.md](07-stop-criteria.md) at ~60 sessions. Pass → v2.
Fail → stop, or narrow, per what's written there. **"Keep the briefing, never automate" is
a successful outcome**, not a consolation prize.

---

## Phase 4+ — v2 and beyond (only after Gate 2)

Deliberately coarse. Specifying it in detail now would be fiction — three months of journal
data will change it.

| Step | Goal | Notes |
|---|---|---|
| 4.1 | Fast-loop engine process | Separate process; zero LLM in the order path |
| 4.2 | Entry-condition watcher | Tick-driven; latency budget met with LLM services stopped |
| 4.3 | Risk gate switched to enforcing | Same code as v1, different wiring — by design |
| 4.4 | Telegram approval with buttons and timeout | No reply ⇒ no order |
| 4.5 | Paper broker adapter and simulated fills | Realistic slippage, partial fills |
| 4.6 | Position manager | Stops, targets, time stops, invalidation exits, expiry-eve flatten |
| 4.7 | Kill switch and flatten-all | Must work from your phone with the engine wedged |
| 4.8 | Reconciliation | Broker vs internal state every cycle; divergence halts |
| 5.x | 100 paper trades across a regime change | Gate 3 |
| 6.x | Live, micro size | Dhan algo registration, static IP, drawdown halts in code, Gate 4 |

---

## Dependency map

```
0.1 ─┬─ 0.2
     ├─ 0.3 ──┬─ 1.1 ──┬─ 1.2 ─┬─ 1.3 ── 1.4 ─┬─ 1.8 ─┐
     ├─ 0.4 ──┤        │       ├─ 1.5         ├─ 2.2 ─┤
     ├─ 0.5 ──┘        └─ 1.8  └─ 1.6         │       │
     └─ 0.6                                   │       │
0.4 ── 2.1 ─────────────────────────────┬─ 2.3 ◀──────┤
                                        ├─ 2.4 ◀──────┤
                                        ├─ 2.5 ── 2.6 │
                                        └─ 2.7 ── 2.8 │
0.5 ── 1.7 ────────────────────────────────────────┐  │
0.3 ── 3.1 ─┬─ 3.2 ─┐                              │  │
            └─ 3.3 ─┴─ 3.4 ── 3.5 ─┬─ 3.6 ◀────────┘  │
                                   ├─ 3.7 ── 3.8 ◀────┘
                                   └─ 3.9
```

**The critical path** is 0.1 → 0.3 → 1.1 → 1.2 → 1.3 → 1.4 → 1.8 → 2.3 → 3.3 → 3.4 → 3.5.
Everything else has slack. If you have one free evening, spend it on the critical path.

---

## 7. If you have less time than this assumes

The plan above is ~90 hours of focused work, so roughly 11 weeks at 8 hrs/week. Under 5
hrs/week, cut like this rather than stretching to six months:

1. **Drop Setup A entirely** (steps 2.3 and most of 3.2). Index-only halves the project —
   no news pipeline, no entity resolution, no credibility scoring, no labelling 200
   articles. Saves ~25 hours.
2. **Universe of two.** Nifty and Bank Nifty only, skipping step 1.2 almost completely.
3. **Keep every one of these:** 1.1 (snapshotter), 1.7 (journal), 2.7 (risk gate),
   3.8 (calibration). These are what make it a project rather than a script.

That version still delivers a real daily briefing and still answers the question the whole
project exists to answer. It's a smaller product, not a lesser one.

---

## 8. Start here, this week

1. **Step 0.1 and 0.3** — two evenings, no market access needed. Do them now.
2. **In parallel, confirm with Dhan:** API access enabled, and your plan's **websocket
   instrument cap**. That number can force step 1.2's universe smaller.
3. **Then step 1.1**, even as an ugly cron script. Every day you wait is a day of options
   history you can never recover.
