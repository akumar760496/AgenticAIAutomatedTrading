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
forbids `datetime`. See `docs/state/DEVLOG.md` 2026-10-15 for the reasoning and the two
compensating controls.
