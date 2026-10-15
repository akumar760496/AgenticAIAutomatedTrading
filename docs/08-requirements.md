# 08 — Requirements & Feature Specification

What gets built, as numbered requirements with acceptance criteria. This is the contract:
if a feature is not in here, it is not in scope, and if it is in here with a release tag,
it does not ship until its acceptance criterion passes.

**How to read a row.** `FR-x.y` is a stable ID — quote it when you want something changed.
**Release** is the version it ships in (v1 = research briefing, v2 = paper execution,
v3 = live micro-capital). **Priority**: `MUST` = v1 does not ship without it, `SHOULD` =
ships in that version unless time forces a cut, `COULD` = only if everything else is done.

Flow context in [00-system-flow.md](00-system-flow.md) · scope detail in
[06-v1-scope.md](06-v1-scope.md) · what is deliberately excluded in §3 below.

---

## 1. Functional requirements

### FR-1 — Universe & instrument management

| ID | Requirement | Release | Pri | Done when |
|---|---|---|---|---|
| FR-1.1 | The tradeable universe is defined in a version-controlled config file, not in code | v1 | MUST | Changing the universe requires no code change; the file is in git history |
| FR-1.2 | Exchange rules (lot size, expiry day, tick size, F&O eligibility) are config data with an effective-from date | v1 | MUST | The Jan-2026 lot-size change is representable as a dated entry, not an edit |
| FR-1.3 | No instrument outside the universe can appear in a proposal | v1 | MUST | A test feeds a strong off-universe idea to the pipeline; it is rejected |
| FR-1.4 | A `universe_service` ranks F&O stocks by 20-day average **option** turnover and OI and proposes the top 25 | v1 | MUST | Produces a ranked list; the current hand-written list is replaced by its output |
| FR-1.5 | Exclusions are automatic: NSE ban list, F&O entrants <6 months old, names with <6 months of our own chain history | v1 | MUST | Each exclusion reason is logged per excluded name |
| FR-1.6 | Universe refresh runs on the quarterly F&O review cycle and reports the diff for approval | v1 | SHOULD | A diff is delivered; changes apply only after you approve |

### FR-2 — Market data

| ID | Requirement | Release | Pri | Done when |
|---|---|---|---|---|
| FR-2.1 | Subscribe to Dhan websocket for the full universe, respecting the plan's instrument cap | v1 | MUST | Runs a full session without exceeding the cap or dropping a subscription |
| FR-2.2 | Aggregate ticks into 1m / 5m / 1d bars, stored in TimescaleDB | v1 | MUST | Bars reconcile against Dhan historical API within tolerance for 5 sessions |
| FR-2.3 | Automatic reconnect with backoff; a gap is recorded as a gap, never interpolated | v1 | MUST | A forced disconnect produces a logged gap row and a clean resume |
| FR-2.4 | **Option chain snapshotter**: full chain with greeks for all universe instruments, 3× per session, written to Parquet | v1 | MUST | 20 consecutive sessions captured with no missing snapshot |
| FR-2.5 | Store exchange timestamp and local receipt timestamp separately, both tz-aware UTC | v1 | MUST | Schema has both columns; no naive datetime exists anywhere |
| FR-2.6 | Data-freshness watchdog marks any feed stale after a configured threshold | v1 | MUST | Stale state is queryable and blocks idea generation |
| FR-2.7 | Backfill historical daily/intraday bars for the universe for indicator warm-up | v1 | MUST | ATR(14) and 20-day range computable on day one |
| FR-2.8 | Second data source for cross-checking prices and greeks | v2 | COULD | Divergence beyond tolerance raises an alert |

### FR-3 — News & information ingestion

| ID | Requirement | Release | Pri | Done when |
|---|---|---|---|---|
| FR-3.1 | Poll NSE/BSE corporate announcements and filings for universe names | v1 | MUST | A known filing appears in the store within the poll interval |
| FR-3.2 | Ingest news from configured APIs and tier-1 RSS, storing full raw text, URL, publisher, timestamps | v1 | MUST | Raw body is retrievable unmodified for any stored item |
| FR-3.3 | Deduplicate syndicated and reworded copies of the same story | v1 | MUST | A story carried by 5 outlets produces one event with 5 sources |
| FR-3.4 | Link each item to instruments by entity resolution, not substring match | v1 | MUST | "Tata Motors" vs "Tata Steel" vs "TATAMOTORS" resolve correctly in a fixture set |
| FR-3.5 | Classify each item into a catalyst taxonomy (results, guidance, order win, regulatory, rating, M&A, management change, …) | v1 | MUST | Classifier scores ≥90% on a hand-labelled 200-item set |
| FR-3.6 | Score source credibility on a documented tier scheme; the score is explainable | v1 | MUST | Every score carries the rule that produced it |
| FR-3.7 | Economic-events calendar (RBI policy, CPI, IIP, FOMC, Budget) available as structured data | v1 | MUST | Setup B's event-window gate reads it successfully |
| FR-3.8 | Results/earnings calendar per universe name | v1 | MUST | Setup A's expiry-window gate reads it successfully |
| FR-3.9 | News bodies are treated as untrusted input: never interpolated into a prompt as instructions, and tool access is gated | v1 | MUST | An adversarial fixture containing a planted instruction does not alter agent behaviour |

### FR-4 — Agent layer

| ID | Requirement | Release | Pri | Done when |
|---|---|---|---|---|
| FR-4.1 | Supervisor agent (Chief Strategist) orchestrates a research cycle with a single `cycle_id` | v1 | MUST | Every artifact from a run is joinable on `cycle_id` |
| FR-4.2 | Five specialist agents: News & Sentiment, Fundamental, Technical/Quant, Options Strategist, Portfolio Context | v1 | MUST | Each returns a typed result; each is independently testable |
| FR-4.3 | Specialists may delegate to sub-agents; the delegation tree is recorded per cycle | v1 | MUST | The tree is reconstructable from stored traces |
| FR-4.4 | All agent output is schema-validated; a validation failure fails the cycle rather than degrading it | v1 | MUST | A malformed-output fixture produces a failed cycle, never a partial briefing |
| FR-4.5 | Every claim is an `Evidence` object with source, tier, timestamp, direction and strength | v1 | MUST | No thesis can be constructed without them |
| FR-4.6 | A thesis requires ≥2 independent sources | v1 | MUST | Enforced by the type, verified by a test |
| FR-4.7 | Proposals are structurally incapable of carrying quantity, lot count, order type or price | v1 | MUST | The class has no such field; a test asserts this by introspection |
| FR-4.8 | At most 2 ranked proposals per cycle | v1 | MUST | Enforced by type (`rank: Literal[1,2]`) |
| FR-4.9 | "No ideas today" is a first-class result with a stated reason | v1 | MUST | Produces a valid briefing with no proposals |
| FR-4.10 | Every rejected candidate is returned with the gate that killed it | v1 | MUST | Rejections appear in the briefing and the journal |
| FR-4.11 | Model tiering: cheap model for triage/dedup/classification, frontier model for synthesis | v1 | SHOULD | Per-cycle cost stays inside the configured cap |
| FR-4.12 | Cycles are checkpointed per node and resumable after a crash | v1 | SHOULD | A killed process resumes without re-running completed nodes |
| FR-4.13 | Full prompt/response tracing for every LLM call | v1 | MUST | Any briefing line is traceable to the calls that produced it |
| FR-4.14 | Nightly reviewer agent proposes prompt and threshold changes; it cannot apply them | v1 | SHOULD | Suggestions are delivered for your approval only |

### FR-5 — Strategy / setup evaluation

| ID | Requirement | Release | Pri | Done when |
|---|---|---|---|---|
| FR-5.1 | Setup A (Catalyst Continuation, stocks) implemented as individually testable pure gate functions | v1 | MUST | Each of the 9 gates has unit tests incl. boundary cases |
| FR-5.2 | Setup B (Index Volatility-Regime Spread) implemented the same way, including the explicit no-trade band | v1 | MUST | IV rank 35–70 returns no-trade, with a test |
| FR-5.3 | Gates are pure: no network, no clock, no LLM, no I/O | v1 | MUST | `import-linter` forbids outward imports from `domain/` |
| FR-5.4 | Every gate returns its computed value, not just pass/fail | v1 | MUST | The briefing prints values; a boolean-only return fails review |
| FR-5.5 | Thresholds live in config with the file under version control | v1 | MUST | Changing 1.5× ATR to 1.8× needs no code change |
| FR-5.6 | IV rank computed from our own chain history; a documented realised-vol proxy is used until 6 months accrue | v1 | MUST | Proxy-derived values are tagged as such everywhere they appear |
| FR-5.7 | Option structure selection (debit/credit vertical, strikes by delta band, DTE window) | v1 | MUST | Produces a leg plan matching the spec for fixture chains |
| FR-5.8 | Greeks computed locally and cross-checked against Dhan's | v1 | SHOULD | Divergence beyond tolerance is flagged in the briefing |
| FR-5.9 | Naked short options disabled at config level and unreachable in code | v1 | MUST | A test asserts no code path can emit an unhedged short |
| FR-5.10 | A signal registry: only pre-backtested signals may be referenced by agents | v1 | SHOULD | An agent naming an unregistered signal fails validation |
| FR-5.11 | Adding a third setup requires 100 resolved ideas on the first two | v2 | MUST | Written precondition, checked at review |

### FR-6 — Risk & position sizing

| ID | Requirement | Release | Pri | Done when |
|---|---|---|---|---|
| FR-6.1 | Deterministic risk gate, 13 documented checks, the sole approver of any trade | v1 build / v2 enforce | MUST | All 13 implemented with property-based tests |
| FR-6.2 | In v1 the gate runs advisory and reports the size it *would* have taken | v1 | MUST | Every briefing idea carries a hypothetical size |
| FR-6.3 | Risk per trade is flat 0.25% of equity until calibration earns otherwise | v1 | MUST | Sizing code has no confidence input until FR-7.7 passes |
| FR-6.4 | Money is never a float: `Decimal` or integer paise, via `Money`/`Price` value objects | v1 | MUST | A lint/test rejects float arithmetic on monetary fields |
| FR-6.5 | Portfolio-level limits: concentration, heat, net delta, net vega | v1 build / v2 enforce | MUST | Each limit has a test that drives it to breach |
| FR-6.6 | Daily loss limit, consecutive-losing-day halt, soft and hard drawdown halts — enforced in code | v2 | MUST | A halt cannot be cleared from inside the app; it needs explicit operator action |
| FR-6.7 | No LLM may grant a risk exception, ever | all | MUST | No code path lets agent output override a gate result |
| FR-6.8 | Margin check against live broker margin before approval | v2 | MUST | An under-margin proposal is rejected, not attempted |
| FR-6.9 | Risk Narrator explains gate decisions in prose and has no authority | v1 | SHOULD | Its output is display-only, asserted by a test |

### FR-7 — Briefing, journal & evaluation

| ID | Requirement | Release | Pri | Done when |
|---|---|---|---|---|
| FR-7.1 | Pre-open briefing at 08:15 IST and post-close review at 15:45 IST | v1 | MUST | 20 consecutive sessions delivered on time |
| FR-7.2 | Briefing has 5 sections: market view, ≤2 ideas with all gate values, rejects with killing gate, open-thesis status, cost | v1 | MUST | Matches the spec in 06 §3 |
| FR-7.3 | Delivered to Telegram, readable on a phone without scrolling past the decision | v1 | MUST | You can act on it from the message alone |
| FR-7.4 | Capture your decision per idea — took / skipped / modified — **with a free-text reason** | v1 | MUST | Captured for every idea in every briefing for 20 sessions |
| FR-7.5 | Journal tables (`briefing_ideas`, `user_decisions`, `outcomes`, `override_log`, `calibration_snapshots`) exist from Phase 1 | v1 | MUST | Migrations shipped before the first briefing |
| FR-7.6 | Every idea is scored on hypothetical P&L after full costs, whether or not you took it | v1 | MUST | Scores exist for skipped ideas too |
| FR-7.7 | Calibration: Brier score, reliability curve, resolution, against a naive base-rate baseline | v1 | MUST | Computed on a rolling window and reported monthly |
| FR-7.8 | Confidence-based sizing is blocked until calibration beats baseline over ≥50 resolved ideas | v1 | MUST | Enforced in code, not policy |
| FR-7.9 | No-trade-rate monitor with a 40–60% target band and an alert outside it | v1 | MUST | Alert fires on a synthetic run below 20% |
| FR-7.10 | Briefing-engagement metric (did you read and act on it) | v1 | SHOULD | Feeds Gate 2's ">70% ignored" criterion |
| FR-7.11 | Monthly performance and attribution report | v1 | SHOULD | Delivered on the first Sunday |
| FR-7.12 | Per-cycle cost accounting in tokens and rupees | v1 | MUST | Appears in every briefing |

### FR-8 — Execution & position management

| ID | Requirement | Release | Pri | Done when |
|---|---|---|---|---|
| FR-8.1 | Entry-condition watcher on the fast loop; no LLM call in the order path | v2 | MUST | Latency budget met with LLM services stopped |
| FR-8.2 | Human approval via Telegram with inline buttons and an expiry timeout | v2 | MUST | No reply ⇒ no order, verified by test |
| FR-8.3 | `auto_approve` exists as a config flag, defaults off, stays off through v2 and v3 | v2 | MUST | Default asserted by test; enabling it requires written preconditions |
| FR-8.4 | Order router with per-leg idempotency keys; a retry can never double-send | v2 | MUST | Duplicate-submission test sends one order |
| FR-8.5 | Order slicing against F&O freeze limits | v2 | MUST | An oversized order is sliced correctly for fixture limits |
| FR-8.6 | Multi-leg structures submitted so that a partial fill leaves no naked short | v2 | MUST | Partial-fill simulation leaves a hedged or flat book |
| FR-8.7 | Append-only audit ledger for every order, modification and cancel | v2 | MUST | Ledger rows are immutable; no update/delete path exists |
| FR-8.8 | Position manager: stops, targets, time stops, thesis-invalidation exits | v2 | MUST | Each exit type fires in simulation |
| FR-8.9 | Expiry-eve flatten for index positions | v2 | MUST | No position is ever held into expiry day in simulation |
| FR-8.10 | Reconcile broker positions against internal state every cycle; divergence halts trading | v2 | MUST | An injected divergence triggers a halt |
| FR-8.11 | Live/paper divergence monitor | v3 | MUST | Divergence beyond threshold halts and reports |

### FR-9 — Control plane & safety

| ID | Requirement | Release | Pri | Done when |
|---|---|---|---|---|
| FR-9.1 | Kill switch as a single Redis key, checked before every order and on a 1s heartbeat | v2 | MUST | Setting it cancels open orders and blocks entries within 2s |
| FR-9.2 | Kill switch settable from your phone when the rest of the system is unhealthy | v2 | MUST | Demonstrated with the engine deliberately wedged |
| FR-9.3 | Flatten-all command | v2 | MUST | Closes every position and reports what it did |
| FR-9.4 | Fail-closed: stale data, disconnect, unhandled exception, broker error ⇒ no new positions, existing stops stay live | v1 build / v2 enforce | MUST | Each of the four conditions has a test |
| FR-9.5 | Status command: positions, P&L, feed health, halt state, last cycle | v1 | SHOULD | Answers from the phone in one message |
| FR-9.6 | Alerting on cycle failure, feed loss, schema failure, cost-cap breach | v1 | MUST | Each fires in a fault-injection test |
| FR-9.7 | Three schema or hallucination failures in a month halt the project for review | v1 | MUST | Counter exists and triggers |

### FR-10 — Backtesting & simulation

| ID | Requirement | Release | Pri | Done when |
|---|---|---|---|---|
| FR-10.1 | One engine, three modes (backtest / paper / live), differing only by injected adapters | v1 | MUST | Mode is a wiring choice; no `if mode ==` in domain code |
| FR-10.2 | Gate functions backtestable against historical bars and chain snapshots | v1 | SHOULD | Produces a trade list over available history |
| FR-10.3 | Cost model: brokerage, STT, stamp duty, exchange fees, GST, plus slippage | v1 | MUST | Reconciles against a real Dhan contract note |
| FR-10.4 | Point-in-time correctness: a backtest cannot see data published after the decision timestamp | v1 | MUST | A lookahead-detection test fails on injected future data |
| FR-10.5 | Paper trading against live data with simulated fills | v2 | MUST | 100 trades across a regime change |
| FR-10.6 | Walk-forward evaluation rather than single-period fitting | v2 | SHOULD | Reports per-window results |

### FR-11 — Observability, security, operations

| ID | Requirement | Release | Pri | Done when |
|---|---|---|---|---|
| FR-11.1 | Structured JSON logging with `cycle_id`/`order_id` correlation throughout | v1 | MUST | Any decision is reconstructable from logs alone |
| FR-11.2 | LLM observability: prompts, responses, latency, tokens, cost per node | v1 | MUST | Per-cycle cost and latency queryable |
| FR-11.3 | Metrics and dashboards for feed health, cycle duration, gate pass rates, cost | v1 | SHOULD | Dashboard exists and is checked at the monthly review |
| FR-11.4 | Monthly spend cap on LLM usage, enforced by refusing calls rather than alerting after | v1 | MUST | Cap breach stops cycles and notifies |
| FR-11.5 | Secrets only from environment/secret store; never in git; never echoed in logs or briefings | v1 | MUST | Secret-scanning in pre-commit; log redaction tested |
| FR-11.6 | Reproducible deployment via Docker Compose; one command brings the stack up | v1 | SHOULD | Clean-machine bring-up documented and tested |
| FR-11.7 | Nightly backup of Postgres and the Parquet chain archive, restore-tested | v1 | MUST | A restore drill succeeds — the chain history is irreplaceable |
| FR-11.8 | Static IP registered with the broker, order tagging, auditable client→algo→IP→key chain | v2 | MUST | Confirmed in writing with Dhan before the first live order |
| FR-11.9 | Time synchronisation (NTP) with drift monitoring | v2 | SHOULD | Drift beyond tolerance alerts |

### FR-12 — Code quality (non-negotiable, your point 8)

| ID | Requirement | Release | Pri | Done when |
|---|---|---|---|---|
| FR-12.1 | Hexagonal layering with an inward-only dependency rule, enforced mechanically in CI | v1 | MUST | `import-linter` contract passes and fails on a deliberate violation |
| FR-12.2 | `mypy --strict` clean; `ruff` clean; pre-commit hooks installed | v1 | MUST | CI blocks merge on any failure |
| FR-12.3 | Domain objects frozen and immutable | v1 | MUST | Mutation attempt raises, asserted by test |
| FR-12.4 | All timestamps tz-aware UTC internally; IST only at the edges | v1 | MUST | A naive-datetime test fails the build |
| FR-12.5 | Property-based tests on the risk gate and all sizing arithmetic | v1 | MUST | Hypothesis suite present and green |
| FR-12.6 | Adversarial test fixtures: prompt injection, malformed agent output, stale data, broker errors | v1 | MUST | All four classes present in the suite |
| FR-12.7 | Every port has a fake adapter so tests never touch network or broker | v1 | MUST | Full suite runs offline |

---

### FR-13 to FR-15 — Interfaces, order placement, order tracking

Specified in full, with the order lifecycle state machine and the failure-case table, in
[11-interfaces-and-orders.md](11-interfaces-and-orders.md) §6. Summary: read-only broker
sync so the journal records real fills rather than stated intentions (FR-13, v1 MUST), the
order lifecycle state machine with idempotency and reconciliation (FR-14, v2), and the
read-only web dashboard (FR-15, v1.5).

---

## 2. Non-functional requirements

| ID | Requirement | Target |
|---|---|---|
| NFR-1 | Research cycle completes well inside the pre-open window | ≤20 min from trigger to Telegram |
| NFR-2 | Order path latency, decision to broker (v2) | ≤250 ms p99, with no LLM in path |
| NFR-3 | Monthly running cost, v1 | within a cap you set; reported every cycle |
| NFR-4 | Briefing delivery reliability | ≥95% of sessions on time over a quarter |
| NFR-5 | Data capture reliability (chain snapshots) | ≥99% of scheduled snapshots |
| NFR-6 | Recovery | full stack restored from backup in ≤1 hour, drill-verified |
| NFR-7 | Single-operator maintainability | ≤5 hrs/week steady state; more means scope was wrong |
| NFR-8 | Auditability | any order or recommendation explainable from stored artifacts years later |

---

## 3. Explicitly out of scope

Stated so that scope creep has to argue with a document. Scope creep is the most likely
failure mode of this project — in v1 there is no market risk, so there is little else.

1. HFT, latency arbitrage, market making.
2. Intraday scalping and anything needing sub-second decisions.
3. Naked short options — through v2 and v3, disabled in config and unreachable in code.
4. Multi-broker execution (a second broker is for *data* redundancy only).
5. Managing anyone else's money, or publishing recommendations — that is regulated territory.
6. Training or fine-tuning models. We use hosted models and measure them.
7. Equity delivery/long-term investing, crypto, commodities, currency, US markets.
8. A *control* UI — any screen with a button that can place, modify or cancel an order.
   Approvals stay on Telegram. A **read-only** dashboard is in scope from v1.5 (FR-15,
   specified in [11-interfaces-and-orders.md](11-interfaces-and-orders.md)).
9. More than two setups, until the first two have 100 resolved ideas.
10. Fully autonomous trading with no human in the loop — not in any planned version.

---

## 4. Release cut

| Version | Feature set | Gate to the next |
|---|---|---|
| **v1** | FR-1, FR-2, FR-3, FR-4, FR-5, FR-7, FR-12 in full · FR-6 built and advisory · FR-9/FR-10/FR-11 partial | 60 briefing sessions pass Gate 2 of [07-stop-criteria.md](07-stop-criteria.md) |
| **v2** | FR-6 enforcing · FR-8 paper · FR-9 full · FR-10.5 · FR-11.8 | 100 paper trades across a regime change pass Gate 3 |
| **v3** | Live execution at micro size, FR-8.11, code-enforced drawdown halts | Gate 4 — reviewed monthly, hard review quarterly |

---

## 5. Traceability to your original ten points

| Your point | Requirements that satisfy it |
|---|---|
| 1 — Real-time news + real research before deciding | FR-3.1–3.9, FR-4.5, FR-4.6 |
| 2 — Analyse live data for options **and** stocks | FR-2.1–2.7, FR-5.1 (stocks), FR-5.2 (index options), FR-5.7 |
| 3 — Market research analyst recommending the top 2 | FR-4.1, FR-4.8, FR-7.1–7.3 |
| 4 — Main agents propagating to sub-agents | FR-4.2, FR-4.3 |
| 5 — Right tools and tech stack | [01-architecture.md §7](01-architecture.md), FR-11 |
| 6 — Python | FR-12 (the whole section presumes it) |
| 7 — Use the Dhan account; tell me what you need | FR-2.1, FR-8.4, [04-inputs-needed.md](04-inputs-needed.md) |
| 8 — Clean architecture, modular, naming | FR-12.1–12.7 |
| 9 — Architectural plan before development | This doc + 00, 01, 02, 03 |
| 10 — Honest suggestions | [05-realities.md](05-realities.md), [07-stop-criteria.md](07-stop-criteria.md), §3 above |

---

## 6. Requirements that need your decision before Phase 1

These are blanks in the spec, not open design questions. Each one blocks a `MUST`.

1. **FR-2.1** — your Dhan plan's websocket instrument cap. If it is below ~30, FR-1.4 must
   shrink the universe to fit, and that changes Setup A's breadth.
2. **FR-5.5** — do you accept my threshold values (1.5× ATR, IV rank 60/70/35, credibility
   0.7)? They are analyst judgment, not fact. Push back now, before they are in code.
3. **FR-6.3 / FR-7.8** — confirm flat 0.25% risk per trade as the v1 and v2 number.
4. **FR-7.4** — how you want to record decisions. Telegram reply is my assumption; if you
   won't actually do that at 09:20, this `MUST` fails silently and the journal is empty.
5. **FR-11.4** — your monthly LLM spend cap, as a rupee figure.
6. **NFR-7** — your realistic weekly hours. Under 5, cut v1 to index-only with one setup.
7. **FR-1.2** — whether historical option-chain/IV data is purchasable from your vendors.
   A yes removes FR-5.6's six-month proxy period entirely.
