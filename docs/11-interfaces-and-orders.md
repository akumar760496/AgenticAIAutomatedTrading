# 11 — Interfaces, Order Placement & Order Tracking

Your question exposed a genuine gap, not just a missing document. **In v1 as planned, the
system tells you what to trade but never finds out what you actually did.** You reply
"took it" on Telegram and it believes you — your fill price, your partial fill, your
early exit are all invisible, so every outcome it scores is a polite fiction. §1 fixes
that. The rest of the doc covers how orders are placed and tracked once v2 exists, and
which screens exist for each.

Three interfaces, with a hard rule between them:

| Surface | Purpose | Can it place or cancel an order? |
|---|---|---|
| **Telegram** | Briefings, decisions, approvals, kill switch, status | **Yes** — the only approval surface |
| **Web dashboard** | Looking at things: positions, history, calibration, order log | **Never.** Read-only by construction |
| **CLI** | Operations: backfills, migrations, replays, manual reconcile | Admin only, never a discretionary trade |

Why approvals live on Telegram and not in a web app: it's already on your phone with
notifications, it works on hotel wifi, it needs no session management, and it gives you an
immutable message trail of every approval. A web app would need auth, HTTPS, session
handling and a mobile layout before it did anything a chat message doesn't.

---

## 1. v1 — you place the order; the system must still see it

### 1.1 How placement works in v1

There is no order path. You read the briefing at 08:15 and place the trade yourself in the
Dhan app or web terminal. The system has no broker write credentials and no code that could
use them. That is deliberate: it's how you evaluate the research at zero market risk.

### 1.2 The gap, and the fix

Your Telegram reply records your *intention*. Trading reality differs: you fill at a worse
price, you get one leg of a spread, you exit on Thursday because you didn't like the look
of it. If the journal only holds intentions, then calibration — the metric the whole project
turns on — is computed against trades that never happened.

**The fix is read-only broker sync.** Dhan's API exposes the order book, trade book and
positions for *reading* without any order-placement capability. So:

1. A nightly (and on-demand) job pulls your order book, trade book and positions.
2. It matches real fills to briefing ideas on instrument, expiry, strike, right, direction
   and date, proposing a link for anything ambiguous rather than guessing.
3. It writes actual entry price, quantity, exit price, exit time and realised P&L into
   `outcomes`, keeping the hypothetical figures beside them in separate columns.
4. Trades you made that match no briefing idea are recorded too, tagged `unprompted`.
   Those are interesting: they're your own ideas, measurable against the system's.
5. A weekly reconciliation report: ideas you said you took but no fill exists for, fills
   with no matching idea, and slippage versus the briefing's assumed entry.

**Two things this buys that are worth more than the effort.** Your *real* slippage replaces
my slippage guess in the cost model — which is the number that decides whether either setup
is viable at all. And "I said I'd take it, I didn't" becomes visible, which is itself a
finding about the briefing's usefulness.

**Credential note.** This uses read-only access. If your Dhan API key cannot be scoped to
read-only, the key still has order permissions technically available, so the mitigation is
architectural: the order-placement adapter does not exist in the v1 codebase, and
`import-linter` forbids `adapters/dhan/orders.py` from being imported anywhere. Confirm
with Dhan whether a read-only key is issuable — ask them directly.

### 1.3 How you track things in v1

| Question | Where you answer it |
|---|---|
| What should I consider today? | 08:15 Telegram briefing |
| What happened to my open ideas? | 15:45 Telegram review |
| What did I actually fill, and at what price? | Nightly broker sync (§1.2) |
| How is this doing overall? | Monthly report; read-only dashboard from v1.5 |
| Is the system healthy? | `/status` on Telegram; dashboard health page |

---

## 2. The order lifecycle (v2 onwards)

Every order is a row in a state machine. Nothing is implicit, and every transition is
written to the append-only ledger before it is acted on.

```
  PROPOSED            research cycle emitted it (no size, no price)
     │  risk gate sizes it
     ▼
  SIZED               quantity and max loss now exist; still not an order
     │  gate approved + entry condition met
     ▼
  AWAITING_APPROVAL   Telegram message sent, buttons live, timer running
     │                      │                        │
     │ you approve          │ you reject             │ timer expires
     ▼                      ▼                        ▼
  APPROVED              REJECTED                  EXPIRED       ← terminal, logged
     │  router builds legs, assigns idempotency keys
     ▼
  SUBMITTING          sent to Dhan; awaiting acknowledgement
     │                      │
     │ ack                  │ broker error / timeout
     ▼                      ▼
  WORKING             SUBMIT_FAILED → reconcile before any retry  ← never blind retry
     │
     ├── partial fill ──▶ PARTIALLY_FILLED ──┬─ completes ─▶ FILLED
     │                                       └─ leg risk? ─▶ HEDGE_OR_UNWIND (urgent)
     ├── full fill ─────▶ FILLED
     ├── you cancel ────▶ CANCELLED
     └── broker rejects ▶ REJECTED (reason stored verbatim)
     ▼
  OPEN POSITION       position manager owns it: stops, targets, time stop,
     │                invalidation exit, expiry-eve flatten
     ├── exit triggered ─▶ EXITING ─▶ CLOSED
     └── kill switch ────▶ FLATTENING ─▶ CLOSED
```

**Terminal states are terminal.** Nothing re-enters the machine; a replacement is a new
order with a new ID and a link to its predecessor. This is what makes the ledger readable
a year later.

### 2.1 Placement rules

1. **Idempotency key per leg**, derived from `(cycle_id, proposal_id, leg_index, attempt)`.
   A network timeout followed by a retry can never produce two positions.
2. **Submit-then-verify, never submit-then-assume.** After a timeout the system
   *reconciles* against the broker's order book before deciding anything. A timeout is not
   a failure; it's an unknown, and the two must never be conflated.
3. **Slice against F&O freeze limits** — an order above the limit is rejected outright by
   the exchange, so slicing happens before submission, not in response to rejection.
4. **Hedge legs first.** On a defined-risk spread, the protective leg is submitted and
   confirmed before the short leg. A partial fill must never leave you naked, and the
   ordering is what guarantees that rather than hoping both fill.
5. **One in flight per instrument.** No second order for the same underlying while one is
   working. This kills the fat-finger loop at the root.
6. **Every transition is written before it's acted on**, so a crash mid-sequence leaves a
   recoverable record rather than a mystery.

### 2.2 Tracking rules

1. **Order-update websocket** is the primary feed: Dhan pushes status changes, so you learn
   about a fill in milliseconds rather than by polling.
2. **Polling is the backup**, every N seconds, because a websocket you believe uncritically
   is a websocket that will lie to you once.
3. **Reconciliation loop, every cycle and at startup**: broker order book, trade book and
   positions versus internal state. Any divergence halts new entries immediately and alerts
   — it does not auto-correct, because a disagreement about what you own is not something to
   resolve by guessing.
4. **Orphan detection**: a broker position with no internal record (or the reverse) is the
   most dangerous state in the system. Immediate halt and alert.
5. **Reconstruction**: positions are derived from the trade ledger, never stored as a
   mutable running total. If the ledger and the broker disagree, the ledger can be replayed
   to find where they diverged.
6. **Startup is a reconciliation, always.** The system never assumes it knows the book
   after a restart.

### 2.3 The failure cases, each with a defined answer

| Failure | What the system does |
|---|---|
| Crash between submit and ack | On restart: reconcile first, adopt or cancel the live order, never resubmit |
| Submit times out | Mark unknown, reconcile, then decide. Never retry blind |
| One leg fills, other rejected | Immediate hedge-or-unwind; alert; no new entries until flat or hedged |
| Broker rejects for margin | Reject, store the reason verbatim, no retry at smaller size without a new approval |
| Websocket drops with orders working | Switch to polling, alert, block new entries |
| You never answer an approval | Expires silently. No order. Logged |
| Kill switch during a working order | Cancel the working order, then flatten what filled |
| Drawdown limit hit mid-session | New entries blocked in code; existing stops stay live; needs explicit operator action to clear |
| Internal state and broker disagree | Halt, alert, manual reconcile via CLI. Never auto-resolve |

---

## 3. Telegram — the control surface

**v1**
- 08:15 briefing, 15:45 review.
- Per-idea buttons: `Took it` / `Skipped` / `Modified`, then a reason prompt.
- A nag at 10:00 for unanswered ideas.
- `/status` — feed health, open ideas, capture health, month-to-date cost.
- `/why <idea_id>` — the full evidence chain with source links.

**v2 adds**
- Approval cards: instrument, structure, strikes, net debit/credit, max loss in rupees and
  as a percentage of equity, every gate value, and the thesis in one line — with
  `Approve` / `Reject` / `Approve & halve size` and a visible countdown.
- `/positions`, `/pnl`, `/orders`.
- `/kill` with a confirmation step, and `/flatten` likewise.
- Push alerts on fills, stops, rejections, halts.

**Two rules for this surface.** The approval message must contain **max loss in rupees**,
because a percentage at 9am on a phone is not a number anyone feels. And the kill switch
must work when everything else is broken — it sets one Redis key, with no dependency on the
engine being alive.

---

## 4. The web dashboard — read-only, and that's the feature

I had this out of scope. Your question is fair, so here's the revised position: **a
read-only dashboard is worth building in v1.5; a control UI is not worth building at all.**

The split isn't aesthetic. Every button that can place an order is a new path to an
unintended trade, needing its own auth, CSRF protection, confirmation design and audit
trail. Reading needs none of that. So the dashboard is read-only *by construction* — it
runs behind the same FastAPI app with no write endpoints at all, not merely with the
buttons hidden.

**Pages, in build order:**

1. **Today** — this morning's briefing as rendered, the two ideas with every gate value,
   rejected candidates with killing gates, your recorded decisions.
2. **Ideas history** — every idea ever, filterable by setup, rank and outcome. Each row
   expands to the full evidence chain with source links and the exact prompt/response
   trace. This is the page you'll actually use, because "why did it say that in March?" is
   the question you'll keep asking.
3. **Calibration** — the reliability curve, Brier versus baseline over time, hit rate by
   setup and by rank, and the no-trade rate against its 40–60% band.
4. **Journal & overrides** — your decisions and reasons, searchable. The page that answers
   *what rule am I applying that the system doesn't know about?*
5. **Health** — feed status, snapshot capture completeness (a calendar heatmap: a missing
   day should be visibly alarming), cycle durations, LLM spend against cap.
6. **Positions & orders** *(v2)* — live positions, the order state machine per order with
   full transition history, the reconciliation log.
7. **Trade detail** *(v2)* — one trade end to end: thesis → gates → approval → every order
   transition → fills → management actions → exit → attribution.

**Tech, kept deliberately boring:** the FastAPI app already in the plan, server-rendered
HTML with HTMX, Plotly for the two charts that need interactivity. No React, no build step,
no separate frontend repo. Local network only, behind Tailscale or similar — never exposed
to the internet, because a public dashboard is an invitation to look at your positions.

Estimated 10–14 hours for pages 1–5. Worth it: by month two you'll be asking historical
questions daily, and "grep the logs" doesn't survive that.

---

## 5. CLI — operations only

`universe refresh`, `backfill bars`, `snapshot run --date`, `cycle replay <cycle_id>`,
`journal export`, `reconcile --broker`, `db migrate`, `calibration report`.

One rule: **the CLI never places a discretionary trade.** `reconcile` and `flatten` are
admin recovery actions, logged like everything else, and `flatten` only ever reduces risk.

---

## 6. New requirements this introduces

To be folded into [08-requirements.md](08-requirements.md).

| ID | Requirement | Release | Pri | Done when |
|---|---|---|---|---|
| FR-13.1 | Read-only broker sync of order book, trade book and positions | v1 | MUST | Nightly job pulls and stores; no write capability in the codebase |
| FR-13.2 | Match real fills to briefing ideas; ambiguous matches proposed, not guessed | v1 | MUST | A hand-checked month matches correctly, ambiguities surfaced |
| FR-13.3 | Store actual and hypothetical outcomes in separate columns | v1 | MUST | Calibration can be computed on either, and reports which |
| FR-13.4 | Record unprompted trades (yours, not the system's) tagged as such | v1 | SHOULD | They appear in the monthly report, compared against system ideas |
| FR-13.5 | Weekly reconciliation report: claimed-but-not-filled, filled-but-unprompted, slippage vs assumed entry | v1 | MUST | Delivered weekly; real slippage replaces the modelled figure in FR-10.3 |
| FR-13.6 | Order-placement adapter absent from the v1 codebase, enforced by the layering checker | v1 | MUST | A contract forbids importing an order adapter; CI fails if one appears |
| FR-14.1 | Order lifecycle implemented as an explicit state machine with terminal states | v2 | MUST | Every transition logged; invalid transitions raise |
| FR-14.2 | Per-leg idempotency keys; retry can never double-send | v2 | MUST | Duplicate-submission test produces one order |
| FR-14.3 | Submit-then-verify: a timeout triggers reconciliation, never a blind retry | v2 | MUST | Timeout fixture results in reconcile, not resubmit |
| FR-14.4 | Hedge leg confirmed before short leg on every defined-risk structure | v2 | MUST | Partial-fill simulation never leaves an unhedged short |
| FR-14.5 | One order in flight per instrument | v2 | MUST | Concurrent-proposal test blocks the second |
| FR-14.6 | Order-update websocket primary, polling backup | v2 | MUST | Fill detected within latency budget; polling catches a dropped socket |
| FR-14.7 | Reconciliation at startup and every cycle; divergence halts and never auto-corrects | v2 | MUST | Injected divergence halts entries and alerts |
| FR-14.8 | Orphan detection in both directions | v2 | MUST | Both orphan types detected in fixtures |
| FR-14.9 | Positions reconstructed from the ledger, never a mutable total | v2 | MUST | Replaying the ledger reproduces current positions exactly |
| FR-14.10 | Approval card shows max loss in rupees, all gate values, and a countdown | v2 | MUST | Reviewed on a real phone screen |
| FR-15.1 | Read-only web dashboard, pages 1–5 | v1.5 | SHOULD | Runs with zero write endpoints; served on local network only |
| FR-15.2 | Dashboard write-endpoint absence enforced by test | v1.5 | MUST | A test enumerating routes fails on any non-GET |
| FR-15.3 | Evidence chain and LLM trace viewable per historical idea | v1.5 | SHOULD | Any idea from any past session is fully explainable from the UI |
| FR-15.4 | Capture-completeness heatmap | v1.5 | SHOULD | A deliberately deleted snapshot day is visibly obvious |
| FR-15.5 | Positions, order state machine and reconciliation log pages | v2 | SHOULD | Each order's full transition history is viewable |

---

## 7. New build steps

To be folded into [10-build-plan.md](10-build-plan.md).

| Step | Goal | Depends on | Est |
|---|---|---|---|
| **1.10** | Read-only broker sync: order book, trade book, positions → DB | 1.7 | 4 hrs |
| **3.10** | Fill matching, outcome enrichment, weekly reconciliation report | 1.10, 3.8 | 5 hrs |
| **3.11** | Dashboard skeleton: FastAPI read-only app, pages 1 and 5 | 3.9 | 5 hrs |
| **3.12** | Dashboard pages 2–4: history, calibration, journal | 3.11, 3.8 | 7 hrs |
| **4.0** | Order lifecycle state machine and ledger, no broker attached | Gate 2 | 6 hrs |
| **4.4b** | Approval card UX on a real phone | 4.4 | 2 hrs |
| **4.9** | Reconciliation and orphan detection | 4.5 | 5 hrs |
| **4.10** | Dashboard pages 6–7 | 4.9 | 5 hrs |

Step 1.10 is the one worth doing early: **the journal is being written from Phase 1, and
without it every row records what you meant rather than what happened.**

Phase 1 grows by 4 hours, Phase 3 by 17 (12 of which is the dashboard and could slip to
v1.5 without blocking anything). The critical path is unchanged.
