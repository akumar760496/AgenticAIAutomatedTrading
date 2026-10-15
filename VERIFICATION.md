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
