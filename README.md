# Agentic AI Automated Trading

An agentic market-research and (eventually) execution system for Indian equities and
options, using Dhan as the execution broker.

**Status: planning / pre-implementation.** No code exists yet.

## v1 is a research analyst, not a trading bot

v1 delivers a **daily briefing with at most two ranked ideas** to Telegram, which you trade
manually — or don't. It carries zero market risk, teaches you whether the reasoning is any
good, and is useful even if execution is never automated. Execution automation (v2) starts
only after v1 has earned it against written-down criteria.

```
v1  Research briefing      → you trade manually        ~6 wk build + 3 mo evaluation
v2  Paper execution        → only if v1 proves out
v3  Live, micro capital    → only if paper proves out
```

| Doc | What it covers |
|---|---|
| **[docs/00-system-flow.md](docs/00-system-flow.md)** | **Start here — the complete flow end to end, v1 and v2, in one page** |
| [docs/01-architecture.md](docs/01-architecture.md) | Layering rules, runtime topology, repo layout, tech stack |
| [docs/02-agents.md](docs/02-agents.md) | Agent roster, delegation, contracts, how agents are evaluated |
| [docs/03-roadmap.md](docs/03-roadmap.md) | Phased plan with exit criteria per phase |
| [docs/04-inputs-needed.md](docs/04-inputs-needed.md) | What you need to provide, and when |
| [docs/05-realities.md](docs/05-realities.md) | Regulatory, financial and engineering constraints |
| **[docs/06-v1-scope.md](docs/06-v1-scope.md)** | **The universe, the two setups, the briefing, the journal, calibration** |
| **[docs/07-stop-criteria.md](docs/07-stop-criteria.md)** | **When to stop — written down before any money is at stake** |
| **[docs/09-features-plain.md](docs/09-features-plain.md)** | **The features in plain English — start here if 08 is too dense** |
| **[docs/10-build-plan.md](docs/10-build-plan.md)** | **Step-by-step build plan: every task, dependency, deliverable and acceptance check** |
| **[docs/11-interfaces-and-orders.md](docs/11-interfaces-and-orders.md)** | **Telegram, dashboard and CLI · how orders are placed, tracked and reconciled** |
| **[docs/12-verification.md](docs/12-verification.md)** | **How to test and verify each build step, and what "passing but broken" looks like** |
| **[docs/13-dev-workflow.md](docs/13-dev-workflow.md)** | **Agent operating rules, skills, state tracking, token discipline** |
| **[docs/14-code-style.md](docs/14-code-style.md)** | **How the code is written: simple, readable, intermediate-level, with examples** |
| **[docs/08-requirements.md](docs/08-requirements.md)** | **Numbered requirements & feature list with acceptance criteria, release cut, out-of-scope** |

## Six commitments this plan is built on

1. **Narrow scope.** Nifty + Bank Nifty index options and 25 liquid F&O stocks. Exactly two
   setups. Breadth is where agentic projects die.
2. **Research before trading.** The briefing ships first and stands alone.
3. **The human gate stays on** well past the point it feels necessary. Auto-approve is a
   flag that stays off through v2 and v3.
4. **The journal starts day one.** Your own override decisions are the training data for
   what the risk rules should encode in v2.
5. **Calibration is a first-class metric.** Sizing may not use agent confidence until it
   beats a naive baseline over ≥50 resolved ideas. Until then, flat risk per trade.
6. **Stop criteria are written down now**, with dates and numbers, while nobody is
   attached to the outcome.

## The rule that shapes the architecture

An LLM never places an order. Agents emit **proposals** that structurally cannot contain a
position size; a deterministic, property-tested risk engine sizes and approves them; a
deterministic router executes. Every layer exists to protect that boundary.

## Working on this repo

`CLAUDE.md` holds the rules that always apply. `.claude/skills/` holds the procedures for
recurring tasks. `docs/state/` holds where the project actually is — `STATE.md` (now),
`DEVLOG.md` (why, current phase — earlier phases in `DEVLOG-ARCHIVE.md`), `ISSUES.md` (what's
broken, open only — closed ones in `ISSUES-ARCHIVE.md`). Start a session with the `session-start`
skill; see [docs/13-dev-workflow.md](docs/13-dev-workflow.md).
