# 03 — Phased Roadmap

Restructured around one decision: **the research briefing is v1 and ships on its own.**
Execution automation is v2 and begins only after the briefing has demonstrated the
reasoning is worth acting on, per [07-stop-criteria.md](07-stop-criteria.md).

The old plan treated the briefing as a stepping stone to a trading bot. That was backwards.
The briefing is where almost all the learning happens, it costs zero rupees of market risk,
and it's independently useful. Build it, run it for three months, *then* decide whether
automating execution is worth the engineering.

```
v1  RESEARCH BRIEFING          Phases 0–3   ~6 weeks build, 3 months evaluation
    └─ you trade manually, or not at all
v2  PAPER EXECUTION            Phases 4–5   only if Gate 2 passes
v3  LIVE, MICRO CAPITAL        Phase 6      only if Gate 3 passes
v4  SCALE                      Phase 7      cautiously, and maybe never
```

---

# v1 — The Research Briefing System

### Phase 0 — Foundations (2–3 days)
Repo skeleton, `uv` on Python 3.12, ruff/mypy/import-linter/pre-commit, Docker Compose
(Postgres+Timescale+Redis), pydantic-settings, structlog, CI with the layering check.
Plus `config/universe.yaml` and `config/risk_limits.yaml` as real files from day one —
lot sizes and expiry days are **data, not code**, because NSE changes them.
**Exit:** tests and linters green in CI; `import-linter` fails if `domain` imports an adapter.

### Phase 1 — Data spine + journal + the snapshotter (1.5–2 weeks)
**Start the option-chain snapshotter on the first day of this phase**, before any strategy
exists. Every day it doesn't run is a day of IV history you cannot buy back, and both v1
setups gate on IV rank.

- Dhan auth, instrument master, symbol normalisation, historical bar backfill
- Live WS feed with reconnect/backoff/gap detection; tick→bar aggregation
- **Option chain snapshotter** (greeks, IV, OI, spreads) on a schedule
- `universe_service`: rank F&O names by option turnover and OI → top 25, monthly refresh,
  with ban-list and new-listing exclusions
- News adapters normalised to one schema with dedup and entity linking
- **The journal schema and the override log** — tables, Telegram capture prompt, the lot.
  Not deferred. Phase 1 or it never happens.
- Corporate-action handling (splits/bonuses silently corrupt price series and IV history)

**Exit:** 6+ months of bars for the 27-instrument universe; chain snapshots accruing daily
and backed up; feed survives a forced network drop with no gaps; journal accepts a manual
entry end to end.

### Phase 2 — Setup logic + cost model + backtest (2 weeks)
The two setups from [06-v1-scope.md](06-v1-scope.md) as **deterministic, testable gate
functions** — before any LLM touches them. Every gate is a pure function returning a value
and a pass/fail, so the briefing can show you its work and the backtest can replay it.

- Full cost model: brokerage, STT, GST, stamp duty, exchange fees, **bid-ask spread**
- Realised-vol percentile proxy for IV rank, tagged as a proxy wherever used
- Event-driven backtest over whatever history exists; golden-file regression tests
- Hypothetical-P&L scorer: every idea gets graded after full costs whether or not you
  took it

**Exit:** a deliberately bad strategy loses roughly what the cost model predicts; gate
functions are property-tested; backtest and a replayed live run emit identical signals.

### Phase 3 — Agents + the briefing (2–3 weeks)
LangGraph orchestrator and the agent roster from [02-agents.md](02-agents.md), scoped to
exactly the two setups. Telegram delivery, pgvector memory, Langfuse tracing, cost guards,
the eval fixture corpus, and the **calibration tracker** (Brier, reliability bins, baseline
comparison) wired up from the first briefing.

**Exit:** 20 consecutive cycles with zero schema failures and zero unsourced claims; cost
per cycle within budget; no-trade rate in the 40–60% band; every gate value visible in the
briefing so you can audit the reasoning.

### Phase 3.5 — Run it (3 months, the real work)
Daily briefings. You read them, journal your decisions and reasons, and trade manually or
not at all. Weekly prompt and gate iteration driven by the rejected-candidates section and
your override log. Monthly calibration report.

**Exit: Gate 2 in [07-stop-criteria.md](07-stop-criteria.md).** This is a genuine decision
point, not a formality. "Keep the briefing, never automate" is a legitimate and possibly
correct place to stop.

---

# v2 — Paper Execution *(only if Gate 2 passes)*

### Phase 4 — Risk engine + paper broker (1.5 weeks)
The deterministic risk gate, property-tested with hypothesis. Paper broker with a fill
model calibrated against the real spreads Phase 1 recorded. Order state machine,
reconciliation loop, kill switch end to end.
**Exit:** hypothesis cannot construct an input producing a position over your limits.

### Phase 5 — Paper trading (100+ trades, 3–6 months)
Everything live except the money, with the Telegram approval gate on every order.
**Exit:** Gate 3.

---

# v3 — Live, Micro Capital *(only if Gate 3 passes)*

### Phase 6
Smallest viable size. Manual approval stays ON. The goal is measuring the paper-to-live
gap: slippage, rejects, margin surprises, partial fills, feed lag.
**Exit:** live expectancy within 50% of paper over 30+ trades; zero unintended orders;
every reject understood.

### Phase 7 — Scale
Size up in steps. Auto-approve stays off; it becomes discussable only under the narrow
conditions in [06-v1-scope.md](06-v1-scope.md) §6. Add instruments one at a time.

---

## What to do this week

1. Get Dhan API access confirmed and the credentials into `.env`
2. Phase 0 scaffold
3. **Get the chain snapshotter running**, even as a crude cron script, before anything else
   is polished. Six months from now you'll either have the IV history or you won't, and
   nothing else in this project is time-sensitive in that way.
