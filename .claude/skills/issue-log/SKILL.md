---
name: issue-log
description: Record, triage and close development issues for this trading project in docs/state/ISSUES.md. Use when something breaks, a bug or data problem is found, a question blocks progress, or an issue needs closing.
---

# Issue log

Lightweight on purpose. The point is that nothing found mid-step gets lost or fixed as a
drive-by.

## Recording

Append to the `## Open` section of `docs/state/ISSUES.md`:

```markdown
### ISS-012 — bars_5m volume disagrees with Dhan on low-volume strikes
- **Found:** 2026-11-03, during Step 1.3 verification
- **Severity:** data-correctness
- **Symptom:** our volume is ~8% higher on 3 of 25 instruments, only after 14:30
- **Impact:** volume-ratio gate in Setup A would read too high → false positives
- **Hypothesis:** double-counting ticks across a reconnect boundary
- **Status:** open · **Blocks:** 1.3 sign-off
```

Severity is one of: **data-correctness**, **safety** (anything touching the risk gate,
sizing, order path or a limit), **correctness**, **reliability**, **cosmetic**.

## Triage rules

1. **`safety` and `data-correctness` block the step.** Nothing proceeds until they are
   fixed or consciously accepted in writing.
2. Everything else is logged and continues. **Do not fix an unrelated issue inside a step**
   — it makes the diff unreviewable and the verification ambiguous.
3. An issue found during verification gets referenced in `VERIFICATION.md` too, so a future
   reader knows the step passed with a known caveat.
4. A data-correctness issue affecting stored history also needs a note on whether the
   affected data is recoverable. **Chain snapshots are not** — that raises the severity.

## Closing

Move the entry to `## Closed` with two lines added:

```markdown
- **Fixed:** 2026-11-05 in commit abc1234 — dedupe ticks by (token, exch_ts, seq)
- **Verified:** re-ran verify 1_3, volume now matches within 0.1% on all 25
```

Keep `## Closed` to the last ~20 entries; move older ones to `ISSUES-ARCHIVE.md`. The open
section is read every session, so it must stay cheap to read.

## Rules

- Every issue names its **impact on a trading decision** where there is one. "Volume is 8%
  high" is a bug; "the volume gate would produce false positives" is why it matters.
- Never close an issue without a verification line.
- If the same issue recurs twice, it is not a bug any more — it is a design problem, and it
  goes to `DEVLOG.md` as a decision to make.
