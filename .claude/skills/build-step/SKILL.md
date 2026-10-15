---
name: build-step
description: Implement one numbered step from the build plan in docs/10-build-plan.md for this trading project, following the layering rules, with tests and a state update. Use when asked to build, implement or code a step like "do step 1.3" or any feature work in this repo.
---

# Build a step

One step per session where possible. A step half-done across two sessions is where context
is lost and rework starts.

## 1. Load the step, not the plan

```bash
awk '/^### Step 2\.3 /,/^### Step 2\.4 /' docs/10-build-plan.md
```

Extract: goal, depends-on, tasks, deliverable, done-when, FR ids. **If a dependency step
is not marked done in `STATE.md`, stop and say so** — building out of order is how the
chain snapshotter ends up after the strategy code.

## 2. Check the architecture before writing

Decide the layer for each new module using `CLAUDE.md` §3. If it involves I/O, you are
writing two things: a Protocol in `ports/` and an implementation in `adapters/`. The pure
logic goes in `domain/` and takes its inputs as arguments.

Load the `architecture-guard` skill if the placement isn't obvious.

## 3. Implement

- Smallest working vertical slice first, then the edge cases. A step is not a module list.
- Write the test alongside, not after. For `domain/` code, that includes boundary cases on
  every threshold.
- Any new external dependency gets a port and a fake in the same commit.
- Any threshold, limit or market rule goes to `config/*.yaml`, never a literal in code.
- Keep diffs reviewable. If the step needs >400 changed lines, commit it in parts.
- **Write it for an intermediate developer** — `docs/14-code-style.md`. Plain functions,
  plain types, under 30 lines, comments saying why. If a solution feels sophisticated, it
  is probably the wrong solution. Load `docs/14` if unsure how simple is simple enough.

## 4. Prove it

Run `make check`. Then write `scripts/verify/step_X_Y.py` per `docs/12-verification.md`
and run `make verify STEP=X_Y`. Every check prints value and threshold, never a bare OK.

**A step with no verify script is not finished**, regardless of whether the code works.

## 5. Record it

Three updates, all small:

1. `docs/state/STATE.md` — rewrite in place: active step, what's done, next action.
2. `docs/state/DEVLOG.md` — append one entry: what was built, what surprised you, any
   decision a future session would otherwise re-litigate.
3. `VERIFICATION.md` — append the verify output if the step is complete.

Commit as `[X.Y] <module>: <what changed>`.

## Rules

- Never add a field to `TradeProposal` that expresses size, price or order type.
- Never import outward. If a test needs an exception to the layering rule, the design is
  wrong, not the rule.
- If the step turns out to be bigger than its estimate, say so and propose splitting it.
  Do not silently expand scope.
- If you discover a problem outside this step, use the `issue-log` skill. Do not fix it
  here; drive-by fixes destroy reviewability.
