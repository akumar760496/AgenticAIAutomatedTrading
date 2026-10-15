# 01 — Architecture

## 1. Design principles

1. **The LLM is a researcher, not a trader.** Agents emit `TradeProposal` objects.
   A deterministic risk engine validates them. A deterministic router executes them.
   No LLM output is ever passed to a broker API as an instruction.
2. **LLMs do not do arithmetic.** Greeks, position sizing, P&L, indicators, payoffs are
   computed by tested Python functions exposed to agents as tools. The LLM chooses *which*
   tool and interprets the result.
3. **Hexagonal (ports & adapters).** The domain core knows nothing about Dhan, Postgres,
   Anthropic or websockets. Everything external sits behind a Protocol.
4. **One engine, three modes.** Backtest, paper and live differ only by which adapters are
   injected. If a strategy can't be backtested by the same code path that trades it live,
   the backtest is a lie.
5. **Two clocks.** A *slow loop* (research, LLM, minutes-to-hours) and a *fast loop*
   (ticks, signals, risk, execution, milliseconds). They communicate only through a
   durable store, never by blocking calls. An LLM call must never sit in the path of an
   order.
6. **Everything is auditable.** Every proposal, veto, fill and agent rationale is appended
   to an immutable ledger. SEBI traceability needs it; so will you, when you want to know
   why the bot bought something.
7. **Exchange rules are data, not code.** Lot sizes, expiry days, F&O eligibility and
   ban lists live in `config/universe.yaml` and a nightly refresh job. NSE cut index lot
   sizes with the Jan 2026 series and withdrew Bank Nifty weeklies; anything hard-coded
   will be wrong within a year.
8. **Fail closed.** Any ambiguity, stale data, disconnect, or unhandled exception results
   in *no new positions*, never in a guessed order.

## 2. Layering

```
┌───────────────────────────────────────────────────────────────────┐
│  interfaces/        CLI, FastAPI control plane, Telegram alerts   │
├───────────────────────────────────────────────────────────────────┤
│  agents/            LangGraph graphs, prompts, agent tools        │
├───────────────────────────────────────────────────────────────────┤
│  application/       Use cases: RunResearchCycle, EvaluateProposal,│
│                     ExecuteApprovedTrade, ManageOpenPositions     │
├───────────────────────────────────────────────────────────────────┤
│  domain/            Pure Python. Entities, value objects, risk    │
│                     rules, sizing, greeks, strategy primitives.   │
│                     ZERO third-party I/O imports.                 │
├───────────────────────────────────────────────────────────────────┤
│  ports/             Protocols: BrokerPort, MarketDataPort,        │
│                     NewsPort, LLMPort, ClockPort, RepositoryPort  │
├───────────────────────────────────────────────────────────────────┤
│  adapters/          dhan/, news/, llm/, persistence/, backtest/   │
├───────────────────────────────────────────────────────────────────┤
│  infra/             config, logging, telemetry, scheduling, DI    │
└───────────────────────────────────────────────────────────────────┘
```

**Dependency rule:** arrows point inward only. `domain` imports nothing from the layers
above it. Enforce it mechanically with `import-linter` in CI — this is the one rule that
decays silently if unpoliced.

## 3. Runtime topology

For the end-to-end flow — ingest through briefing through journal, and where the
order path attaches in v2 — see [00-system-flow.md](00-system-flow.md). This section
covers process boundaries only.

> **In v1, only the market-data, research and control-plane processes exist.** The trading
> engine and the Dhan order path are built in v2 (Phase 4). The risk gate is still
> specified and tested in v1 — the briefing reports the size it *would* have taken — so
> that v2 is wiring, not design. Everything below is the full picture; v1 is a subset.

Four long-lived processes, communicating over Redis streams. Separate processes because a
hung LLM call or a news-API timeout must not stall the tick handler.

```
                      ┌──────────────────────────┐
  Dhan WS feed ──────▶│  market-data service     │──▶ Timescale (bars, ticks)
  (20 instr. cap)     │  tick → bar aggregation  │──▶ Redis: md.ticks
                      └──────────────────────────┘
                                   │
  News APIs, NSE/BSE  ┌──────────────────────────┐
  filings, RSS ──────▶│  research service (slow) │──▶ Postgres: proposals
                      │  LangGraph agent graphs  │──▶ pgvector: memory
                      └──────────────────────────┘
                                   │ proposals
                      ┌──────────────────────────┐
                      │  trading engine (fast)   │
                      │  signals → RISK GATE     │──▶ Audit ledger (append-only)
                      │  → order router          │──▶ Dhan REST (orders)
                      └──────────────────────────┘
                                   │
                      ┌──────────────────────────┐
                      │  control plane           │ FastAPI + Telegram:
                      │  approvals, KILL SWITCH  │ status, approve, flatten-all
                      └──────────────────────────┘
```

The **kill switch** is a single Redis key checked by the engine before every order and on
a 1-second heartbeat. Setting it cancels all open orders and blocks new entries. It must be
settable from your phone without the rest of the system being healthy.

## 4. The risk gate (deterministic, non-negotiable)

Every proposal passes a hard-coded, property-tested chain. Any single failure = veto.
No LLM involvement, no overrides at runtime.

| Check | Example rule |
|---|---|
| Market state | Within session hours; not in the first/last 5 min; not expiry-day after 14:30 for short options |
| Data freshness | Last tick < 3s old; option chain < 30s old; else veto |
| Instrument allowlist | Symbol is in the approved universe (liquid F&O names + index options only) |
| Per-trade risk | Max loss ≤ R% of equity (R starts at 0.25%) |
| Position sizing | Volatility-adjusted (ATR or IV-based); never a fixed lot count |
| Concentration | Max exposure per symbol, per sector, max correlated gross |
| Portfolio risk | Max portfolio heat, max net delta, max short vega, max gross notional |
| Daily limits | Daily loss limit, max trades/day, max consecutive losses → auto-halt |
| Options-specific | No naked short options unless explicitly enabled; min OI/spread liquidity; defined-risk structures preferred |
| Margin | Pre-trade margin check against live Dhan funds, with buffer |
| Duplicate / churn | No re-entry in same symbol within N minutes; idempotency key per proposal |
| Circuit breakers | Three rejects, a feed disconnect, or a reconciliation mismatch → halt |

A proposal that passes becomes an `ApprovedOrder` with a frozen size and a mandatory
pre-registered stop. Stops are placed as broker-side orders, not held in memory — your
process will crash one day.

## 5. Repo layout

```
src/trading_desk/                        # the package. Imports point inward only.
  domain/
    instruments/     symbol.py  contract.py  option.py  universe.py
    market/          bar.py  tick.py  quote.py  chain.py  regime.py
    signals/         indicator.py  signal.py  confidence.py
    options/         greeks.py  iv.py  payoff.py  strategies/ (spreads, straddles…)
    portfolio/       position.py  holding.py  exposure.py  pnl.py
    risk/            rules/  sizing.py  limits.py  gate.py
    orders/          order.py  proposal.py  approved_order.py  fill.py  state_machine.py
    research/        thesis.py  evidence.py  sentiment.py  catalyst.py
  ports/             broker.py  market_data.py  news.py  llm.py  clock.py  repository.py
  application/
    use_cases/       run_research_cycle.py  evaluate_proposal.py
                     execute_approved_trade.py  manage_positions.py
                     reconcile_state.py  run_backtest.py
    services/        feature_service.py  universe_service.py  journal_service.py
  agents/
    graph/           orchestrator.py  state.py  routing.py
    roster/          news_agent.py  fundamental_agent.py  technical_agent.py
                     options_agent.py  risk_narrator.py  reviewer.py
    tools/           market_tools.py  option_tools.py  research_tools.py  math_tools.py
    prompts/         *.md              # versioned, diffable, never inline strings
    memory/          vector_store.py  episodic.py
  adapters/
    broker/dhan/     client.py  auth.py  orders.py  feed_ws.py  mapping.py  rate_limit.py
    broker/paper/    simulated_broker.py  slippage.py  fill_model.py
    market_data/     dhan_md.py  historical.py  cache.py
    news/            drishti.py  marketaux.py  nse_announcements.py  rss.py  normalizer.py
    llm/             anthropic_client.py  router.py  cost_guard.py
    persistence/     timescale/  postgres/  redis/  ledger.py  migrations/
    backtest/        engine.py  clock.py  data_replay.py  cost_model.py
  infra/             settings.py  container.py  logging.py  telemetry.py  scheduler.py
  interfaces/
    cli/             main.py           # typer: backtest, paper, live, status, flatten
    api/             control_plane.py  # FastAPI: health, approve, kill
    notify/          telegram.py

tests/
  unit/              domain tests — fast, no I/O, the bulk of the suite
  property/          hypothesis tests on sizing, risk limits, greeks, state machine
  integration/       adapters against recorded fixtures / Dhan sandbox
  backtest/          golden-file regression on strategy outputs
  agents/            prompt evals: fixed news fixtures → expected proposal shape
notebooks/           research only; never imported by src
config/              base.yaml  paper.yaml  live.yaml  universe.yaml  risk_limits.yaml
```

## 6. Naming conventions

- Packages/modules `snake_case`, singular for a concept (`risk/`), plural for collections
  (`instruments/`).
- Classes `PascalCase`; domain entities are nouns (`TradeProposal`), use cases are verbs
  (`EvaluateProposal`), ports end in `Port`, adapters are named by technology
  (`DhanBroker`, `PaperBroker`).
- Booleans read as assertions: `is_tradeable`, `has_open_position`, `should_exit`.
- Money is never a float. Use `Decimal` or integer paise throughout; define `Money` and
  `Price` value objects in week one and never pass bare numbers across a boundary.
- All timestamps are timezone-aware UTC internally; convert to `Asia/Kolkata` only at the
  edges. Store the exchange timestamp *and* the receipt timestamp separately.
- Every domain object is a frozen Pydantic model or frozen dataclass. Mutation happens by
  producing a new object, which makes the audit ledger trivial.

## 7. Tech stack

| Concern | Choice | Why |
|---|---|---|
| Language | **Python 3.12** | Not 3.14 — several quant/data wheels still lag. Pin it. |
| Packaging | **uv** + `pyproject.toml` | Already on your machine; fastest resolver, lockfile |
| Contracts | **Pydantic v2** + `pydantic-settings` | Validation at every boundary, typed config |
| Concurrency | **asyncio** | Websocket feed + REST + LLM calls are all I/O-bound |
| Agent orchestration | **LangGraph** | Typed state, checkpointing, durable resume, native human-in-the-loop breakpoints — the approval gate is a first-class concept, not bolted on |
| LLM | **Claude** — `claude-opus-5` for thesis synthesis, `claude-haiku-5-5` for triage/classification/dedup | Tiered routing keeps cost sane; most news items only need a cheap classifier |
| LLM observability | **Langfuse** (self-host) or LangSmith | You must be able to replay why an agent said what |
| Broker | **DhanHQ v2** Python SDK | Your account. REST orders + WS market feed + WS order updates |
| Timeseries | **Postgres + TimescaleDB** | Bars, ticks, features; hypertables + continuous aggregates |
| Vector memory | **pgvector** in the same Postgres | One database beats two; your corpus is small |
| Hot state / bus | **Redis** (streams + keys) | Tick fan-out, kill switch, idempotency locks, rate-limit tokens |
| Research store | **Parquet + DuckDB** | Fast local analytics over historical bars without hammering Postgres |
| Numerics | polars (ETL), pandas (interop), numpy, TA-Lib or `pandas-ta` | polars for speed, pandas where libs demand it |
| Options math | `py_vollib` / `mibian`, cross-checked against Dhan chain greeks | Always have your own greeks to validate the vendor's |
| Backtesting | **vectorbt** for sweeps → **nautilus_trader** for event-driven validation | Vectorised research is fast but optimistic; re-validate the finalists in an event-driven engine with the same code path as live |
| Scheduling | **APScheduler** in-process; **Prefect** if research jobs grow | Don't reach for Airflow |
| Observability | `structlog` (JSON), OpenTelemetry, Prometheus + Grafana | One dashboard: P&L, heat, latency, feed lag, LLM cost, veto counts |
| Testing | pytest, pytest-asyncio, **hypothesis**, freezegun, respx | Property tests on the risk engine are worth more than any other test here |
| Quality | ruff, mypy `--strict` on domain+application, **import-linter**, pre-commit | Enforce the layering rule in CI |
| Secrets | `.env` locally (gitignored), SOPS/age or AWS Secrets Manager in prod | Never a token in git, ever |
| Deploy | Docker Compose on a single **AWS ap-south-1 (Mumbai)** VM with an **Elastic IP** | Mumbai for latency to NSE; Elastic IP satisfies SEBI's static-IP mandate |

### Why not CrewAI
Faster to prototype, but its state handling and error granularity are thin for something
that touches money. LangGraph costs more lines and buys you resumability and real approval
breakpoints. Build it once, in LangGraph.

## 8. Data model essentials

- `bars` — hypertable (instrument_id, tf, ts, o,h,l,c,v, oi), compressed after 7 days
- `option_chain_snapshots` — periodic chain captures with greeks and IV; your IV surface
  history is a genuine edge and nobody sells it to you cheaply, so start recording it on
  day one even before any strategy exists
- `news_items` — raw, normalised, hashed for dedup, entity-linked to instruments, embedded
- `theses` — agent output: claim, evidence refs, confidence, horizon, invalidation condition
- `proposals` / `risk_decisions` / `orders` / `fills` / `positions`
- `audit_ledger` — append-only, hash-chained; every state transition with actor and inputs

**The v1 evaluation tables — built in Phase 1, not deferred** (full schema in
[06-v1-scope.md](06-v1-scope.md) §4):

- `briefing_ideas` — every idea ever briefed, with *every gate's computed value*, the
  agent rationale, confidence, and an `used_iv_proxy` flag
- `user_decisions` — TOOK / SKIPPED / MODIFIED plus **a required free-text reason**
- `outcomes` — hypothetical P&L after full costs for *every* idea regardless of whether
  you took it, plus actual P&L and max adverse excursion where taken
- `override_log` — every disagreement between you and the system, and what followed
- `calibration_snapshots` — rolling Brier score, reliability bins, baseline comparison,
  per agent and per setup

These five tables are the project's evidence base. They are the difference between
"I think this works" and knowing. They cannot be backfilled, so they ship in Phase 1.

Reconciliation job on every startup and every 60s: broker positions and orders are the
source of truth; local state that disagrees triggers a halt, never a silent correction.
