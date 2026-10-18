# DEVLOG — append-only record of what happened and why

One entry per session that changed something. Newest at the **bottom**, so `tail` gets the
recent past cheaply.

**What belongs here:** decisions a future session would otherwise re-litigate, surprises,
rejected alternatives, and anything that contradicts a plan doc.
**What does not:** what the code does (read the code), routine progress (that's `STATE.md`),
or bugs (that's `ISSUES.md`).

Entry format — keep it to five lines or fewer:

```markdown
## 2026-11-03 · Step 1.3
- **Built:** tick→bar aggregation, reconnect with gap rows
- **Decision:** gaps are recorded, never interpolated. Interpolated bars would make the
  volume gate silently wrong and the backtest optimistic.
- **Surprised by:** Dhan sends a snapshot tick on subscribe; it must not count as volume.
- **Rejected:** storing bars only, no ticks. Ticks are needed to debug the volume gate.
- **Follow-up:** ISS-012
```

### Rollup policy

At the end of each month, add a `## Rollup — <month>` entry summarising that month in ≤10
lines, and move the month's individual entries to `DEVLOG-ARCHIVE.md`. The live file stays
short; the archive stays greppable. Without this, the file that was meant to save tokens
becomes the thing that costs them.

---

## 2026-10-14 · Planning
- **Built:** docs 00–12 — flow, architecture, agents, roadmap, inputs, realities, v1 scope,
  stop criteria, requirements, plain-English features, build plan, interfaces/orders,
  verification.
- **Decision:** v1 is a research briefing with no order path. The earlier framing treated
  the briefing as a stepping stone; that was backwards — it is the product.
- **Decision:** proposals cannot structurally express a position size. This is the single
  constraint the rest of the architecture exists to protect.
- **Surprised by:** Bank Nifty weekly expiry is withdrawn (monthly only) and lot sizes
  changed with the Jan 2026 series. Writing the setups from memory would have specified a
  contract that does not exist.
- **Follow-up:** IV-rank gating created a bootstrapping problem — no IV history exists and
  it cannot be backfilled. Snapshotter is now the first action of Phase 1.

## 2026-10-15 · Dev workflow
- **Built:** `CLAUDE.md`, five skills in `.claude/skills/`, `docs/state/` (STATE, DEVLOG,
  ISSUES), `docs/13-dev-workflow.md`.
- **Decision:** split the requested single dynamic `skills.md` into a small stable
  `CLAUDE.md` + on-demand skills + rewritten-in-place state files. One growing file would
  be re-sent in full on every request and would defeat its own token goal.
- **Decision:** `STATE.md` is rewritten, `DEVLOG.md` is appended with monthly rollups. An
  unbounded append-only context file is a slow leak.

## 2026-10-15 · Code style
- **Built:** `docs/14-code-style.md`; §4b added to `CLAUDE.md`; `build-step` skill now
  points at it during implementation.
- **Decision:** target reader for all code is an intermediate Python developer with no
  context. Boring beats clever, and the risk gate gets the most boring version available
  because it is read under pressure during an incident.
- **Decision:** complexity is earned in exactly three places — the async market-data
  adapter, hypothesis property tests on the risk gate, and the LangGraph graph. Outside
  those, sophistication is a smell.
- **Rejected:** a lambda/registry pattern for the setup gates. Compact, but returns bare
  booleans, is not unit-testable per gate, and gives a dict key instead of a stack trace.

## 2026-10-15 · Steps 0.1 + 0.2
- **Built:** repo scaffold — Python 3.12 pinned, ruff/mypy-strict/pytest/hypothesis/
  pre-commit/detect-secrets, 7 layer packages with docstrings, `make check`, the verify
  harness (`make verify STEP=0_1`), CI workflow, `.importlinter` with 4 contracts.
- **Decision:** 0.2 was built in the same session as 0.1, because `make check` calls
  `lint-imports` — shipping 0.1 alone would have meant committing a gate that could not run.
- **Decision:** the `domain-is-pure` contract forbids `time` and `datetime` too, not just
  I/O libraries. Stricter than the plan text, and consistent with rule 5 (domain takes
  timestamps as arguments). Consequence: domain modules needing `datetime` for annotations
  must import it under `if TYPE_CHECKING:`. Step 0.4 will hit this first.
- **Surprised by:** ruff's default rule set flagged two things worth fixing rather than
  silencing — a magic number in the verify runner and a function-local import in a test.
  Both fixed properly; no `noqa` added.
- **Rejected:** `PLR0913` (too many arguments) stays ignored project-wide. Gate functions
  take explicit values rather than a context object on purpose (docs/14 section 2), and
  that rule fights the house style directly.

## 2026-10-15 · Package rename
- **Built:** `src/ativa` → `src/trading_desk` across the repo: package tree, `.importlinter`
  contracts, `pyproject.toml`, verify script, scaffold test, `VERIFICATION.md`, `docs/01`,
  `docs/04`, `STATE.md`.
- **Decision:** `trading_desk` over `research_desk`. A trading desk does research *and*
  executes, so the name still fits once v2 places orders — `research_desk` would have felt
  wrong the day the order path landed.
- **Decision:** distribution name is `trading-desk` (hyphen, PEP 508 convention), import
  package is `trading_desk` (underscore). Different on purpose, not an inconsistency.
- **Note:** done now rather than later on purpose — the rename touched 8 files today and
  would have touched every module written after it.

## 2026-10-15 · Step 0.3
- **Built:** `infra/config.py` (settings split by concern, SecretStr, fail-fast naming the
  missing env vars), `infra/logging.py` (redaction filter), `.env.example`, 10 tests,
  `scripts/verify/step_0_3.py`.
- **Decision:** settings are split into Dhan / LLM / Telegram / Infra / App rather than one
  object. A phased build needs Dhan credentials months before an Anthropic key exists, and
  one object would force every key present from day one.
- **Decision:** loaders take an explicit `env_file` parameter. Needed because pydantic
  evaluates `model_config` at class-definition time, so patching a module constant in a
  test does nothing.
- **Surprised by:** a test silently read the real `.env` once it was populated — it passed
  for the wrong reason while the file was empty. Fixed with the parameter plus a
  `conftest.py` autouse fixture stripping the five real credential variables from every
  test. This is the class of bug that only appears after credentials arrive.
- **Rejected:** having the redaction filter drop records containing secrets. Dropping the
  line hides the event as well as the secret; rewriting keeps the event.

## 2026-10-15 · Step 0.4
- **Built:** `domain/values.py` (Money, Price, Quantity, Percent), `domain/enums.py`,
  `domain/instruments.py`, `ports/clock.py`, `adapters/clock.py`, `tests/fakes/clock.py`,
  `tests/domain/test_domain_is_pure.py`, 39 tests, `scripts/verify/step_0_4.py`.
- **Decision — architecture change.** The `domain-is-pure` import contract no longer bans
  `datetime`. It banned a *type* when the real rule is that domain never *reads* a clock:
  an option expiry is a `date`, and `InstrumentRef` cannot express one without it. The
  alternative was `if TYPE_CHECKING` imports throughout, which breaks for any runtime-
  validated model (step 2.1's Pydantic contracts) and makes the code harder to read for
  no safety gain. Two compensating controls, both stricter than the import ban:
  `tests/domain/test_domain_is_pure.py` scans every domain module for clock calls
  (catching `date.today()`, which the import ban never did), and the step 0.4 verify
  check scans `src/` for `datetime.now(` outside the clock adapter. Both were induced and
  witnessed. **Reversible — say so and the ban comes back with TYPE_CHECKING imports.**
- **Decision:** `Money` and `Price` are separate types even though both hold paise. A
  price is per unit, money is an amount, and `Price.times(Quantity) -> Money` is the only
  bridge. Confusing the two is how a position gets sized at one contract's premium.
- **Decision:** rounding is ROUND_HALF_UP, not Python's default bankers' rounding. For
  money, 0.005 rounding to 0.00 surprises people, and surprise in sizing code is expensive.
- **Surprised by:** the verify check for stray clock calls produced two false positives —
  a compiled `.pyc`, and prose in a docstring that wrote the call literally. Fixed by
  scanning `.py` files in Python rather than shelling out to `grep`, whose
  directory-exclusion flags differ between BSD and GNU. A check that cries wolf gets
  ignored, which is worse than no check.
- **Rejected:** storing `Percent` as a fraction. Every threshold in docs/06 is written in
  percent, and converting at the boundary is where scale errors come from.

## 2026-10-15 · Step 0.5
- **Built:** `docker-compose.yml` (Postgres with TimescaleDB + pgvector, Redis), alembic
  scaffolding and the 0001 baseline migration, `infra/db.py`, five `make db-*` targets,
  `scripts/verify/step_0_5.py`.
- **Decision:** one Postgres with both extensions, not separate databases. Bars, news,
  journal and agent memory can then be joined, and there is one backup and one restore
  drill rather than three.
- **Decision:** the database URL is read from `.env` by `alembic/env.py` rather than
  written into `alembic.ini`. A tracked config file should never be able to hold a
  credential.
- **Decision:** `downgrade()` on the baseline is a deliberate no-op. Dropping the
  timescaledb extension would destroy every hypertable in the database - not a reasonable
  response to rolling back one migration. `make db-reset` is the dev path to clean.
- **Decision:** Redis runs with persistence off, and the verify script asserts it. Redis
  is transport; durable state belongs in Postgres. If something important ends up in
  Redis, that failing check is the signal.
- **Decision:** migrations are hand-written, `target_metadata = None`, no autogenerate.
  Autogenerate does not understand hypertables, and a time-series schema written by hand
  is clearer than one diffed from models.
- **Known gap:** `make check` does not start Docker, so CI cannot catch a broken compose
  file. Only `make verify STEP=0_5` exercises infrastructure. Acceptable while the stack
  is two containers.

## 2026-10-16 · Step 0.6 — Phase 0 complete
- **Built:** 9 ports (`market_data`, `option_chain`, `news`+`filings`, `llm`, `notifier`,
  `journal`, `broker`, `clock`), 9 fakes, `domain/market.py` and `domain/news.py` for the
  types the ports return, a conftest network guard, 20 tests,
  `scripts/verify/step_0_6.py`.
- **Decision:** the broker port is `BrokerReadPort`, named so that adding writes means
  adding a *new* port rather than a method. No `place_order` exists anywhere - not
  commented out, absent - and the verify script fails if one appears on any port or fake.
  This matters more than it looks: Dhan documents no read-only token scope, so the
  credential in `.env` can trade. The control is the shape of the code.
- **Decision:** ports return domain objects, never vendor payloads. `OptionChainPort`
  returns `OptionChainSnapshot`, so when Dhan renames a field exactly one adapter changes.
  That forced `domain/market.py` and `domain/news.py` to exist now rather than being
  invented ad hoc in Phase 1.
- **Decision:** the network guard blocks outbound sockets but permits loopback, so
  `integration`-marked tests can still reach local Postgres and Redis. A test that hits
  the network fails with a message naming the fix rather than a timeout.
- **Decision:** greeks are floats while prices are integer paise. Greeks are model outputs
  with no exact decimal value and no money is computed directly from them. Deliberate
  exception to the no-floats rule, recorded so it is not mistaken for an oversight.
- **Decision:** `ARG002` is ignored for `tests/fakes/**` only. A fake must match its
  Protocol's signature even where it ignores an argument; dropping the parameter would
  stop it satisfying the port.
- **Surprised by:** the Phase 0 sweep failed step 0.5 because Docker containers had exited
  while the machine slept. `restart: unless-stopped` does not help when Docker Desktop
  itself is not running. `make db-up` fixes it, data intact - noted in VERIFICATION.md so
  it is not re-diagnosed later.

## 2026-10-16 · Step 1.1a
- **Built:** `adapters/dhan/{client,token,chain}.py`, `application/universe.py`,
  `config/universe_starter.yaml`, 20 tests, `scripts/verify/step_1_1a.py`.
- **Live verified:** a real NIFTY chain - 235 strikes, spot 23146.15, expiry 2026-10-19,
  greeks present. The fixture-based tests could only prove we parse what we *think* Dhan
  sends; this proves what they actually send.
- **Finding (ISS-004):** expiries are not always Tuesday. Holidays move them to the
  previous trading day, confirmed against the live list and the 2026 holiday calendar.
  **Nothing in this codebase may compute an expiry date.** The adapter already reads them
  from the exchange, which is why the finding cost an afternoon rather than a quarter of
  wrong backtests.
- **Decision:** the Dhan option chain rate limit is one request every 3 seconds, so the
  client spaces calls itself rather than trusting callers. Using 3.2s, not 3.0, because
  the server measures arrival time. Consequence: a 27-instrument snapshot takes ~3
  minutes, so 1.1b's scheduler treats a snapshot as a minutes-long job.
- **Decision:** missing greeks and IV map to None, never 0.0. An illiquid strike has no
  IV, and a zero would corrupt the IV-rank series permanently - the one series that
  cannot be rebuilt.
- **Decision:** the token source is a swappable provider. `StaticTokenProvider` (24-hour
  token) works today; the unattended PIN+TOTP option puts the account PIN in .env and is
  a deliberate decision deferred, not a default.
- **Known caveat:** Dhan's chain response has no exchange timestamp, so `exchange_time`
  equals `received_at`. Commented in the adapter so the equality is not read as a bug.

## 2026-10-16 · Step 1.1b
- **Built:** `ports/chain_archive.py`, `adapters/store/parquet_chain.py`,
  `application/snapshotter.py`, `adapters/notify/logging_notifier.py`,
  `scripts/snapshot_once.py`, 24 tests, `scripts/verify/step_1_1b.py`.
- **Live:** captured 2/2 instruments, 1210 rows, written to
  `data/chains/date=2026-10-16/underlying=*/snapshot_HHMM.parquet`.
- **Bug found live (and fixed):** the step 0.3 redaction filter stringified every log
  argument, breaking httpx's `%d` status format. Only our own `%s`-only log lines had
  exercised it. Now redacts strings only; two regression tests added.
- **Bug found live (and fixed):** Dhan sends `0.0` rather than null for strikes it has not
  priced. Storing those would have dragged the IV-rank percentile down permanently for
  every unquoted strike. Exact zero now maps to None. **This is the docs/12 section 6
  failure mode exactly** - "0 nulls of 740" read as a clean capture and meant the opposite.
- **Bug found live (and fixed):** polars inferred the Parquet schema from the first rows,
  which had no greeks, then failed on the first real float. Schema is now declared, which
  also keeps column types identical across a multi-year archive.
- **Decision:** partitions are keyed by IST trading date, not UTC. A trading day is an IST
  concept, and a late-evening run would otherwise land under the previous UTC date.
- **Decision:** the snapshotter catches broadly in `_capture_one`. The one place it is
  right: one instrument failing must not cost the other 26, and an unexpected exception
  type is exactly the case that must not take the run down.
- **Decision:** alerts go to a `LoggingNotifier` until Telegram exists in step 3.5. A log
  line a human may not read beats a report that does not exist.

## 2026-10-16 · Instrument master (permanent fix for the universe)
- **Built:** `adapters/dhan/instruments.py` (downloads and caches Dhan's 34MB scrip
  master), `domain/instruments.py` gains `UnderlyingInfo` and `InstrumentCatalogue`,
  `application/universe.py` rewritten to resolve symbols against the master.
- **Decision:** config lists symbols only. Security ids and lot sizes are resolved from
  the master at runtime. Neither is guessable - NIFTY is id 13 in the index segment and
  26000 in the derivatives segment, and the chain API wants the index one.
- **Finding:** TATAMOTORS has no listed options. Removed from the starter universe. A
  hand-written list would have carried it until a snapshot silently failed every day.
- **Finding:** 218 NSE underlyings have listed options; NIFTYFPI has no index-segment
  row, so its chain cannot be requested. Unresolvable symbols are returned in
  `InstrumentCatalogue.unresolved` rather than skipped, because a symbol that vanishes
  silently is a gap nobody notices until the IV history is needed.
- **The layering rule earned its keep:** the first version had `application` importing
  the Dhan adapter for the catalogue types. `import-linter` failed the build. The right
  fix was moving the types to `domain/` - they describe what exists to trade, which is a
  domain fact; only the downloading is adapter work.

## 2026-10-16 · Rate limiting (permanent fix for 429s)
- **Finding:** a full 26-instrument run at the documented 3s spacing (we used 3.2s) got
  HTTP 429 on two instruments. The documented limit is a floor, not a guarantee, and the
  expiry-list calls share the same budget.
- **Fix:** interval widened to 4.5s, plus retry-on-429 with 10/30/60s backoff. Only 429
  is retried: retrying a bad token or a bad instrument burns the rate-limit budget for
  everything else.
- **Verified:** 26/26 instruments, 4942 rows, 3m51s, clean. The partial-capture path also
  proved itself first - it warned loudly and named both failures rather than leaving a
  silent gap.
- **Consequence:** a full run takes ~4 minutes. Irrelevant for a job that fires three
  times a day, and the scheduler in 1.1c must allow for it.

## 2026-10-17 — step 1.1c verified live: the app authenticates and schedules itself

- **Ran `trading-desk run` against the real stored token.** No browser opened: the grant
  from the previous evening was still usable, which is the behaviour the design intended -
  a login is asked for only when there is no usable token.
- **Measured the consent token's life: exactly 24 hours** (issued 23:46 IST 16th, expires
  23:46 IST 17th). This answers the question ISS-005 was opened to settle. One login
  covers a whole trading session's three captures, so the scheduler is viable as built;
  see ISS-005 for what is still unresolved (multi-day unattended running).
- **Found and fixed: operational output was invisible under redirection.** The first run
  was piped to a file and logged only the apscheduler lines - every `print` was missing.
  Cause: logging writes to stderr, which Python keeps line-buffered when redirected,
  while stdout is block-buffered, so the `print` output sat in a buffer that a
  long-running scheduler never flushes. `PYTHONUNBUFFERED=1` showed the missing lines and
  confirmed the diagnosis.
  - **Fix:** operational output in `interfaces/cli.py` now goes through
    `logging.getLogger("trading_desk.app")`. `print` survives only for usage errors, on
    stderr, where it belongs. `ensure_valid_token` keeps its own logger default rather
    than being handed `print`.
  - **Why it matters beyond tidiness:** this is the output path for an unattended job.
    Under `nohup` or `launchd` every capture summary and every per-instrument failure
    would have been silently discarded - the project would have lost precisely the
    evidence that a capture went wrong.
- **Not provable today:** 2026-10-17 is a Saturday, so `trading-desk snapshot` correctly
  logged `market closed (Saturday) - skipping capture` and captured nothing. The calendar
  guard is therefore verified; the live capture path is not re-verified today. It was
  verified on the 16th (26 files under `data/chains/date=2026-10-16`).
- **Still open:** `config/holidays.yaml` is `verified: false` and the app warns about it at
  every startup, by design.

## 2026-10-17 — step 1.2: universe from measurement, not memory

- **What it does.** `trading-desk survey` measures one nearest-expiry chain per F&O
  underlying, ranks by premium turnover, and writes `config/universe.yaml` with the
  measurement behind every included name and a reason for every excluded one. The
  snapshotter reads the generated file when it exists and the hand-written starter list
  when it does not, so a fresh clone still runs.
- **Why premium turnover.** Not equity turnover, which measures the wrong market; not
  notional, which is dominated by high-strike names; not OI alone, which is a stock
  rather than a flow. Rejections recorded in the module docstring.
- **The candidate set had to come from the exchange, not the archive.** Ranking from the
  Parquet archive was the obvious move and is circular: the archive holds only the 26
  names already being captured, so it would re-rank the guess and never discover anything.
  The instrument master offers **218 underlyings with listed options** - that is the
  candidate set, and the reason the survey costs ~30 minutes at the rate limit.
- **Added a third floor that is not in the build plan: priced-leg fraction.** A chain can
  report healthy turnover concentrated in two near-the-money strikes while most legs have
  never traded. Turnover and OI both pass; the chain is still untradeable. 25% of legs
  must carry an IV.

### Two live findings, one of which invalidated code written the same hour

- **A weekend survey does not return zeroes.** The first guard assumed it would: if most
  names show zero turnover, refuse to rank. A live two-name run on Saturday measured NIFTY
  at ₹5,126 cr premium turnover and RELIANCE at ₹224 cr - Friday's session, served
  unchanged. The guard would never have fired.
- **`exchange_time` cannot detect it either.** The obvious fix was to compare the chain's
  own timestamp against the clock. Dhan stamps that field with the time it *answered the
  request*: a 00:24 IST Saturday survey reported 00:24 IST Saturday over Friday's volume,
  and the computed age was `-0.0` hours. The staleness check was built, tested, and then
  removed the same session because the signal it relied on does not exist.
- **Where freshness actually comes from: the trading calendar, in the caller.** The survey
  now makes no claim about freshness and carries no timestamp on `LiquidityMeasure`, with
  the reason in its docstring so the field is not helpfully re-added later. The CLI checks
  `reason_closed(today)` before authenticating or sending a single request, and
  `config/universe.yaml` records `measured_on`. Logged as ISS-006, including the part that
  still needs watching: the archive's `exchange_time` column must never be read as a trade
  time.
- **Cost of finding it live rather than in a fixture:** about twenty minutes and one
  deleted guard. Cost of not finding it: a universe ranked on an arbitrary past session,
  looking entirely healthy. This is the docs/12 §6 "passing but broken" case again, and
  the second time in two steps that a fixture could not have caught it.

### Not delivered, deliberately

- **20-day average turnover.** One day of archive exists. The survey is a point
  measurement and the file records `window_days: 1`, so it cannot be mistaken for the
  average the plan eventually wants.
- **Six-month F&O-entrant and own-history exclusions.** The first needs listing dates the
  instrument master does not carry; the second would exclude all 218 names today.
- **NSE daily ban list.** Needs a source URL - a decision, not a default.
- **Thresholds are unreviewed.** `config/exchange_rules.yaml` is `verified: false` and
  `verify 1_2` fails on exactly that check, by design. They were set permissive so a first
  real survey shows the shape of the distribution rather than excluding everything.

## 2026-10-17 — re-ran every verification step; found three regressions

Prompted by the question "are all scenarios working fine?". Running the full
verification sweep rather than answering from memory turned up three things, none of
which the test suite could see.

- **Two `datetime.now()` calls outside the clock adapter** (`adapters/dhan/token_store.py`,
  `adapters/dhan/login.py`), breaking CLAUDE.md rule 5. Introduced during the step 1.1c
  token work; `verify 0_4` would have caught it immediately, but Phase 0 had not been
  re-run since. Both now read through `SystemClock`, and `hours_remaining` takes an
  injectable `now`.
  - **The check itself was also wrong:** a literal text scan, which flagged the very
    docstrings explaining the rule. Its own docstring admitted the flaw. Replaced with an
    AST walk that sees calls only - and which additionally catches `dt.now()` after
    `import datetime as dt` and `date.today()`, neither of which the text scan looked for.
    Induced an aliased violation to prove it fires.
- **`verify 0_6` was undercounting ports.** `ChainArchivePort` was added in step 1.1b and
  never added to `EXPECTED_PORTS`, so "every port has a fake" had silently not covered it.
  Ten ports live in nine files, because `ports/news.py` declares two; the count now says so.
- **Two entry points could have archived a non-trading day.** The serious one. See ISS-006b:
  the calendar guard existed only in the CLI, while a verify script and an on-demand script
  wrote to the archive unchecked. Moved into `ChainSnapshotter.run()`, which is the point of
  the write. Changing the constructor made mypy name all four call sites, which is how the
  two unguarded ones were found.
  - Moving the guard inward needed the IST date inside an application module, and importing
    `to_ist` from `adapters/clock.py` broke the dependency rule. Rather than an exception,
    `IST` and `to_ist` moved to `domain/timezones.py` - a timezone is reference data and a
    conversion is arithmetic, so neither was ever adapter-shaped. `adapters/clock.py`
    re-exports them so existing callers are untouched. Second time import-linter has forced
    a correct move rather than a workaround.
- **One data finding, logged as ISS-007.** `verify 1_1b` failed on `max IV 419.45`. Real data:
  deep-ITM strikes that did not trade keep a stale `last` several times intrinsic value, and
  the solver backs 350-420% out of it. ATM IV over the same archive is 6.2-35.5%, and turnover
  is `last x volume` with volume 0 on every affected row, so both things the project depends on
  are clean. The *check* was mis-scoped, not the data: it now bounds ATM IV and separately
  asserts the implausible values only ever sit on untraded strikes.

**Where the sweep stands:** 0.1, 0.3, 0.4, 0.5, 0.6, 1.1a all pass. 1.1b is 7/8 - the
failure is `live_capture`, which cannot pass on a Saturday and must be re-run on a trading
day. 1.2 is 6/7, the failure being the unreviewed liquidity thresholds, which is a decision
rather than a defect.

**Process note worth keeping:** every one of these was invisible to `make check`. 183 tests
and four import contracts were green throughout. The verification harness earns its keep
precisely because it checks claims the unit tests do not make.

## 2026-10-18 — step 1.3a: market data schema and the bar builder

Step 1.3 split, because a Sunday cannot verify a websocket. 1.3a is everything
provable offline; 1.3b is the live feed.

- **Migration 0002: `ticks`, `bars_1m`, `bars_5m`, `bars_1d`, `data_gaps`.** Four
  hypertables plus a plain table for gaps. Verified by round-tripping the migration down
  to 0001 and back up, and by querying `timescaledb_information.hypertables` rather than
  trusting the migration source - a migration written correctly and never applied looks
  identical to one that was.
  - **Prices are BIGINT paise everywhere**, asserted against `information_schema` rather
    than by reading the migration. A `double precision` column would round silently and
    nothing downstream could detect it.
  - **`ticks` has no primary key, deliberately.** Two genuine trades can share a symbol
    and a timestamp; a key over those columns would reject real data.
  - **`data_gaps` is a table, not a log line.** Nothing interpolates, so the gap row is
    the only record that silence was a fault rather than a quiet market. Its ordering
    constraint was induced, not assumed: the verify script inserts a gap that ends before
    it starts and requires the database to refuse it.
- **`domain/sessions.py` and `domain/bars.py`: pure, batch, no clock.** Ticks in, bars out.
  A live feed holds one window in the application layer and calls the same function, so
  the identical code serves the websocket and the backfill.
- **Bars align to the session open, not the wall clock.** NSE opens 09:15, so 5m bars run
  09:15, 09:20 ... 15:25. Aligning to the hour would leave a two-minute stub and shift
  every later boundary - an error that reconciles perfectly against itself and not at all
  against the exchange.
- **The session is half-open.** 09:15:00 is in, 15:29:59 is in, 15:30:00 is out: a print
  at the close belongs to the closing auction, which v1 does not model, so it is rejected
  rather than crammed into the final bar.

### Three decisions where the easy answer loses information

- **No bar is invented for a quiet window.** An empty candle carrying the previous close
  is a fabricated price, and once written it cannot be told from a real one.
- **No tick is silently dropped.** Everything that does not reach a bar comes back in
  `rejected` with a reason. A vanished tick is a volume discrepancy nobody can trace.
- **Duplicates are only removed on a feed sequence number.** Two genuine trades can match
  on timestamp, price and size exactly. Without a sequence number a repeat is
  indistinguishable from a real second trade, so it is kept and volume stays honest.
  `Tick.sequence` is optional for exactly this reason, and tested both ways.
- **`Tick.quantity` is the size of that trade, never the running day total.** Feeds differ
  on which they send and the difference is invisible until volumes come out N times too
  large. Converting is the adapter's job in 1.3b - confirm which form Dhan sends before
  writing it.

### Another stale verify check

`verify 0_5` asserted `migrations == ["0001"]` and failed the moment a second migration
landed. It was asserting "0001 is applied" when it meant "the database is up to date". It
now reads the head revision from `alembic/versions` and compares. Induced by downgrading
to 0001 and confirming it fails, then upgrading back. That is **three stale checks in two
days** (0.4's text scan, 0.6's port count, 0.5's revision) - each written to assert a fact
true on the day rather than the property intended. Worth watching for in the rest.

**Logged ISS-008:** Muhurat trading runs outside 09:15-15:30 and is not modelled, so that
evening would produce no bars. Needs the NSE circular, due before 2026-11-08.

## 2026-10-18 — login, and the harness was authenticating differently from the app

- **Consent login ran clean: token valid for 24.0 hours.** Third measurement, same answer.
  The lifetime is a flat 24h from issue, not a rolling or session-bound window.
- **`verify 1_1a` still returned 401 with a fresh token**, which is what made it a bug
  rather than an expiry. Both `step_1_1a.py` and `step_1_1b.py` were building their client
  with `StaticTokenProvider(settings.access_token)` - the hand-pasted `DHAN_ACCESS_TOKEN`
  from `.env` - while the application uses `StoredTokenProvider()` and the consent grant in
  `secrets/dhan_token.json`. The two diverged the moment the consent flow landed in 1.1c:
  327 characters in `.env`, long dead; 352 in the token store, current.
  - **Why it matters more than a failing check:** the verification harness was exercising
    an auth path the application no longer uses. Every "live" check it passed was evidence
    about a code path that no longer runs in production.
  - **Fix:** both scripts now use `StoredTokenProvider()`, and report "no stored Dhan token
    - run: make login" rather than naming an env var that should no longer matter.
  - After the fix, 1.1a passes 5/5 against a real NIFTY chain: 235 strikes, spot 23,146.15,
    expiry 2026-10-19, greeks present.
- **Fixed a check whose message contradicted its own result.** `rate_limiter_spacing`
  reported `waited 3.50s` against a stated requirement of `>= 4.5s` and passed. The check
  was right - a call 1s after the previous one waits out the remaining 3.5s so the two land
  4.5s apart - but the requirement string described a different thing. A harness that prints
  an apparent contradiction and calls it PASS teaches people to stop reading it.
- **Left for the user to delete: `DHAN_ACCESS_TOKEN` in `.env`.** Now unused by every code
  path and dead as a credential. Nothing reads it; it can only cause the confusion it just
  caused. Not edited here because `.env` is the user's file.

**Sweep after the fix:** 0.1, 0.3, 0.4, 0.5, 0.6, 1.1a, 1.3a all pass. 1.1b is 7/8 waiting
on a trading day; 1.2 is 6/7 waiting on threshold sign-off.

## 2026-10-18 — step 1.4 taken before 1.3b, and done

**Order changed deliberately** (CLAUDE.md rule 10). 1.3b is the Dhan websocket and no ticks
flow on a Sunday, so building it today meant writing a binary-protocol adapter against
assumptions nothing could test - the exact pattern that has produced a finding at almost
every step. 1.4 uses the REST history API, which answers today. Its stated dependency on
1.3 was the bars schema, delivered in 1.3a. 1.3b follows on a trading day.

### What the live probe settled before any code was written

- **Response shape:** six parallel arrays (open/high/low/close/volume/timestamp), not
  records. Lengths are checked rather than assumed, because a mismatch would pair the
  wrong price with the wrong day silently.
- **Prices arrive as JSON floats** (`1313.1`). Converted with `Decimal(str(value))`, never
  `Decimal(value)`, which would preserve the binary float's error.
- **Timestamps are a true UTC epoch landing on midnight IST** of the trading date:
  1788201000 is 2026-08-31 18:30 UTC = 2026-09-01 00:00 IST. Taking `.date()` off the UTC
  value gives 2026-08-31 - **every daily bar off by one day**, consistently, in a way that
  reconciles perfectly against itself. This was the single highest-value thing probing
  first bought.
- **Depth: 940 daily bars back to 2023-01-02**, well past the two years required.
- **Intraday 5m bars start at 09:15**, independently confirming the session-open alignment
  built in 1.3a against a different endpoint.

### Built

`ports/history.py`, `ports/bar_store.py`, `adapters/dhan/history.py`,
`adapters/store/bar_repository.py`, `domain/indicators.py`, `application/backfill.py`.

- **Idempotency comes from the primary key plus ON CONFLICT DO NOTHING**, not read-then-
  write: no race, and one round trip instead of two per bar. Proven live three ways - the
  same range re-run inserted 0; a *widened* range inserted only the 246 new days per
  symbol; 940 rows hold 940 distinct dates.
- **Nothing is adjusted.** A move beyond 20% close-to-close is flagged with its date and
  left alone. Dhan's history may already be adjusted, and applying a second adjustment
  turns a visible anomaly into an invisible one.
- **ATR returns None rather than a partial figure.** An ATR over five bars is not a less
  precise ATR(14), it is a different number, and a gate comparing against it would be
  acting on something nobody authorised.

### A precision bug found by reasoning, then measured

The first ATR used integer division at every smoothing step. Wilder's recursion feeds each
truncated value into the next, so the errors accumulate into a one-sided bias instead of
cancelling. Measured over 694 bars: **ATR understated by ~5 paise, every time**. On a
₹24 ATR that is 0.2% - small, but the breakout gate asks whether a move exceeds 1.5x ATR,
so an ATR that is reliably low is a gate that fires reliably too often. Now carries a
Decimal and rounds once, ROUND_HALF_UP. Hand-checked: TRs 1000/800/700/1000 with period 3
give (1000+800+700)/3 = 833.33, then (833.33x2+1000)/3 = 888.89 -> **889 paise**, which is
what the code returns.

### Cross-endpoint agreement

`verify 1_4` compares the latest daily close against the option chain's spot for the same
instrument - two unrelated endpoints with different response shapes. Both say
**₹23,146.15**. A fixture can only prove we parse what we think Dhan sends; this is
evidence the price mapping itself is right.

### The clock check was over-broad, and my own code proved it

`verify 0_4` failed on `datetime.fromtimestamp(epoch, UTC)` in the new history adapter.
That call converts a supplied epoch and reads no clock - the ban list written yesterday
was simply too wide. Rather than delete the rule, it was narrowed to the genuinely faulty
form: `fromtimestamp` with no timezone uses the machine's local zone, so the same epoch
becomes a different wall-clock time on a server elsewhere. Induced all three forms - naive
fails, positional `UTC` passes, keyword `tz=UTC` passes.

**Ports are now 12** (added HistoricalBarsPort and BarStorePort), with a fake each, and
`verify 0_6` updated in the same change rather than a day later.

## 2026-10-18 — step 1.6: calendars, and the expiry list turns out to audit the holidays

Taken ahead of 1.5 (news), which carries a decision that is the user's: task 3 says to
check each source's licence terms, some of which prohibit automated storage. 1.6 is
offline, deterministic and unblocks the Phase 2 gates.

### The rule this step exists to enforce

**Nothing computes an expiry date.** ISS-004 found the plan's "NIFTY weekly on Tuesday"
contradicted by the venue. It is now enforced rather than merely followed: `verify 1_6`
applies the weekday rule to the live list and **requires them to disagree**. A future
simplification fails CI instead of quietly naming contracts that do not exist. `docs/06`
corrected at the same time - it still read "Weekly, Tuesday" with no caveat, which is the
sentence that caused the problem.

### The dividend: the expiry list is evidence about the holiday calendar

Reading the rule backwards, a weekly expiry that is *not* on a Tuesday means the exchange
was shut that Tuesday. That makes the venue's own expiry list a primary source for the
holiday file - stronger than any published calendar.

Run against the live list it found **2026-11-24, Prakash Gurpurb Sri Guru Nanak Dev,
missing from `config/holidays.yaml`** after sitting unnoticed through four verification
sweeps. Both NIFTY and BANKNIFTY moved to Monday 11-23.

It also settled a conflict the web could not. Published calendars disagreed on whether
Diwali Balipratipada is 9 or 10 November. The expiry moved *to* Monday the 9th, which it
would not have done if the 9th were shut - so the holiday is the 10th. Primary data
resolving a secondary-source disagreement.

`config/holidays.yaml` now states evidence per date: `expiry_shift` (proven by the venue,
re-derived on every verify run) or `published` (believed, not proven). Three of five are
expiry-proven; the two Friday holidays cannot be, since no weekly expiry falls on a Friday.

### An implementation that looked equivalent and was not

The first `displaced_expiries` scanned for Tuesdays *missing between* two listed expiries.
It found 10-20 and 11-10 and silently missed **11-24** - the one whose Monday sits at the
end of the published run, with no expiry after it to bracket against. The correct rule is
simpler and has no edge: in the weekly run, an expiry that is not a Tuesday is itself the
evidence, and the Tuesday it was moved off is the next one. Both behaviours are now in
tests, including a long-weekend case where the expiry moves back four days to a Friday.

The sparse tail needed guarding too. An expiry list runs weekly for about six weeks then
jumps to monthly and quarterly contracts; treating that tail as weekly would read every
skipped Tuesday as a holiday and invent months of closures. `weekly_expiries` ends the run
at the first gap over ten days - ten rather than seven, because a week containing a
holiday already produces an eight or nine day gap. BANKNIFTY, which is monthly only,
correctly yields no displacements at all despite having a Monday expiry.

### Sourcing

- **RBI MPC dates: verified against the RBI's own press release** under Section 45ZI, with
  the url in `config/events.yaml`. Two future meetings: 2026-12-04 and 2027-02-05. The four
  earlier FY27 meetings are deliberately omitted rather than kept - a calendar of past
  events invites a backtest to treat them as known in advance.
- **NSE 2027 holidays: not written.** The circular was not published as of 2026-10-18 and
  the third-party calendars that exist disagree with each other on Holi, Ambedkar Jayanti
  and Good Friday. An unverified 2027 list is a wrongly-listed trading day waiting to
  happen, and a missed capture cannot be recovered.
- **CPI, IIP, FOMC, Budget: left empty with the reason in the file.** A gate that avoids
  the wrong day is worse than no gate.
- **Company results dates: deferred to 1.5**, where the announcement feed lives. The table
  and query path are built so 1.5 drops into them; hand-typing a few would imply coverage
  that does not exist.

### A stale unit test, in the same family as the three stale verify checks

`test_the_calendar_declares_whether_it_has_been_verified` asserted `verified is False` and
broke the moment the calendar was actually verified. It recorded the fact of the day rather
than the property. Rewritten to load two temporary files and assert the flag follows the
file. **That is the fourth instance of this pattern** - 0.4's text scan, 0.6's port count,
0.5's revision, now a unit test. Worth watching for anywhere an assertion names a literal
that describes current state rather than intended behaviour.

**Ports are now 13** (added ExpiryStorePort), fake and `verify 0_6` updated in the same
change. Migration 0003 adds the `expiries` cache - the only thing in this step that earns a
table, because holidays and macro events are published facts that belong in reviewable
config, while expiries are fetched data with an age.
