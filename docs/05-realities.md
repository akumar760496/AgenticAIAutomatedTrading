# 05 — Realities to accept before you start

These aren't discouragement. They're the constraints that decide whether this ends as a
tool you trust or an expensive lesson.

## Regulatory (India, as of Oct 2026 — verify against primary sources)

SEBI's 2025 algo-trading framework reshaped retail API trading. What matters for you:

- **Static IP.** Orders via API must come from an IP your broker has registered. Cloud
  instances with dynamic IPs break this — hence an **AWS Mumbai VM with an Elastic IP**.
  Reports indicate the registered IP can be changed only about once a week, so don't plan
  on hopping hosts.
- **Algo registration / tagging.** Algo orders carry exchange-issued identifiers, and the
  chain Client → Algo ID → Static IP → API key is auditable. Ask Dhan directly what *your*
  account needs: self-use algo registration, an algo ID, order tagging, and any order-rate
  threshold above which stricter obligations kick in.
- **Order-rate threshold.** Retail self-use algos are generally permitted below an
  exchange-prescribed order frequency. Design to stay well under it; it's another reason
  to prefer a few considered trades over a high-frequency approach.
- **Dates conflict across sources** (Aug 2025, Oct 2025, and an extension to Apr 2026 are
  all cited by different vendor blogs). **Confirm with Dhan and the SEBI/NSE circulars
  directly** — this is the one item where getting it wrong risks your account, not just
  your P&L.
- Strategies for **your own use** are the simple case. The moment you share, sell, or
  manage someone else's money with it, you're in a different regulatory category
  (vendor empanelment, exchange strategy approval, possibly RIA/PMS registration). Don't
  drift into that accidentally.

## Financial

- **Most retail algo systems lose money.** Costs and slippage are the usual killers, not
  bad signals. Your edge must exceed round-trip cost, which for options is mostly the
  bid-ask spread, not the brokerage.
- **An LLM has no millisecond alpha.** Its plausible edge is in breadth and context:
  reading 500 filings nightly, noticing a supply-chain link between two companies,
  catching a governance flag you'd miss. Build toward that, not toward speed.
- **Backtest results will overstate reality.** Expect live performance meaningfully worse
  than paper, and paper worse than backtest. If a strategy is only marginally profitable
  in backtest, it loses live.
- **Short options are the common blow-up.** Selling premium looks like free money for
  months and then gives it all back in one gap. Defined risk only, and keep it that way
  until you have years of evidence.
- **Taxes:** F&O income is typically business income with audit implications, and frequent
  equity trading may be treated as business income too. Talk to a CA before scaling — it
  changes the after-tax edge calculation materially.
- **LLM costs are real.** Hundreds of news items a day through a frontier model adds up.
  Hence the Haiku-triage/Opus-synthesis split and hard budget caps.

## Engineering

- Your process will crash mid-position. Stops must live at the broker, state must be
  reconstructible from the broker plus your ledger, and startup must reconcile before
  doing anything else.
- The feed will disconnect during the one minute that matters. Fail closed.
- Dhan rate limits are real and differ per endpoint (order APIs are the tight ones).
  Build a token-bucket limiter once, in the adapter, and route everything through it.
- Corporate actions will corrupt your price history and surprise your option positions.
  Handle them explicitly in Phase 1 rather than discovering it via a phantom 40% gap.
- News text is untrusted input reaching an LLM that has tools. Treat prompt injection as a
  live threat: news bodies are data, tools are gated, and no agent output reaches a broker
  without passing the deterministic gate.
- Expiry day is its own special hell: liquidity cliffs, gamma, strike skew, settlement
  mechanics. Consider excluding expiry-day trading entirely at first.

## Decisions already taken in response to these realities

The six suggestions that used to sit at the bottom of this document are now the plan
itself, not advice. Where each one lives:

| Decision | Where it's specified |
|---|---|
| Narrow scope: Nifty + Bank Nifty + 25 stocks, two setups | [06-v1-scope.md](06-v1-scope.md) §1–2 |
| Research assistant before trader — briefing is v1 and ships alone | [03-roadmap.md](03-roadmap.md), [06](06-v1-scope.md) §3 |
| Human gate stays on through v2 and v3 | [06-v1-scope.md](06-v1-scope.md) §6 |
| Journal from day one, including override reasons | [06-v1-scope.md](06-v1-scope.md) §4, built in Phase 1 |
| Calibration first-class; gates whether sizing may use confidence | [06-v1-scope.md](06-v1-scope.md) §5 |
| Stop criteria written down with dates and numbers | [07-stop-criteria.md](07-stop-criteria.md) |

Two consequences of those decisions worth naming here, because they're costs:

- **Narrow scope means you will watch opportunities go by.** Twenty-five stocks and two
  setups will miss things that were obviously good in hindsight. That is the trade:
  a system whose failures you can diagnose, versus one that trades everything and teaches
  you nothing. Resist adding a third setup until the first two have 100 resolved ideas.
- **v1-before-v2 means three months before any automated order.** It will feel slow. The
  compensation is that those three months cost nothing but attention, and they're the
  cheapest possible place to discover the reasoning doesn't work.

## Still worth your attention

- **Opportunity cost is real and never appears in a P&L curve.** The quarterly review in
  [07-stop-criteria.md](07-stop-criteria.md) asks directly whether this beats an index fund
  after your time. Answer it honestly.
- **Don't drift into regulated territory.** Strategies for your own use are the simple
  case. Sharing, selling, or managing anyone else's money with this puts you in a different
  category entirely — vendor empanelment, exchange strategy approval, possibly RIA/PMS
  registration.
- **Scope creep is the actual failure mode here**, more than any market risk, because in v1
  there is no market risk. The thing most likely to kill this project is a sixth setup, a
  third exchange, or a dashboard rewrite in month two.
