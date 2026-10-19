# VERIFICATION — evidence that each step works

Append-only. One entry per step, added when the step is signed off. Written so that in
three months you can see *why* you believed a step was correct, not just that you did.

Kinds of evidence (docs/12-verification.md): **A** automated · **R** reproducible command ·
**X** external cross-check · **H** human check.

---

## Step 0.1 — Repository and toolchain · 2026-10-15 · PASS

```
STEP 0.1 — Repository and toolchain
  [PASS] python_version       = 3.12          (need 3.12)
  [PASS] layer_packages       = 7 of 7        (need all 7)
  [PASS] scaffold_files       = all present   (need pyproject, Makefile, gitignore, pre-commit)
  [PASS] dev_tools_pinned     = 8 pinned      (need all 7 tools)
  [PASS] git_tracked_files    = 53            (need > 0)
  [PASS] dotenv_is_gitignored = True          (need true)
  RESULT: PASS (6 of 6)
```

**A** — `make check` green: ruff (lint + format), mypy --strict over 19 files,
import-linter 4 contracts, pytest 4 passed.

**R — induced failure 1 (type error).** Appended `broken: int = "not an int"` to
`domain/__init__.py`. mypy failed as required:

```
src/trading_desk/domain/__init__.py:8: error: Incompatible types in assignment
  (expression has type "str", variable has type "int")  [assignment]
```

Reverted; `make check` green again.

**Outstanding:** none. The pre-commit hooks are installed but have not yet run on a real
commit with staged Python changes — first code commit in step 0.3 will exercise them.

---

## Step 0.2 — The dependency rule, enforced · 2026-10-15 · PASS

Built alongside 0.1 because `make check` calls `lint-imports`; leaving it out would have
meant committing a gate that could not run.

**A** — 4 contracts kept: layered architecture; agents may not import adapters; domain does
no I/O; ports stay thin.

**R — induced failure 2 (layering violation).** Appended `import httpx` to
`domain/__init__.py`. import-linter failed as required:

```
Broken contracts
Domain does no I/O - no network, DB, LLM, clock or dataframes
trading_desk.domain is not allowed to import httpx:
-   trading_desk.domain -> httpx (l.8)
```

Reverted; contracts green again.

**Note on the `domain-is-pure` contract:** it forbids `time` and `datetime` as well as the
I/O libraries, which is stricter than the plan text. Domain code takes timestamps as
arguments, so this holds — but it means a domain module needing `datetime` *as a type
annotation* must import it under `if TYPE_CHECKING:`. Recorded here because the first
domain module (step 0.4) will hit it.

---

## Step 0.3 — Configuration and secrets · 2026-10-15 · PASS

```
STEP 0.3 — Configuration and secrets
  [PASS] env_example_committed     = True                (need true)
  [PASS] env_example_keys          = 12 keys             (need all 12)
  [PASS] dotenv_present_locally    = True                (need true, not committed)
  [PASS] dhan_settings_load        = all three present   (need all three credentials)
  [PASS] secret_redacted_from_logs = True                (need true)
  [PASS] secret_hidden_in_repr     = True                (need true)
  [PASS] run_mode                  = research            (need research)
  RESULT: PASS (7 of 7)
```

**A** — 14 tests pass, `make check` green. Tests cover: settings load from env, missing
settings name the env vars, `repr()` does not leak a secret, prefixed settings, redaction
of both message and `%s` arguments, short values not registered, filter never drops a
record.

**R — induced failure (missing credentials).** Ran the loader with the Dhan variables
unset and `env_file=None`, leaving the real `.env` untouched:

```
Dhan settings incomplete. Set these in .env: DHAN_API_KEY, DHAN_API_SECRET, DHAN_CLIENT_ID
```

Names every missing key, no `NoneType` three layers later.

**R — real credentials load.** The populated `.env` loads: client id, API key and secret
all present. **No value was read, printed or logged at any point** — the check reports
presence only.

**Defect found and fixed during verification.** The first version of
`test_missing_dhan_settings_name_the_env_vars` patched the module-level `ENV_FILE`
constant, but `model_config` is evaluated at class-definition time, so the patch did
nothing and the test silently read the developer's **real** `.env` — which is why it failed
only after the file was populated. Fixed properly rather than patched around: loaders now
take an explicit `env_file` parameter, and `tests/conftest.py` strips the five real
credential variables from the environment for every test in the suite. A test that reads
live credentials passes locally, fails in CI, and can print a real secret into test output.

---

## Step 0.4 — Value objects and the money rule · 2026-10-15 · PASS

```
STEP 0.4 — Value objects and the money rule
  [PASS] float_money_rejected     = True                      (Money(10.5) must raise)
  [PASS] float_multiply_rejected  = True                      (Money * 1.5 must raise)
  [PASS] decimal_arithmetic_exact = 0.10 + 0.20 = 0.30        (not 0.30000000000000004)
  [PASS] risk_sizing_exact        = 0.25% of 10,00,000 = 2500.00
  [PASS] price_times_quantity     = ₹16,315.00                (125.50 x 130)
  [PASS] half_option_rejected     = True                      (expiry without strike raises)
  [PASS] option_renders_readably  = NIFTY 2026-10-20 25000 CE
  [PASS] no_stray_clock_calls     = 0 outside adapter
  [PASS] clock_is_tz_aware_utc    = True
  [PASS] ist_conversion           = 04:00 UTC -> 09:30 IST
  RESULT: PASS (10 of 10)
```

**A** — 53 tests pass (39 new). Money rejects floats on construction and on multiply;
rounding is HALF_UP not bankers'; Price cannot be negative; `Price.times(Quantity)` is the
only bridge to Money; a half-specified option cannot be constructed; every value object is
frozen; the clock is tz-aware UTC and the fake does not move on its own.

**R — induced failure (domain purity).** Appended a function to `domain/values.py` that
read the clock and printed:

```
assert ['values.py: datetime.now('] == []     # tests/domain/test_domain_is_pure.py
assert ['values.py: print('] == []
[FAIL] no_stray_clock_calls = 1 outside adapter    # make verify STEP=0_4
```

Both guards fired independently, and the purity test caught the `print()` as well.
Reverted; `make check` and verify green again.

**Two check defects found and fixed during verification.** The clock-call scan initially
(1) matched a compiled `.pyc` in `__pycache__`, and (2) matched prose in a docstring that
wrote the call literally. Fixed by scanning `.py` sources directly in Python rather than
shelling out to `grep` — BSD and GNU `grep` differ on directory-exclusion flags, so the
check would have passed or failed depending on the machine. The docstring was reworded.
A verify script that reports a false failure gets ignored, which is worse than not having
it.

**Architecture change made in this step.** The `domain-is-pure` import contract no longer
forbids `datetime`. See `docs/state/DEVLOG-ARCHIVE.md` 2026-10-15 (Package
rename) for the reasoning and the two compensating controls.

---

## Step 0.5 — Local infrastructure · 2026-10-15 · PASS

```
STEP 0.5 — Local infrastructure
  [PASS] compose_file_present  = True
  [PASS] postgres_reachable    = connected
  [PASS] timescaledb_installed = 2.30.2
  [PASS] pgvector_installed    = 0.8.7
  [PASS] required_extensions   = all present      (timescaledb, vector)
  [PASS] migrations_applied    = ['0001']         (baseline)
  [PASS] redis_reachable       = True
  [PASS] redis_not_persisting  = save='' appendonly=no
  RESULT: PASS (8 of 8)
```

**R — clean rebuild.** `make db-reset` destroyed the volume and rebuilt from migrations
alone; extensions and migration history came back identical. This is the check that
matters: it proves the database is reproducible from version control, not from a command
someone remembers running once.

**R — induced failure (database down).** Stopped the Postgres container:

```
[FAIL] postgres_reachable    = failed: OperationalError
[FAIL] timescaledb_installed = absent
[FAIL] required_extensions   = missing ['timescaledb', 'vector']
RESULT: FAIL (5 of 8)
```

Restarted; 8 of 8 again. The checks fail rather than skip when infrastructure is absent —
"the database was not up" is a failure of this step, not an excuse for it.

**Versions pinned by this step:** TimescaleDB 2.30.2, pgvector 0.8.7, Postgres 16,
Redis 7-alpine.

**Not yet done:** `make check` does not start Docker, so the step 0.5 verify is the only
place infrastructure is exercised. CI will not catch a broken compose file. Acceptable for
now — revisit if the stack grows.

---

## Step 0.6 — Ports and fakes · 2026-10-16 · PASS

```
STEP 0.6 — Ports and fakes
  [PASS] ports_defined         = 9              (need 9)
  [PASS] fakes_defined         = 9              (one per port)
  [PASS] every_port_has_a_fake = all satisfied  (need 0 orphans)
  [PASS] no_order_methods      = none           (v1 cannot place an order)
  [PASS] no_order_adapter_file = none           (FR-13.6)
  [PASS] suite_passes_offline  = 73 passed      (network blocked)
  RESULT: PASS (6 of 6)
```

**A** — 73 tests pass (20 new). Each fake is assigned to its Protocol-typed variable, which
is a real structural check rather than a formality. Fakes raise instead of inventing data.
The journal fake enforces append-only. Failure switches exist for chain fetch, delivery and
feed staleness, so fail-closed paths can be exercised.

**R — induced failure 1 (network access).** Added a test that opened a real socket:

```
tests.conftest.NetworkAccessError: A test tried to connect to 172.66.147.243.
Use a fake from tests/fakes/ instead.
```

Deleted immediately. Loopback stays permitted so `integration`-marked tests can still
reach local Postgres and Redis.

**R — induced failure 2 (an order method).** Added `place_order` to the broker fake:

```
[FAIL] no_order_methods = found ['FakeBrokerRead.place_order']  (need 0)
RESULT: FAIL (2 of 6)
```

Reverted. This check is FR-13.6 enforced by shape: `BrokerReadPort` exposes only
`fetch_positions`, `fetch_trades` and `fetch_funds`. There is no interface in v1 through
which an order could be placed, which matters because Dhan documents no read-only token
scope - the token itself can trade.

---

## Phase 0 — complete · 2026-10-16

```
STEP 0.1 — Repository and toolchain      PASS (6 of 6)
STEP 0.3 — Configuration and secrets     PASS (7 of 7)
STEP 0.4 — Value objects and money rule  PASS (10 of 10)
STEP 0.5 — Local infrastructure          PASS (8 of 8)
STEP 0.6 — Ports and fakes               PASS (6 of 6)
```

(0.2 has no separate script; its contracts are checked on every `make check` and its
violation was induced at the time.)

**Observed during the phase sweep:** step 0.5 failed on first run because the containers
had exited when the machine slept. `make db-up` restored them with data intact, and the
step passed again. Worth knowing: **infrastructure checks fail after any reboot until
`make db-up` runs**, despite `restart: unless-stopped`, because Docker Desktop itself does
not start automatically.

---

## Step 1.1a — Dhan chain adapter and starter universe · 2026-10-16 · PASS

```
  [PASS] universe_loads         = 2 indices, 25 stocks pending
  [PASS] lot_sizes_current      = {'NIFTY': 65, 'BANKNIFTY': 30}
  [PASS] index_segment_mapping  = 6 index symbols
  [PASS] rate_limiter_spacing   = waited 2.20s        (need >= 3.2s between calls)
  [PASS] live_chain_with_greeks = 235 strikes, spot 23146.15, expiry 2026-10-19, greeks=yes
  RESULT: PASS (5 of 5)
```

**X — live API.** A real NIFTY chain with greeks. This is the check the fixtures could not
make: they prove we parse what we *think* Dhan sends.

**X — finding (ISS-004).** Dhan's live NIFTY expiry list reads 2026-10-19 (Mon), 10-27
(Tue), 11-03 (Tue), 11-09 (Mon), 11-17 (Tue). The plan says "Tuesday". **Expiries move to
the previous trading day when the Tuesday is a holiday** - 2026-10-20 is Dussehra and
2026-11-10 is Diwali Balipratipada. Nothing in this codebase may compute an expiry date;
the adapter reads them from the exchange, which is why this cost an afternoon rather than
a quarter of wrong backtests.

---

## Step 1.1b — Chain archive and snapshotter · 2026-10-16 · PASS

```
  [PASS] live_capture              = captured 2/2 instruments, 1210 rows
  [PASS] rows_written              = 2420
  [PASS] partitioned_by_ist_date   = ['date=2026-10-16']
  [PASS] schema_is_fixed           = matches
  [PASS] unpriced_strikes_are_null = 914 null, 0 stored as 0.0
  [PASS] iv_values_are_plausible   = max IV 115.07
  [PASS] prices_are_integer_paise  = Int64
  RESULT: PASS (7 of 7)
```

**A** — 117 tests. Snapshotter: one failure does not stop the run, partial capture warns
and names the instrument, total failure is CRITICAL, a write failure is recorded not
swallowed, the nearest expiry is used. Archive: partition layout, one row per strike per
side, exact paise round-trip, refusal to write an empty file.

### Three defects found only by running it live

**1. The redaction filter broke third-party logging.** It stringified every log argument,
so httpx's `'HTTP Request: %s %s "%s %d %s"'` raised `%d format: a real number is
required, not str` on every call. Never seen before because our own log lines only use
`%s`. Fixed to redact string arguments only - a number cannot contain a secret - with two
regression tests.

**2. Dhan sends `0.0` for unpriced strikes, not null.** The first live capture stored 740
of 740 BANKNIFTY rows with a non-null IV, many exactly 0.0 on strikes with no last price,
no volume and no open interest. Those zeros would have dragged the IV-rank percentile down
for every strike that was simply not quoted - **permanently, in the one series that cannot
be rebuilt**. Exact zero is now treated as "not reported". After the fix: 914 of 2420 rows
correctly null.

This is the failure mode docs/12 section 6 describes: `IV nulls: 0 of 740` looked like a
clean capture and was the opposite.

**3. Parquet schema was inferred, and inference failed.** Early strikes had no greeks, so
polars inferred Null and then raised `could not append value: -0.00084` when a real float
arrived. The schema is now declared explicitly, which also guarantees identical column
types across a multi-year archive.

**Also corrected:** partitions are keyed by **IST** trading date, not UTC. A 22:50 IST run
would otherwise land under the previous UTC date.

**Still open:** only the two indices are captured. The 25 stocks need security ids from
the instrument master, which is step 1.2.
