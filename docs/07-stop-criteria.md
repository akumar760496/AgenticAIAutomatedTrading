# 07 — Stop Criteria

Written **2026-10-14**, before a single line of trading code exists and before any money is
at stake. That timing is the entire point: these numbers are easy to write now and nearly
impossible to write honestly once you're down 8% and certain the next trade comes back.

Fill in the blanks, commit the file, and treat it as binding. If you later want to change a
threshold, change it in a commit with a dated reason — not in your head at 15:20 on a bad
day.

---

## Gate 1 — Build discipline

| Checkpoint | Date | If missed |
|---|---|---|
| Phase 0 + 1 complete (data spine, chain snapshotter recording daily) | **2026-12-15** | Cut scope to: chain snapshotter + daily briefing on index only. Drop single stocks until the spine is solid. |
| First automated briefing delivered to Telegram | **2027-01-15** | Reassess whether the time budget is real. A project that can't produce a daily briefing in three months will not produce a profitable trading system in twelve. |

**The honest test:** if two consecutive months pass with under 10 hours of work on it, stop
and keep the chain snapshotter running as a cron job. The IV history keeps accruing value
while you decide whether you actually want this.

---

## Gate 2 — Is the research any good? (v1 briefing, no money)

Evaluated after **60 briefing sessions** (~3 months), target date **2027-04-15**.

**Stop and reassess if any of these hold:**

| Metric | Failure threshold |
|---|---|
| Rank-1 idea hit rate, hypothetical, after full costs | < 40% |
| Expectancy across all briefed ideas, after full costs | ≤ 0 |
| Brier score vs naive base-rate baseline | Fails to beat it |
| Confidence resolution | Effectively flat — every call clusters at one value |
| No-trade day rate | < 20% (gates too loose) or > 85% (system is inert) |
| **Your own engagement** | You ignored or didn't read > 70% of briefings |

That last row is the one most people would leave out, and it's the most informative. If
you stop reading the briefing, it isn't useful — regardless of what the hit rate says. A
tool you don't open has no edge.

**Schema and hallucination failures are not graded on a curve:** any briefing citing a
number no tool produced, or a URL not in its input context, is a hard bug. Three in a
month halts the project until fixed.

---

## Gate 3 — Does it survive contact with costs? (v2 paper)

Evaluated after **100 paper trades** spanning at least one volatility-regime change.

**Stop if:**
- Expectancy ≤ 0 after full modelled costs (brokerage, STT, GST, stamp duty, exchange fees,
  and for options the bid-ask spread — usually the dominant term)
- Max drawdown > 15% of notional allocated capital
- Max adverse excursion routinely exceeds the stop, i.e. the stops are theatre
- The paper fill model proves optimistic once compared against real spreads

If 100 trades takes six months at 40–60% no-trade days, **it takes six months.** Shortening
the sample to reach a verdict faster is the single most common way this kind of project
destroys money.

---

## Gate 4 — Live capital

Starting allocation: **₹_______** (fill in — the amount you'd be genuinely fine losing
entirely). Risk per trade: **0.25%** flat until calibration is earned.

| Trigger | Action | Threshold |
|---|---|---|
| Daily loss | Auto-halt, no new entries until next session | **2%** of allocation |
| 3 consecutive losing days | Auto-halt, manual review required to resume | — |
| Peak-to-trough drawdown | **Soft halt.** Stop new entries, manage existing, write a review before resuming | **6%** |
| Peak-to-trough drawdown | **Full stop.** Flatten everything, return to paper, no live capital for 30 days minimum | **10%** |
| Live vs paper divergence | Investigate before next trade | Live expectancy < 50% of paper over 30 trades |
| Any unintended order | Full stop until root-caused and a regression test exists | 1 occurrence |
| Calibration decay | Revert to flat sizing | Brier worsens below baseline over trailing 50 |

The 6% and 10% levels are enforced in code by the risk engine, not by your judgment. A
drawdown limit you can talk yourself out of is not a limit.

**Scheduled reviews, in the calendar now:** monthly on the first Sunday, and a hard
quarterly review at **2027-07-15** with a written answer to one question — *is this better
than an index fund, after my time?* Opportunity cost is a real cost and it never shows up
in a P&L curve.

---

## Gate 5 — The sunk-cost escape hatch

If at any review you conclude it isn't working, the acceptable outcomes are:

1. **Keep the briefing, drop the trading.** A daily research assistant is genuinely useful
   and costs almost nothing to run.
2. **Keep the data, drop the agents.** Your accumulated option-chain and IV history is a
   real asset for any future project.
3. **Stop entirely.** Having built a well-architected system that correctly told you the
   edge wasn't there is a *successful* outcome, not a failed one. The system's job is to
   find out whether an edge exists — a truthful "no" is the answer working.

What is **not** an acceptable outcome: increasing size to recover a drawdown, disabling the
risk gate "temporarily", or turning on auto-approve to trade more. Each of those is the
failure mode this document exists to prevent.

---

*Owner: Ajay · Written: 2026-10-14 · Next review: 2026-12-15 (Gate 1)*
