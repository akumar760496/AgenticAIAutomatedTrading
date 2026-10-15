---
name: session-start
description: Load minimum context at the start of a work session on this trading project - current stage, open issues, and the active build step. Use when beginning work, resuming after a break, or when asked "where were we".
---

# Session start

Goal: be oriented in under 2,000 tokens. Do not read the whole `docs/` tree.

## Steps

1. `cat docs/state/STATE.md` — current phase, active step, next action, blockers.
2. `sed -n '/^## Open/,/^## Closed/p' docs/state/ISSUES.md` — open issues only.
3. Read **only** the active step from the build plan:
   `awk '/^### Step 1\.3 /,/^### Step 1\.4 /' docs/10-build-plan.md`
4. `git log --oneline -5` — what actually happened last.
5. `tail -20 docs/state/DEVLOG.md` — why it happened.

## Then report back in this shape

```
Phase 1, Step 1.3 (market data service) — in progress
Last commit: [1.3] bar builder skeleton
Blockers: websocket instrument cap unconfirmed
Open issues: 2 (ISS-004 bar volume mismatch, ISS-007 reconnect loop)
Next action per STATE.md: implement session-boundary handling
```

## Rules

- If `STATE.md` disagrees with `git log`, trust git and say so — `STATE.md` was not updated
  at the end of the last session.
- Do not start work before reporting the above. If the user wants something different from
  `next action`, that is fine, but the mismatch gets named.
- Do not load `01-architecture.md` or `08-requirements.md` at session start. Grep them when
  a specific question arises.
