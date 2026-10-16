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
