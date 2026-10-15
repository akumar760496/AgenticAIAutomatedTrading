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
