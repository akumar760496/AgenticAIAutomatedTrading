# 02 — Agent Roster & Delegation

> **Scope guard:** in v1 every agent serves exactly the two setups in
> [06-v1-scope.md](06-v1-scope.md) — Catalyst Continuation on 25 stocks and the Index
> Volatility-Regime Spread on Nifty/Bank Nifty. An agent that can't contribute a value to
> one of those setups' gates doesn't run. The Execution and Position Manager components
> below are v2; they're specified here so v1's contracts don't have to be rewritten later.

## 1. Shape of the system

One **supervisor** fans work out to specialist agents, each of which may delegate to its
own sub-agents. Specialists return *structured evidence*, never prose and never orders.
The supervisor synthesises, then emits at most **two** ranked proposals per cycle — your
stated requirement, and a useful forcing function: it makes the agents argue about which
ideas are best rather than dumping a list.

```
                        ┌─────────────────────────────┐
                        │  CHIEF STRATEGIST (supervisor)│
                        │  LangGraph orchestrator      │
                        │  routes, synthesises, ranks  │
                        └──────────────┬───────────────┘
        ┌──────────────┬───────────────┼───────────────┬──────────────┐
        ▼              ▼               ▼               ▼              ▼
   NEWS &         FUNDAMENTAL      TECHNICAL /      OPTIONS       PORTFOLIO
   SENTIMENT        RESEARCH        QUANT SIGNAL   STRATEGIST      CONTEXT
   agent            agent            agent          agent          agent
     │                │                │               │
     ├ ingestor       ├ filings        ├ trend         ├ IV surface
     ├ deduper        ├ earnings       ├ mean-rev      ├ strategy selector
     ├ entity-linker  ├ corp actions   ├ volume/OI     ├ payoff/greeks
     ├ classifier     ├ peer compare   ├ regime        └ liquidity screen
     └ credibility    └ ownership      └ breadth
        └──────────────┴───────────────┴───────────────┴──────────────┘
                                       ▼
                        ┌─────────────────────────────┐
                        │  RISK NARRATOR (advisory)   │  ← explains, cannot approve
                        └──────────────┬──────────────┘
                                       ▼
                        ╔═════════════════════════════╗
                        ║  RISK GATE — deterministic  ║  ← the only approver
                        ║  pure Python, no LLM        ║
                        ╚══════════════┬══════════════╝
                                       ▼
                        ┌─────────────────────────────┐
                        │  EXECUTION (deterministic)  │
                        │  router, slicer, stop mgr   │
                        └──────────────┬──────────────┘
                                       ▼
                        ┌─────────────────────────────┐
                        │  POSITION MANAGER (fast loop)│ exits, trails, expiry handling
                        └──────────────┬──────────────┘
                                       ▼
                        ┌─────────────────────────────┐
                        │  REVIEWER (post-trade)      │ journal, attribution, prompt fixes
                        └─────────────────────────────┘
```

## 2. The agents

### Chief Strategist (supervisor)
- **Owns:** the research cycle. Decides which specialists to invoke for the current regime,
  resolves contradictions between them, ranks candidates, emits ≤2 `TradeProposal`s.
- **Must:** cite which evidence drove each proposal, state an invalidation condition
  ("exit the thesis if X"), and assign a calibrated confidence.
- **Must not:** invent prices, sizes or margins. Size is a field the risk engine fills in.
- **Must also:** emit every gate's *computed value* for each proposal and each rejected
  candidate, so the briefing can show its work and you can audit the logic rather than
  trust it. A proposal whose gate values aren't all present is dropped.
- **Must be willing to return nothing.** "No ideas today" is a first-class output and the
  target is 40–60% of sessions. The prompt states this explicitly, and the no-trade rate is
  monitored — a supervisor that finds two ideas every day is pleasing you, not analysing.
  If the rate drops below 20% the gates get tightened before any other work proceeds.
- **Output:** `ResearchCycleResult { proposals[≤2], rejected_candidates[], gate_values, market_view, no_trade_reason?, cycle_id }`

### News & Sentiment Agent
Highest-volume, highest-noise job, so it's the most sub-divided.
- `ingestor` — pulls news APIs, NSE/BSE announcement feeds, RSS; normalises to one schema
- `deduper` — content hash + embedding similarity; the same PR arrives from six publishers
- `entity_linker` — maps a headline to actual tradeable instruments (hard: "Bajaj" is three
  listed companies). Maintain a curated alias table; never let the LLM guess a symbol.
- `classifier` (Haiku) — event type: earnings, order win, guidance, regulatory, M&A,
  downgrade, management exit, macro. Cheap model, high volume.
- `credibility_scorer` — source tier, corroboration count, is it an official filing or a
  "sources say" story. Weights everything downstream.
- `impact_analyst` (Opus) — only for items that survive the funnel: expected direction,
  magnitude, durability, whether the market has already priced it in.
- **Guards:** every claim carries a source URL and timestamp. Anything older than the
  session or unattributable is dropped. Explicit prompt rule: *no claim without a source.*

### Fundamental Research Agent
- Sub-agents for filings/annual reports, earnings-call reading, corporate actions
  (splits, bonuses, dividends — these silently break your price series and your options
  positions), peer comparison, promoter/institutional holding changes.
- Scope honestly: for intraday and short-horizon options trades this agent mostly
  provides *disqualifiers* (pledged shares, auditor resignation, governance flags),
  not buy reasons. Treat it as a veto source.

### Technical / Quant Signal Agent (v1: Setup A technical confirm + Setup B regime)
- Sub-agents: trend, mean-reversion, volume/OI behaviour, market regime (trending vs
  choppy vs high-vol), market breadth and index context.
- **Critical constraint:** this agent does not invent indicators at runtime. It selects
  from a library of signals that have each been **backtested and registered** with known
  historical performance. The LLM's job is regime-aware selection and confluence
  reasoning, not discovering alpha in a chat window.
- Returns numbers computed by Python tools, with the tool call recorded.

### Options Strategist Agent (v1: structure selection, IV rank, liquidity screen)
- Sub-agents: IV surface & term structure, IV rank/percentile vs its own history,
  strategy selector (debit/credit spreads, straddles, strangles, calendars, ratios),
  payoff and greeks computation, liquidity screen (OI, spread width, depth).
- Chooses *structure* from directional view + volatility view + event calendar:
  high IV rank into an event → prefer defined-risk credit structures; low IV with a
  catalyst → debit structures. Encode that matrix as code, with the LLM explaining and
  adjusting within it.
- **Hard defaults:** defined-risk only; naked short options disabled by config flag;
  no positions in strikes below a minimum OI; no expiry-day short gamma after 14:30.

### Portfolio Context Agent
Injects what you already hold into every decision. Prevents the classic agentic failure of
proposing the seventh correlated long Nifty-bank bet. Reports current exposure, net delta,
vega, sector concentration, and open-thesis status.

### Risk Narrator (advisory only)
Translates the deterministic gate's verdict into plain English and flags qualitative risks
the rules can't encode (event calendar, liquidity holiday, result-day timing). **It has no
approval authority.** Keeping the explainer and the enforcer separate is deliberate: the
moment an LLM can grant exceptions, your risk limits are decorative.

### Execution (deterministic — not an agent)
Order router, slicing against F&O freeze limits, rate-limit token bucket, retry with
idempotency keys, broker-side stop placement, partial-fill handling, order state machine.
Pure code, heavily unit-tested.

### Position Manager (fast loop, deterministic rules + optional agent review)
Trailing stops, target scaling, time-based exits, expiry-day handling, assignment risk,
stop-loss enforcement if the broker-side stop is rejected. Runs on ticks, not on LLM calls.
An agent may *suggest* an early exit on a news shock; the rules still execute it.

### Reviewer (post-trade, runs nightly)
Compares each closed trade against its original thesis. Was the thesis right and the
execution poor, or vice versa? Produces attribution, updates episodic memory, and flags
systematic agent errors for prompt revision. This is the loop that makes the system
improve rather than just run.

## 3. Contracts between agents

Agents are decoupled by Pydantic models, not by prose. Sketch:

```python
class Evidence(BaseModel):
    source_url: str
    source_tier: SourceTier
    observed_at: datetime          # tz-aware UTC
    claim: str
    supports: Direction            # BULLISH | BEARISH | NEUTRAL
    strength: confloat(ge=0, le=1)

class Thesis(BaseModel):
    instrument: InstrumentRef
    direction: Direction
    horizon: Horizon               # INTRADAY | SWING_3D | SWING_2W
    evidence: list[Evidence]       # min_length=2 — one source is never enough
    confidence: confloat(ge=0, le=1)
    invalidation: str              # the condition that kills this thesis
    agent_id: str
    cycle_id: UUID

class TradeProposal(BaseModel):
    thesis: Thesis
    instrument_leg_plan: list[ProposedLeg]   # equity or option legs, no quantities
    entry_condition: EntryCondition          # price/level/time trigger
    stop_basis: StopBasis                    # ATR mult, structural level, % of premium
    target_basis: TargetBasis
    max_loss_pct_of_equity: confloat(gt=0, le=1.0)   # risk engine may only reduce this
    rank: Literal[1, 2]
```

Note what `TradeProposal` does **not** contain: quantity, lot count, order type or price.
Those are computed downstream. An agent cannot express a position size, so it cannot
express a catastrophic one.

## 4. Orchestration mechanics

- **Cycle triggers:** pre-open (08:45), post-open settle (09:30), midday (12:30),
  pre-close (14:45), plus event-driven wakeups on high-impact news or a position breaching
  a thesis invalidation condition.
- **LangGraph state** is checkpointed per node, so a crashed cycle resumes instead of
  re-paying for every LLM call.
- **Human-in-the-loop breakpoint** before the risk gate for phases 1–5 of the roadmap:
  the graph interrupts, pushes the proposal to Telegram, waits for your approve/reject.
  Auto-approve is a config flag you flip only after months of evidence.
- **Cost guard:** per-cycle and per-day token budgets; exceeding them degrades to
  cheap-model-only and finally to no-new-proposals. An agentic system with a retry loop and
  no budget cap is a billing incident waiting to happen.
- **Timeouts:** every agent node has a wall-clock budget. A slow agent is dropped from the
  cycle with its absence recorded, rather than delaying the cycle.

## 5. Testing agents

LLM components get evaluated, not just unit-tested:
- **Fixture corpus:** ~100 saved real news/market snapshots with your hand-labelled
  expected classification and direction. Run on every prompt change.
- **Schema conformance:** 100% of outputs must parse into the Pydantic contract. Any parse
  failure is a bug, handled by retry-then-abstain, never by regex-patching the JSON.
- **Hallucination checks:** assert every cited URL exists in the input context and every
  number appears in a recorded tool result. An agent citing a figure no tool produced is
  an automatic cycle abort.
- **Adversarial fixtures:** paywalled stubs, contradictory headlines, stale news
  re-published today, pump-and-dump promo text, and a "SELL EVERYTHING" prompt-injection
  string planted in a news body. The last one matters — your agent reads untrusted internet
  text, so treat every news item as hostile input: it is data, never instruction.
## 6. Calibration — the metric that governs sizing

Tracked from the first briefing, reported monthly whether or not it's flattering:
Brier score per agent and per setup, reliability curves per confidence bucket, resolution
(does confidence discriminate at all, or is every call 0.7?), and comparison against a
naive base-rate baseline.

> **Binding rule:** position sizing may not use agent confidence until calibration beats
> the naive baseline over **≥50 resolved ideas**. Until then risk per trade is flat at
> 0.25% for every idea.

Sizing by uncalibrated confidence means betting most on the ideas the model is most
*fluent* about, which is a different thing from the ideas most likely to work. If
0.8-confidence calls land 45% of the time, the number is noise — and a system that sizes by
noise is worse than one that sizes flat, because it concentrates risk precisely where the
model is overconfident. Details in [06-v1-scope.md](06-v1-scope.md) §5.
