# 06 — v1 Scope: The Research Briefing System

**v1 is not a trading bot.** v1 is a market research analyst that writes you a briefing
every day with at most two ranked ideas, which **you** trade manually. Execution
automation is v2 and is explicitly out of scope until v1 has earned it (see
[07-stop-criteria.md](07-stop-criteria.md)).

This is the highest-value version of this project per rupee of risk, and it is useful even
if you never automate a single order.

---

## 1. Universe (hard allowlist, enforced in code)

### Tier 1 — Index
| Instrument | Expiry | Lot size | Notes |
|---|---|---|---|
| NIFTY | **Weekly, Tuesday — moving earlier when that Tuesday is a holiday** + monthly | **65** | The only weekly index expiry on NSE |
| BANKNIFTY | **Monthly only** (last Tuesday, same holiday shift) | **30** | Weekly expiry was withdrawn — design accordingly |

Lot sizes are the post-January-2026-series values (Nifty 75→65, Bank Nifty 35→30). NSE
expiry moved to Tuesday for its F&O from Sept 2025 (BSE/Sensex is Thursday).

> **The Tuesday rule is not a rule you may compute from (ISS-004).** When the Tuesday is a
> trading holiday the expiry moves to the previous trading day. The live list on 2026-10-18
> read Mon 10-19, Tue 10-27, Tue 11-03, Mon 11-09, Tue 11-17, Mon 11-23 — three Tuesdays
> displaced by Dussehra, Diwali Balipratipada and Guru Nanak Jayanti. **Every expiry date is
> read from the exchange and never derived.** `verify 1_6` induces the weekday rule and
> requires it to disagree with the venue, so the day someone "simplifies" this, CI objects.
**Verify all of this against NSE circulars in Phase 1 and load it from
`config/universe.yaml`, never hard-code it** — lot sizes and expiry days have changed twice
in two years and will change again.

### Tier 2 — Single stocks (target: 25 names, max 30)
Stock options on NSE are **monthly expiry only**, which constrains Setup A's holding window.

Starting candidate list — large, consistently liquid F&O names:
`RELIANCE, HDFCBANK, ICICIBANK, SBIN, AXISBANK, KOTAKBANK, INFY, TCS, HCLTECH,
BHARTIARTL, LT, ITC, HINDUNILVR, MARUTI, TATAMOTORS, TATASTEEL, JSWSTEEL, HINDALCO,
BAJFINANCE, SUNPHARMA, M&M, TITAN, ADANIENT, NTPC, COALINDIA`

**This list is a placeholder and must be regenerated, not trusted.** Phase 1 builds a
`universe_service` that ranks F&O-eligible names by 20-day average *option* turnover and
open interest (not equity turnover — they diverge a lot) and emits the top 25. Re-run
monthly.

### Automatic exclusions
- Anything on the NSE **F&O ban list** that day (>95% of market-wide position limit)
- Names added to F&O within the last 6 months — no IV history, so no IV rank (NSE added
  six names in April 2026 and three more in August 2026; these are excluded by rule)
- Anything with fewer than 6 months of our own option-chain snapshots
- The F&O eligibility list is revised quarterly on the quarter-sigma cycle, so the
  universe job must handle additions *and* removals

---

## 2. The two setups

One directional-catalyst setup on stocks, one volatility-regime setup on the index. Both
defined-risk. Nothing else gets briefed, ever — if an idea doesn't fit a setup, it isn't an
idea, it's a hunch.

### Setup A — Catalyst Continuation (single stocks)

> A credible company-specific catalyst that the market has not yet fully priced, confirmed
> by price and volume, expressed as a defined-risk debit vertical.

**All conditions must hold:**

| Gate | Rule |
|---|---|
| Catalyst exists | Exchange filing, or tier-1 source corroborated by ≥2 independents, within the last 2 trading sessions |
| Catalyst type | earnings surprise, guidance change, large order win, regulatory approval/denial, M&A, credit-rating action, management exit |
| Credibility | `credibility_score ≥ 0.7`; "sources say" stories are capped below this and cannot trigger alone |
| Not already priced | Move since catalyst `< 1.5 × ATR(14)`. If it's already run, the edge is gone — this gate kills most ideas, which is correct |
| Technical confirm | Close beyond the 20-day range edge in the catalyst direction, volume `≥ 1.5 ×` 20-day average |
| Volatility | `IV rank < 60` — never buy premium into an already-panicked chain |
| Liquidity | Both legs: OI ≥ 500 lots, bid-ask ≤ 3% of option mid |
| Calendar | 10–25 days to monthly expiry; no scheduled results inside the window unless results *are* the catalyst; not in ban list |
| Fundamental veto | No open governance flag from the Fundamental agent (auditor exit, pledge spike, promoter sell-down) |

**Structure:** debit vertical. Long leg delta 0.50–0.55, short leg ~1 ATR away
(delta 0.25–0.30). Max loss = net debit, capped at 0.25% of equity.
**Exits:** target 60% of spread width · stop at 50% of debit lost · time stop 5 days to
expiry · immediate exit on thesis invalidation.

**Why an agent earns its keep here:** scoring catalyst credibility, judging "is this
already priced in", and sweeping 25 names for disqualifiers every night. That's breadth no
human sustains, and it's the one place the LLM has a real edge.

### Setup B — Index Volatility-Regime Spread (NIFTY weekly / BANKNIFTY monthly)

One setup, two mutually exclusive variants selected by IV rank:

| IV rank | Variant | Structure |
|---|---|---|
| **≥ 70** | B1 — sell elevated vol | Defined-risk credit spread (short vertical **with protective wing**, or iron condor if the directional view is neutral). Short strike delta 0.15–0.20. Nifty: 4–9 DTE. Exit at 50% of credit captured; stop at 2× credit received |
| **35–70** | **No trade** | A valid and frequent output |
| **≤ 35** | B2 — buy cheap vol with trend | Debit vertical in the regime direction, 10–20 DTE |

**Hard gates:** no position held into expiry day — flat by the prior session's close, no
exceptions (expiry-day gamma is how defined-risk positions stop being defined-risk in
practice). No entry if RBI policy, CPI/IIP, an FOMC decision, or the Union Budget falls
inside the window, unless the variant explicitly prices the event. Maximum **one** index
position at a time. Naked short options are disabled at the config level and the flag
stays off through v1 and v2.

**Why an agent earns its keep:** regime classification is genuinely multi-source —
breadth, term structure, macro calendar, FII/DII flow, global cues. That's synthesis work.

### The IV-rank bootstrapping problem

Both setups gate on **IV rank**, which needs ~1 year of per-instrument IV history. **You
don't have it, and it can't be bought back.** Consequences, stated plainly:

1. Phase 1 starts the chain snapshotter on day one, before any strategy exists.
2. Until 6 months of history accumulate, IV rank is computed from a **realised-volatility
   percentile proxy**, and every briefing that used the proxy is tagged as such so you can
   discount it later.
3. If a vendor will sell historical IV/chain data for NSE, buying it is the single
   highest-leverage purchase in this project — it pulls the IV-gated setups forward by six
   months. Worth asking TrueData and similar providers.

---

## 3. The daily briefing (the actual deliverable)

**Schedule:** 08:15 IST pre-open briefing · 15:45 IST post-close review and journal prompt.
Delivered to Telegram, archived to Postgres, rendered as markdown.

**Structure:**
1. **Market view** — regime classification, index IV rank and term structure, the day's
   event calendar, breadth. 5 lines, no filler.
2. **Idea 1 and Idea 2** — each with: instrument and setup label, direction, the exact
   structure (strikes, expiry, debit/credit), *every gate's value* so you can audit the
   logic, the evidence list with source URLs and timestamps, confidence, the invalidation
   condition, and the planned exits.
3. **Rejected candidates** — what nearly qualified and which gate killed it. This section
   is where you'll learn whether the gates are calibrated, so it is not optional.
4. **Open thesis status** — for ideas already live: still valid, or invalidated?
5. **Cost and token spend** for the cycle.

**"No ideas today" is a first-class output.** A system that finds two ideas every single
day is pattern-matching to please you, not analysing. Phase targets: **no-trade days
should be 40–60% of sessions.** If that rate sits below 20%, the gates are too loose and
I'll tighten them before anything else gets built.

---

## 4. The journal (built in Phase 1, not later)

Every briefed idea gets a row whether or not you act on it. This is the dataset the whole
project's credibility rests on, and it only exists if it starts on day one.

```
briefing_ideas       idea_id, cycle_id, date, instrument, setup, direction, structure,
                     all gate values, confidence, invalidation, agent_rationale,
                     used_iv_proxy (bool)
user_decisions       idea_id, decision (TOOK | SKIPPED | MODIFIED), your reason (free text,
                     required), entry fill if taken, size
outcomes             idea_id, resolved_at, exit reason, hypothetical P&L after full costs,
                     actual P&L if taken, max adverse excursion
override_log         every time you disagreed with the system, and what happened next
```

**Your override reasons are the most valuable field in the schema.** They're the raw
material for what the risk rules should encode in v2 — a rule you discovered by overriding
the system twenty times is worth more than one I guessed at up front. The 15:45 Telegram
prompt asks for them while the trade is fresh; a journal filled in weekly is fiction.

Every idea is scored on hypothetical P&L *after full costs* regardless of whether you took
it, so the system is measured on its own calls and can't hide behind your skipping the bad
ones.

---

## 5. Calibration as a first-class metric

Confidence is a number the system must *earn the right to use*.

**Tracked from the first briefing:**
- **Brier score** per agent, per setup, per confidence bucket
- **Reliability curve** — in the 0.7–0.8 confidence bucket, what fraction actually worked?
- Baseline comparison against a naive always-predict-base-rate model. An agent that can't
  beat that baseline is adding nothing but cost.
- **Resolution** — does it discriminate at all, or is every call 0.7?

**The rule this buys, written down now:**

> Position sizing may not use agent confidence until calibration is demonstrated over
> **≥50 resolved ideas** with a Brier score beating the naive baseline. Until then, risk
> per trade is **flat** — every idea gets the same 0.25%.

Most LLM confidence is uncalibrated noise until measured. Sizing by uncalibrated confidence
means betting most on the ideas the model is most fluent about, which is not the same thing
as the ideas most likely to work. The monthly calibration report goes in the briefing
whether or not it's flattering.

---

## 6. The human gate stays on

v1 has no execution path at all — the gate is total by construction. For v2 and beyond:

- Every order is approved by you on Telegram. Auto-approve is a config flag that **stays
  off through v2 and v3**.
- Auto-approve becomes *discussable* only for a single named setup, after ≥100 live
  approved trades of that setup, with calibration demonstrated and live results tracking
  paper. Even then it applies to that one setup, keeps a per-day order cap, and leaves the
  kill switch reachable from your phone.
- Approving a trade on your phone costs you fifteen seconds. An unsupervised agent in a
  retry loop against a rejecting broker API costs considerably more, and it happens at
  2pm on a Tuesday while you're in a meeting.
