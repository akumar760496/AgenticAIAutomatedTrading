# 04 — What I need from you

Reorganised around the v1-first plan. **v1 needs far less from you than the original plan
did** — no live trading means no static IP, no algo registration, and no capital decisions
until much later. Three items block the start.

**Nothing in here should be pasted into chat.** Put secrets in a local `.env` (gitignored)
and tell me it's populated; I'll write code that reads it and never echoes it. Any
credential that has touched a chat window should be considered compromised and rotated.

---

## A. Blocking — needed to start v1 Phase 1

1. **Dhan API access enabled** (DhanHQ v2), confirmed active in the Dhan web/app.
2. **Client ID + access token** → `.env` as `DHAN_CLIENT_ID`, `DHAN_ACCESS_TOKEN`. Tell me
   the token's expiry and whether you want renewal manual or scripted.
3. **Your data entitlements**, specifically:
   - Live market data entitlement — yes/no
   - Historical and intraday-historical data access — yes/no
   - **Websocket instrument cap on your plan.** v1 needs ~27 instruments plus option
     chains. If your cap is tighter than that, the universe shrinks and I need to know now.
   - Segments enabled: NSE cash, NSE F&O (both are required for v1)

That's genuinely it to begin. Everything below can arrive while Phase 1 is being built.

---

## B. Needed by Phase 3 (the agents and the briefing)

4. **Anthropic API key** → `ANTHROPIC_API_KEY`, plus a **monthly spend cap** you're
   comfortable with so I can set the budget guard. A rough v1 estimate: a few hundred
   rupees a day with Haiku triage and Opus synthesis on 27 instruments, but I'll measure it
   in Phase 3 rather than guess.
5. **Telegram bot** — create one via @BotFather; I need the bot token and your chat ID.
   This is the entire delivery mechanism for v1, so it's not optional.
6. **News API keys.** Start with one paid symbol-keyed Indian feed plus free official
   sources:
   - A symbol-keyed Indian equity news API (Drishti, or Marketaux for broader publisher
     coverage) — key + plan tier
   - NSE/BSE corporate announcements. Either a vendor feed (TrueData offers REST + WS
     corporate announcements) or we read the official feeds directly. Vendor is more
     reliable; official is free. Your call.
   - Check commercial-use licensing before we build on whichever you pick.
7. **Your own research material** — watchlists, setups that have worked for you, screeners
   you use, any trade journal history. **This is the most valuable input on the whole list
   and the only one I can't source elsewhere.** The two v1 setups in
   [06-v1-scope.md](06-v1-scope.md) are my analyst judgment; if your experience says
   otherwise, yours should win, because you're the one who has to trust the output at 09:20
   on a gap-down morning.

---

## C. Worth asking about early (potentially a six-month shortcut)

8. **Historical option chain / IV data for NSE** — ask TrueData and other NSE data vendors
   whether they'll sell historical option chain snapshots with IV and greeks, how far back,
   and at what price. Both v1 setups gate on **IV rank**, which needs ~1 year of IV
   history that you don't have and can't backfill from price data. If this is purchasable
   at a sane price it pulls the IV-gated setups forward by six months, making it the single
   highest-leverage purchase in the project. If it isn't, we run on a realised-volatility
   proxy and accumulate the real thing. Either way I need to know which.

---

## D. Decisions I need from you now (no credentials)

9. **Package name** — decided: `trading_desk` (2026-10-15). No longer open.
10. **Where it runs.** For v1, your Mac plus a small always-on box is fine; no static IP is
    needed because nothing places orders. For v2 you'll want the AWS Mumbai VM with an
    Elastic IP. Confirm you're happy to defer that cost.
11. **Setup review.** Read [06-v1-scope.md](06-v1-scope.md) §2 and push back. The gate
    thresholds — 1.5× ATR for "already priced in", IV rank 60/70/35 boundaries,
    0.7 credibility — are defensible starting points, not truths. Your disagreements are
    more useful before they're in code.
12. **Fill in the blanks in [07-stop-criteria.md](07-stop-criteria.md)** — the capital
    figure, and confirm or change the drawdown thresholds. Do this now, while you're
    unattached to the outcome. That's the whole point of the document.
13. **Realistic time budget per week.** If it's under ~5 hours, say so and I'll cut v1 to
    index-only with one setup. A smaller thing that actually ships beats the full plan
    stalling in Phase 1.

---

## E. Deferred to v2 / v3 (do not do this work yet)

Listed so you know it's tracked, not forgotten:

- **Static IP + Dhan algo registration.** Needed only when orders go through the API. When
  you get there, ask Dhan in writing about: static IP registration process, algo ID /
  strategy registration for self-use algos, order tagging, the order-rate threshold above
  which exchange approval is required, per-endpoint rate limits, and whether a sandbox
  exists for order testing. See [05-realities.md](05-realities.md).
- **Capital allocation, risk-per-trade and daily-loss numbers** beyond the placeholders —
  needed at Phase 4, not now.
- **A CA conversation** about F&O taxation and audit thresholds — before live size, not
  before paper.
- **Second broker** as a data-redundancy source (you mentioned other providers; tell me
  which when relevant). Execution stays on one broker regardless.

---

## Handover format

I'll generate `.env.example` in Phase 0. Copy it to `.env`, fill in what you have, leave
the rest blank, and tell me which keys are populated. Adapters sit behind ports, so a
missing news provider blocks only that provider's agent — nothing else.
