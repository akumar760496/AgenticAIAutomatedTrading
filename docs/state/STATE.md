# STATE — where the project is right now

**Rewritten in place every session. Never appended to.** History belongs in `DEVLOG.md`.

---

**Updated:** 2026-10-19
**Phase:** 0, 1 and 2 complete. **Phase 3: 3.1 to 3.6 done.** Next is 3.7, the scheduler.
**Package name:** `trading_desk` (decided 2026-10-15)

**Nine things are waiting on you: ISS-021 (delta bands), ISS-022 (capital figure),
one real contract note, the LLM monthly cap, 200 hand-labelled news items, ISS-026
(pledge data), ISS-027 (sector map), ISS-028 (the F&O ban list, the only fail-open
input in the gate chain), and ISS-025 — still the one that matters most.**

**Three things now stand between you and a real briefing on your phone:** a
Telegram bot token and chat id, the `ANTHROPIC_API_KEY`, and a capital figure in
`config/risk.yaml`. The first two are blockers; the third only costs you the size
line, which currently reads "unavailable (ISS-022)" every morning by design.

**A decision for you on the stack:** `docs/10` names **LangGraph** for the
strategist graph and I did not use it — the graph is a static six-node DAG and
plain Python kept the tree `mypy --strict` clean. It is behind one module, so
swapping LangGraph in is a contained change. Say if you want it (DEVLOG 3.4).

**ISS-025 is the significant one.** Stored filings are subject lines, not content
(mean body 144 characters), so `EARNINGS_SURPRISE` and `GUIDANCE_CHANGE` — the two
catalyst types Setup A was built around — cannot be detected at all from what the
database holds. Three options are written up in `ISSUES.md`; my recommendation is
to narrow Setup A to the five reachable types for v1 and fetch the filing PDFs as
the next data step.

## Done

- **Planning** — `docs/00` … `docs/15`, `CLAUDE.md`, `.claude/skills/` (5 skills)
- **Phase 0 (0.1–0.6)** — toolchain, 4 import-linter contracts, typed settings with
  secret redaction, value objects in integer paise, Docker Postgres
  (TimescaleDB + pgvector) with alembic, 15 ports each with a fake, network blocked
  in the suite. Every guard induced, not assumed.
- **Phase 1 (1.1–1.9)** — the data spine. Live Dhan chain adapter and Parquet archive,
  the scheduled snapshotter, universe service, bar builder, 940-bar historical
  backfill, NSE filings (1,538 stored, free), SEBI news via RSS, calendars, the
  append-only journal (10 database triggers, including the TRUNCATE path), IV rank
  with a realised-vol proxy, and backup with an automated restore drill.
- **Phase 2 (2.1–2.8)** — the decision core, all pure `domain/`. Research and proposal
  models that cannot express a quantity, indicators and signals, Setup A (9 gates) and
  Setup B (5 gates), the vertical-spread builder that raises rather than emit a naked
  short, Black-76 greeks, the 13-check risk gate, the cost model and the first dry run.
- **Step 3.1** — the LLM adapter and cost governor. Anthropic client with forced-tool
  structured output, Haiku/Opus tiering (measured 50x apart), an append-only `llm_calls`
  ledger (migration 0006), a cap that **refuses before the call on an estimate**, a
  recorded-cassette client so the suite costs nothing, and local JSONL tracing.
  Verified 10/11; the cap refusal and the append-only triggers both induced.
- **Step 3.2** — the News & Sentiment agent. Catalyst classifier and impact analyst
  (the only two LLM nodes), rule-based credibility scoring that shows its working,
  the six-stage pipeline, a confusion-matrix report that refuses to pass on accuracy
  alone, and the labelling harness. Verified 12/14; injection induced in three forms.
  **Four of the six sub-agents already existed** as deterministic code from step 1.5.
  Plus `domain/filing_subjects.py`: NSE's own subject vocabulary settles **60% of
  filings with no model call** (measured over 1,542).

- **Step 3.3** — the other four specialists. Fundamental (a veto table over the
  exchange's subject vocabulary), Technical (four registered signals, **0 backtested**,
  and it says so), Options Strategist (the `docs/02` volatility matrix encoded, not
  prompted), Portfolio Context. **None of the four makes a model call** — they are
  arithmetic, and the DEVLOG says why. Verified **15/15**, including `docs/12`'s
  determinism check run early: 4 specialists x 3 runs, every measured value identical.

- **Step 3.4** — the Chief Strategist graph. One cycle, six nodes, one `cycle_id`
  on every artifact; at most two proposals; "no ideas" with a counted reason; every
  rejection carrying the gate that killed it; append-only checkpoints (migration 0007)
  and a response cache so a **resumed cycle repeats no paid work**. One LLM node
  (synthesis, Opus tier) that writes prose and can return nothing else. Verified
  **17/17**, including the strong form of `docs/12`'s determinism check: different
  prose about the same facts, nine gate values unchanged.

- **Steps 3.5 and 3.6** — the briefing and answering it. Five sections rendered
  from a real cycle (decision message **614 chars / 12 lines**, all **9 gate values**
  in the detail, evidence with URLs and timestamps, cost per node); Telegram adapter
  with splitting, retries and **the bot token kept out of every error** (it is in the
  URL path); decision capture whose state is the message trail, so a restart between
  the tap and the reason loses nothing; the 10:00 nag, one message however many ideas.
  Verified **11/13** and **11/12** — every failure is a credential or a real send.

## Next action

**Four things from you, in the order that unblocks the most:**

1. **A Telegram bot token and chat id.** Message `@BotFather` on Telegram, send
   `/newbot`, follow it, and paste the token into `.env` as `TELEGRAM_BOT_TOKEN`.
   Then message your own new bot once (anything), and run:
   ```
   uv run trading-desk telegram-check
   ```
   If `TELEGRAM_CHAT_ID` is still blank it will tell you; the id is the number in
   `https://api.telegram.org/bot<token>/getUpdates` after you have messaged the bot.
2. **`ANTHROPIC_API_KEY` in `.env`**, and a real monthly cap in
   `config/llm_pricing.yaml` (item 5 below). No cycle runs without the key.
3. **A capital figure** in `config/risk.yaml` (`equity_rupees`). Everything works
   without it; the briefing just says the size is unavailable, every morning.
4. **The 200 labels** — still the long pole, and **decide ISS-025 first**.

Then:

```
uv run trading-desk brief      # one cycle, delivered
uv run trading-desk capture    # record whatever you answered
uv run trading-desk nag        # chase anything unanswered after 10:00 IST
```

**Then step 3.7 — the scheduler**, which runs the three commands above on a
timetable (08:15 brief, 10:00 nag, a capture loop) and wires the IV-rank service
into the cycle. Until 3.7 the briefing has no IV rank, so every idea dies at the
volatility gate — visibly, in the rejected list.

Unrelated and still outstanding from Phase 1, whenever a trading day allows:

```
uv run trading-desk survey      # ~30 minutes, 218 underlyings
make verify STEP=1_1b           # the live capture check
```

The survey refuses to run on a weekend or holiday (ISS-006: the feed serves the
previous session's volume without saying so). `make login` only if the token has
aged out.

## Blocked on you

1. **ISS-022 — what is your capital figure?** (`docs/07` is blank.) Flat 0.25% risk
   means a NIFTY spread risking ₹10,500 per lot needs **₹42 lakh** equity to take one
   lot; at ₹10 lakh the gate correctly returns zero and the briefing never produces a
   tradable size. The most consequential open number in the project.
2. **ISS-021 — Setup A's delta bands do not fit real chains.** Only 10 of 25 universe
   names have a strike in both the 0.50–0.55 and 0.25–0.30 bands. My recommendation:
   select the *nearest strike to a delta target* with a documented maximum deviation.
   Four options written up in `ISSUES.md`. `verify 2_5` fails until this is decided.
3. **One real Dhan contract note** from any options trade you have already done, so the
   cost model reconciles to the rupee (FR-10.3). `verify 2_8` fails until then.
   Costs currently total ₹272.30 round trip — **13% of a real spread's best case** —
   and the largest component, ₹150 of slippage, is an assumption (ISS-023).
4. **ISS-025 — the earnings gap.** Narrow Setup A to five catalyst types for v1,
   fetch and parse the filing PDFs (1,524 URLs already stored), or check whether the
   paid Dhan Data API exposes reported-vs-estimated earnings. My recommendation: the
   first now so Phase 3 is not blocked, the second as the next data step.
4b. **200 hand-labelled news items** — the sheet is already written to
   `data/labels/news_catalysts.csv` (200 rows, 97 distinct subjects). Fill in the
   `true_label` column. 40 of them, chosen by a stable hash rather than by me, are
   held out and neither of us looks at them while tuning. `verify 3_2` fails on this
   and on the 90% score it gates.
4c. **ISS-026 — the Fundamental agent cannot see promoter pledge or sell-down.**
   Measured: 0 of 1,542 filings mention pledge, encumbrance or promoter. They are
   filed under SEBI SAST/PIT on an NSE endpoint we do not poll. My recommendation:
   add that poller after the filing PDFs — same crawler, different path, free.
4d. **ISS-027 — no sector map exists**, so correlated names cannot be detected.
   My recommendation: use NSE's own sectoral index membership (NIFTY BANK, NIFTY IT,
   …). One config file with a source URL, and it is the definition the market trades.
4f. **ISS-028 — the F&O ban list and the earnings calendar have no source**, and
   both default to the permissive answer, so Setup A's calendar gate passes a banned
   symbol. The only fail-open input in the gate chain. My recommendation: fetch the
   ban list — published daily, free, one more poller of the kind 1.5 already has.
4g. **A Telegram bot token and your chat id** — see item 1 under Next action.
4h. **A capital figure in `config/risk.yaml`** (ISS-022 again, now with one place
   to put it). Without it every briefing prints "size unavailable" where FR-6.2
   wants a lot count. With ₹42,00,000 the same idea reads "1 lot(s), risking
   ₹5,500 of a ₹10,500 budget".
4e. **Two numbers `config/setups.yaml` does not have.** Setup B's *debit* variant has
   no delta bands (the agent refuses that path rather than borrowing Setup A's), and a
   credit spread's wing offset is a constructor argument pending your sign-off — it
   decides the max loss.
5. **The monthly LLM cap, and the FX rate** (`config/llm_pricing.yaml`,
   `verified: false`). ₹2,000/month and 89 INR/USD are both placeholders. The rate
   that matters is the one your card is actually billed at, including the bank's
   markup — never the mid-market rate. The per-token prices in that file *are* real,
   fetched from Anthropic today. `verify 3_1` fails until one month's ledger total has
   been compared against the Anthropic console.
5b. **An `ANTHROPIC_API_KEY` in `.env`**, before any live call.
5c. **Tracing: Langfuse or the local file?** `docs/10` names Langfuse, which needs a
   hosted account or a self-hosted stack. I wired a local JSONL tracer instead, which
   meets the walk-back requirement at no cost and keeps our prompts off a third party.
   Say if you want Langfuse — it is one class against the existing port.
5d. **Optional: swap `claude-opus-5` for `claude-opus-5-5`** — newer and cheaper
   ($4/$20 against $5/$25 per million). Left as `docs/10` specifies rather than
   changed silently. One line in `config/llm_pricing.yaml`.
5. **Setup thresholds sign-off** (`config/setups.yaml`, `verified: false`) — `verify
   2_3` fails on it by design.
6. **IV-rank threshold definition.** `docs/06` says 60 / 70 / 35 but not whether those
   are IV *rank* or IV *percentile*. Different numbers. This implements percentile.
7. **Liquidity thresholds** (`config/exchange_rules.yaml`, `verified: false`) —
   turnover ₹50L, OI 50,000, 25% priced legs, 25 stocks. Set permissive on purpose;
   tighten once the first survey shows the real spread. `verify 1_2` fails on it.
8. **Universe backfill go-ahead.** Only NIFTY and RELIANCE have daily bars, so the
   other 24 names produce no IV rank at all (correctly — they refuse rather than
   guess). Needs a `backfill` command and ~24 × 940 bars of live Dhan history.
9. **Offsite backup provider** — backups are local only, on the same disk as the
   database. That survives a dropped table, not a lost laptop, and the option-chain
   archive cannot be re-fetched at any price. A versioned bucket (S3, B2, R2) plus
   credentials. `verify 1_9` fails on `copied_offsite` by design.
10. **Perform one restore by hand** (`docs/15` §4, ~15 minutes, scratch DB, no risk).
    `docs/10` says 1.9 is done when *you* have restored once.
11. **Dhan Data API subscription (₹499 + GST/month)** — required for live feed, option
    chain and historical bars beyond what is already captured.
12. **Licensed news API — a purchase decision (ISS-010).** No commercial Indian
    financial publisher permits this use; 16 feeds surveyed, clauses quoted in
    `config/news_sources.yaml`. SEBI + NSE filings cover regulatory disclosure for
    free, so **nothing is blocked today**.
13. **Optional, free: write to RBI** asking permission to cache and deep-link their
    feeds (ISS-015). Their terms prohibit both without written permission. One email.
14. **NSE poller User-Agent (ISS-013)** — robots.txt permits the path but their bot
    protection only answers a browser string. Kept on the basis that it is mandated
    public disclosure; say if you would rather drop it.
15. Drawdown confirmation (`docs/07`), realistic weekly hours (under 5 → cut to
    index-only, `docs/10` §7), decision-capture mechanism (`docs/11` §1.2).

## Verification sweep — 2026-10-19

| Step | Result | Note |
|---|---|---|
| 0.1 toolchain | PASS 6/6 | |
| 0.3 config & secrets | PASS 7/7 | |
| 0.4 value objects | PASS 10/10 | clock check is an AST walk |
| 0.5 infrastructure | PASS 8/8 | compares against the head revision |
| 0.6 ports & fakes | PASS 6/6 | **19 ports, 19 fakes** (`CycleStore` 3.4, `ChatPort` 3.6) |
| 1.1a chain adapter | PASS 5/5 | live NIFTY chain, 235 strikes, greeks present |
| 1.1b archive & snapshotter | **7 of 8** | `live_capture` needs a trading day |
| 1.2 universe service | **6 of 7** | `thresholds_reviewed` needs your sign-off |
| 1.3a schema & bar builder | PASS 8/8 | |
| 1.4 historical backfill | **7 of 8** | `close_matches_an_independent_endpoint` compares a stored bar against a **live** spot, so it drifts off a trading day |
| 1.5a exchange filings | PASS 8/8 | 1,538 filings stored live from NSE, free |
| 1.5b news articles | PASS 7/7 | 25 SEBI articles live; 5 guards induced. RBI excluded |
| 1.6 calendars | PASS 6/6 | expiry list audits the holiday file; found Guru Nanak missing |
| 1.7 journal schema | PASS 8/8 | 5 tables, 10 triggers; UPDATE/DELETE/TRUNCATE all refused |
| 1.8 IV rank | **7 of 8** | proxy path live; `universe_has_bar_history` — 2 of 26 names |
| 1.9 backup & restore | **6 of 7** | real restore matched 3,448 rows; `copied_offsite` by design |
| 2.1 research models | PASS 8/8 | no-size introspection induced at top level and nested |
| 2.2 indicators & signals | PASS 8/8 | 6 signals registered, **0 backtested** |
| 2.3 Setup A gates | **7 of 8** | 9 gates, boundaries both sides; `thresholds_signed_off` |
| 2.4 Setup B gates | PASS 8/8 | middle band tested hardest; naked short unreachable |
| 2.5 structure builder | **6 of 7** | real AXISBANK spread built; **ISS-021** |
| 2.6 greeks | PASS 7/7 | matches vollib to 1.8e-15; Dhan delta gap 0.015 mean, real data |
| 2.7 risk gate | PASS 9/9 | 13 checks, property-tested; no confidence input by shape |
| 2.8 costs & dry run | **8 of 9** | lookahead induced; `contract_note` pending (**ISS-023**) |
| 3.1 LLM adapter & cap | **10 of 11** | ₹1 cap blocks with the client never reached; `rates_reconciled` pending |
| 3.2 news agent | **12 of 14** | injection induced in 3 forms; subject router settles 60% of filings; **labels pending** |
| 3.3 four specialists | PASS 15/15 | determinism induced (3 runs identical); naked short and the size-field detector both induced |
| 3.4 strategist graph | PASS 17/17 | resume induced (0 new calls, same answer); reworded prose moves no gate value; checkpoints append-only |
| 3.5 briefing & delivery | **11 of 13** | 9 gate values survive; token-leak induced; `telegram_credentials`, `real_briefing` pending |
| 3.6 decision capture | **11 of 12** | restart-between-tap-and-reason induced; journal shape drilled and rolled back; **0 of 10** briefings answered |

`make check`: **966 passed, 4 contracts kept, mypy --strict clean.**

**Note on reading this table:** the runner used to print `FAIL (1 of 7)` meaning *one
failed*. Both result lines now count passes (ISS-017).

**Token:** re-issued 2026-10-18 16:36 IST, valid 24.0h — **expired ~16:36 today**, so
`make login` before any live call. **Delete `DHAN_ACCESS_TOKEN` from `.env`** — nothing
reads it, and a stale copy there is what made the harness authenticate differently from
the app.

## Open risks being watched

- Chain snapshotter not yet running: **every day of delay is permanently lost IV history**
- Historical option-chain data purchasable from a vendor? Would remove the 6-month proxy
- **ISS-020 open with an unknown mechanism** — one TimescaleDB restore failed and has
  never reproduced. The pre/post_restore calls stay because they are cheap and
  documented, not because they are proven necessary.
