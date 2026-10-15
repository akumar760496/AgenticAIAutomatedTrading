---
name: verify-step
description: Verify a completed build step for this trading project using docs/12-verification.md - run automated checks, cross-checks against external ground truth, and record sign-off. Use when asked to verify, test, validate or sign off a step.
---

# Verify a step

Verification is not "the tests pass". It is evidence that survives three months.

## 1. Load the step's checks

```bash
grep -n "Step X.Y" docs/12-verification.md
sed -n 'START,ENDp' docs/12-verification.md
```

Each check is tagged **A** (automated), **R** (reproducible command), **X** (external
cross-check) or **H** (human). Run A and R yourself. X and H need the user — list them
explicitly and say what you cannot do alone.

## 2. Run

```bash
make check
make verify STEP=X_Y
```

Report the actual values against thresholds, not a summary. If a check is missing from
`scripts/verify/`, write it now.

## 3. Induce the failure

**For any step containing a guard — a limit, halt, rejection, alert or cap — break it on
purpose and confirm it fires.** Then revert. An untriggered guard is a guess, not a
feature. Record what you induced and what happened.

## 4. Ask for what you cannot verify

X and H checks go back to the user as a short list, with the exact comparison to make:

```
Needs you (2 checks):
  X — compare bars_5m for RELIANCE 2026-10-14 against Dhan historical API
  H — open one chain Parquet in a notebook: does the IV smile look like a smile?
```

## 5. Sign off

Append to `VERIFICATION.md`: step id, date, full verify output, what was induced, and one
line per H check in the user's words. Then mark the step done in `docs/state/STATE.md`.

## Rules

- Do not mark a step done with X checks outstanding. Mark it `code-complete, awaiting
  cross-check` instead, and say which.
- When a number looks good, check how it would look if the feature were broken. If the two
  look identical, nothing has been verified — see §6 of `docs/12-verification.md`.
- Never edit a verify script to make it pass. Fix the code or change the threshold
  deliberately, with a DEVLOG entry saying why.
