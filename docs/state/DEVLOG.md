# DEVLOG — append-only record of what happened and why

One entry per session that changed something. Newest at the **bottom**, so `tail` gets the
recent past cheaply.

**What belongs here:** decisions a future session would otherwise re-litigate, surprises,
rejected alternatives, and anything that contradicts a plan doc.
**What does not:** what the code does (read the code), routine progress (that's `STATE.md`),
or bugs (that's `ISSUES.md`).

Entry format — keep it to five lines or fewer:

```markdown
## 2026-11-03 · Step 1.3
- **Built:** tick→bar aggregation, reconnect with gap rows
- **Decision:** gaps are recorded, never interpolated. Interpolated bars would make the
  volume gate silently wrong and the backtest optimistic.
- **Surprised by:** Dhan sends a snapshot tick on subscribe; it must not count as volume.
- **Rejected:** storing bars only, no ticks. Ticks are needed to debug the volume gate.
- **Follow-up:** ISS-012
```

### Rollup policy

When a **phase** completes, add a `## Rollup — Phase <n>` entry summarising it in ≤10
lines, and move that phase's individual entries to `DEVLOG-ARCHIVE.md`. The live file stays
short; the archive stays greppable. Without this, the file that was meant to save tokens
becomes the thing that costs them.

The boundary is a phase rather than a calendar month because the work moves faster than the
calendar: phases 0, 1 and 2 all closed inside five days of October 2026, by which point the
file was 134 KB — a single `cat` of it cost more than most tasks. A phase boundary is also
the point at which a session stops needing the detail.

---

## Rollup — Phase 0 (steps 0.1–0.6, 14–16 Oct 2026)

Scaffolding, and the decisions that constrain everything after it. Full entries in
`DEVLOG-ARCHIVE.md`.

- **v1 has no order path.** `TradeProposal` cannot structurally express a size — the single
  rule the rest of the architecture is built to keep.
- **`Money` and `Price` are separate types** though both hold integer paise; rounding is
  ROUND_HALF_UP, never bankers'. `Percent` holds percentage points, not fractions.
- **`domain-is-pure` forbids `time` and `datetime`**, not just I/O: a domain function that
  can read the clock is untestable at a boundary.
- **Ports return domain objects, never vendor payloads**, and the broker port is
  `BrokerReadPort` so adding writes means renaming a file someone has to review.
- **Package is `trading_desk`**, distribution `trading-desk`. Migrations are hand-written
  with `target_metadata = None` — no autogenerate.
- **Rejected:** a lambda/registry for setup gates (returns bare booleans, breaks rule 9);
  dropping log records that contain secrets (redact instead, a dropped record hides the bug).

## Rollup — Phase 1 (steps 1.1–1.9, 16–18 Oct 2026)

Data in, with four findings that invalidated code written the same day. Full entries in
`DEVLOG-ARCHIVE.md`.

- **The RSS feeds were added and removed within hours** once the terms were actually read,
  and RBI was rejected outright despite being live, fresh and useful. Reading terms before
  wiring a source is now the rule; ISS-010 tracks a paid replacement.
- **Gaps are recorded, never interpolated**, and ticks are kept alongside bars because the
  volume gate cannot be debugged from bars alone. Dhan's snapshot tick on subscribe must
  not count as volume.
- **Missing greeks and IV map to `None`, never `0.0`** — an illiquid strike has no IV, and a
  zero would pass a volatility gate.
- **Append-only is enforced by a database trigger**, not by the adapter, and a correction is
  a new row rather than an UPDATE. That changed the `JournalPort` contract.
- **Partitions are keyed by IST trading date, not UTC.** The expiry list turned out to audit
  the holiday calendar, which is now how the calendar is checked.
- **Entity resolution is whole-phrase, never substring** (ISS-012 would have attributed one
  company's news to another). Silent truncation is ISS-009.
- **Surprised by:** three separate verify checks were stale and passing. Verifications are
  now re-run as a sweep, not trusted once.

## Rollup — Phase 2 (steps 2.1–2.8, 19 Oct 2026)

The rulebooks as code: vocabulary, setups, greeks, the risk gate, costs. Full entries in
`DEVLOG-ARCHIVE.md`.

- **Setups, thresholds and costs are config with effective-from dates** (rule 7), so a
  changed lot size is a dated YAML edit, not a diff in gate logic.
- **Every gate returns its measured value and the threshold it compared against** (rule 9).
  No gate anywhere in the project returns a bare bool.
- **The risk gate got the most boring implementation available**, deliberately: it is read
  under pressure during an incident.
- **Costs are modelled in full** — brokerage, STT, exchange charges, stamp duty, GST — so a
  hypothetical P&L is never gross.
- **Surprised by:** `STATE.md` had drifted from the code and was corrected with no code
  change, which is why the phase line in it is now rewritten every session.

## 2026-10-19 — Step 3.1: the LLM adapter and the cost governor

The first step of Phase 3, and the first code in the project that can spend
money on its own. That framing drove every decision below.

**The cap refuses before the call, on a deliberately pessimistic estimate.**
This is the one design choice worth arguing about, so: the true cost of a call
is only known once it has returned, and by then it is spent. A check that
compares *spend so far* against the cap therefore always allows one more call,
and that call is unbounded in size. So `check_cap` compares `spent + estimated`
and refuses if the sum crosses. The estimate counts 3 characters to the token
(English runs nearer 4) and assumes a full-length 2,000-token response, both
wrong in the safe direction. `docs/10` is explicit that the cap must refuse
rather than alert afterwards; alerting afterwards is a receipt.

**`spend_this_month()` came off `LlmPort`.** It was a reasonable guess in step
0.6 and it is wrong: asking a *client* what we have spent gives a different
answer in every process, and a cap that resets when the scheduler restarts is
not a monthly cap. Spend now lives in `ports/llm_ledger.py`, backed by the
`llm_calls` table, and `application/llm_governor.CostGovernedLlm` combines the
two. Ports went 15 -> 17 (`LlmLedger`, `Tracer`); `verify 0_6` updated.

**Structured output is a forced tool call, not a prompt asking for JSON.**
Asking for JSON gets JSON most of the time and an apology wrapped around JSON
the rest. The Pydantic schema goes up as a tool with `tool_choice` pinned to
it. A response that still fails validation **is not retried** - the same prompt
returns the same shape, so a retry spends the call again to receive the same
malformed answer. Retries cover transport and rate limits only.

**Migration 0006 is append-only, enforced by triggers**, the same shape as the
journal in 0005 including the statement-level TRUNCATE trigger. A spend ledger
that can be edited cannot answer "did we stay under the cap in March?", which
is the only question it exists for.

**Two findings worth recording.**

The first is a mistake I made and had to undo. The trigger drill ran against
the real database and inserted a 134-paise row - which, the triggers working
exactly as designed, could not then be deleted. A verification artefact was
permanently in a financial ledger and counting against October's cap. Cleared
by `alembic downgrade 0005 && upgrade head`, which also proved the downgrade
path works. Every database check in `verify 3_1` now runs inside a transaction
that is rolled back, and the ledger reads 0 rows after a full verify run.

The second is the familiar one. `verify 3_1` first printed the per-node report
as rupee figures - which came from `FakeLlm`'s flat 50-paise-per-call constant,
not from pricing, sitting beside the genuinely priced ₹13.35 / ₹667.50 tiering
line. That is the third time this session's family of mistakes has appeared:
a fixture constant printed as though it were measured. It now prints call
counts and says in the line itself that it checks grouping, not pricing.

**Prices are real, the cap and the FX rate are not.** The per-million rates in
`config/llm_pricing.yaml` were fetched from Anthropic's published pricing page
today, so triage on Haiku 5.5 is $0.10/$0.50 and synthesis on Opus 5 is
$5/$25 - measured 50x apart at the same token counts, which is what justifies
having two tiers at all. The ₹2,000 cap and the 89 INR/USD rate are both
placeholders. `verified: false`, so `verify 3_1` fails on it by design until
one month's ledger total has been compared against the Anthropic console:
published rates prove the prices are right, only the console proves our
*accounting* of them is.

**Langfuse is not wired, deliberately.** `docs/10` names it; it needs a hosted
account or a self-hosted stack, and that is a signup decision rather than
mine. The requirement it exists to serve here is `docs/12` step 3.4 - walk back
from any briefing line to the calls behind it - and `adapters/tracing/jsonl.py`
satisfies that today at no cost, with no third party holding our prompts.
Swapping in Langfuse later is one class against `ports.tracing.Tracer`.

Also: `claude-opus-5-5` is both newer and cheaper than the `claude-opus-5` that
`docs/10` names ($4/$20 against $5/$25). Left as the plan specifies rather than
swapped silently (rule 10); it is a one-line config change whenever Ajay says.

`verify 3_1`: **10 of 11**, the failure being `rates_reconciled_with_console`.
`make check`: 712 passed (up from 673), 4 contracts kept, mypy clean.

## 2026-10-19 — Step 3.2: the News & Sentiment agent

`docs/10` asks for six sub-agents. **Four of them already existed** and are
deterministic domain code from step 1.5: the ingestor (`application/news_ingest.py`),
the deduper (`domain/similarity.py`), the entity linker (`domain/entities.py`) and
the injection inspector (`domain/untrusted.py`). What was genuinely missing was the
catalyst classifier, the credibility scorer, the impact analyst, and the means to
score any of it.

**Credibility is rules, not a model, and that was a decision rather than a
shortcut.** `docs/12` asks Ajay to read twenty credibility scores with their stated
rules and say whether he agrees. That is impossible if the number came out of an
LLM: you cannot audit a score whose reasoning is re-derived each time, and "the
model thought 0.62" is not a rule anyone can disagree with. So `domain/credibility.py`
is arithmetic over a source tier and some phrasing patterns, and every score carries
the rules and the matched words that produced it.

The load-bearing part is the **hedging ceiling**. `docs/06` says "sources say"
stories are capped below 0.7 and cannot trigger alone. Implemented as a ceiling
applied *last*, not as a penalty - measured in `verify 3_2`: a hedged story with
every available bonus (named attribution, a cited filing, ten corroborating
publishers) still scores **0.65** against Setup A's 0.70, while a clean Tier-1
story scores 0.85. A penalty can be out-earned; a ceiling cannot.

**Stage order is a cost decision as much as a correctness one.** Deduping and
entity-linking happen *before* classification, so three outlets carrying one
announcement cost one model call rather than three (measured: 3 articles in, 1
story, 2 calls), and an article naming no universe instrument costs **zero** calls.
At a few hundred articles a day that is the difference between a few hundred calls
a month and a few thousand.

**Two guards on the classifier that the prompt cannot provide.** The response
schema admits only the closed seven-type vocabulary plus NONE, so an eighth
category cannot be returned - it fails validation instead. And the evidence quote
is checked against the article text: a quote that is not in the source means the
model wrote the sentence rather than found it, and the label is downgraded to NONE
with the reason recorded. That catches a confident fabrication without having to
judge whether the classification happened to be right.

**Injection fixtures in three forms**, as `docs/12` asks: in the body, in the
headline, and inside a quoted statement. The third is the realistic one - corporate
announcements legitimately contain quotes, so the attacker does not have to make
the text look odd, only to be quoted saying it. All three are flagged, none changes
behaviour, and the article is still classified rather than filtered: dropping it
would discard the evidence that someone tried. The headline goes *inside* the fence
with the body for the same reason - a headline feels like metadata and is neither
shorter nor safer.

**The accuracy check cannot be satisfied by an accuracy number.** `docs/12` is
explicit that accuracy hides a classifier that never predicts the rare catalyst
types. `verify 3_2` induces exactly that failure: an always-NONE classifier is fed
in, scores **90%**, and the report names both never-predicted classes rather than
passing it.

**New: `ports/llm.GovernedLlmPort`.** Agents may import domain and ports only, so
depending on `application.llm_governor` directly would have left that contract
technically unviolated but dishonest. It is deliberately *not* in the step 0.6
fakes inventory and has no fake, with the reason written at the inventory: the
agent tests build a real `CostGovernedLlm` over `FakeLlm`, which exercises the cap
and the ledger for free, and a fake governor would make them weaker.

Also added `NewsStorePort.all_articles(limit)` - the store could write articles but
not read them back, so there was no way to draw a labelling sample. Oldest-first,
because a sample of the newest articles is a sample of whatever happened this week
(one earnings season, no rating actions) and a classifier tuned on that learns the
week rather than the task.

**`trading-desk label-news --count 200`** writes the blank labelling sheet. It is
dispatched *before* the Dhan settings load: a labelling command that needs trading
credentials is one that cannot be run on a laptop without them. The sheet is
shuffled by a stable hash rather than ordered by date, because 200 articles in date
order are read in topic clusters and by item 40 the labeller is answering from
momentum. The same hash decides the held-out split, so membership never drifts as
the set grows - which is what makes "never looked at while tuning" enforceable
rather than a promise. Writing refuses to overwrite an existing file.

`verify 3_2`: **10 of 12**. Both failures are the labelled set, which is Ajay's.
`make check`: 757 passed (up from 712), 4 contracts kept, mypy clean.

## 2026-10-19 (continued) — Step 3.2 found that the archive cannot support it

Two findings while generating the labelling sheet from real data, and the second
one is the most consequential thing in Phase 3 so far.

**The exchange had already classified most of the archive.** NSE filings carry a
`subject` from a controlled vocabulary — 60 distinct values across 1,542 filings.
Measured: the subject alone settles **60% of them** (718 routine + 207 catalyst),
at zero model cost. `domain/filing_subjects.py` is that lookup, wired in front of
the classifier in `CatalystClassifier._by_subject`. Paying an LLM to re-derive
"Credit Rating" from a filing titled "Credit Rating" is a worse answer at a price.

The important design detail is the **three-way** return: a catalyst type, or NONE,
or `None` meaning "the subject does not decide — read the document". Collapsing the
last two would have been the serious error. "Outcome of Board Meeting" is not
routine, it is *undetermined*, and routing it to NONE would silently discard every
earnings announcement in the archive with nothing anywhere reporting an absence.
`verify 3_2` checks that specific mistake rather than trusting the map.

**Then: the filings have no content (ISS-025).** Body length is min 17, mean 144,
max 879 characters — stubs reading "X has informed the Exchange about Y". The real
content is a PDF we record a URL for and never fetch. So the 617 filings that
"need the document" have no document, and the classifier can only honestly answer
NONE. `EARNINGS_SURPRISE` and `GUIDANCE_CHANGE` — the two types Setup A was
designed around — are **structurally unreachable** from what is stored. No
classifier tuning fixes that; the information is not in the database.

I checked rather than assumed the fix is available: **1,524 of 1,542 filings carry
a real nsearchives.nseindia.com URL, and 100% of the "needs reading" subjects do.**
Nothing needs re-crawling.

**The labelling sheet was wrong on the first attempt and I regenerated it.** A
uniform draw over this archive is 93% compliance boilerplate, because that is what
the archive is — five subjects are over half of it. Ajay would have labelled 200
rows of "Certificate under SEBI (Depositories and Participants) Regulations" and
the resulting test set would have been one a constant could pass. `stratified_sample`
now round-robins across subject strata: 97 distinct subjects across 200 rows instead
of a handful.

That trade is stated in the module docstring rather than left implicit: **a
stratified set deliberately over-represents the rare types, so its accuracy number
is not the production accuracy.** It is the right trade — per-class recall is what
`docs/12` asks for and a uniform sample cannot give it — but the headline percentage
must never be quoted as "how often the classifier is right on a normal day".

Also added `NewsStorePort.all_filings` and `domain/news.filing_as_article`, because
the news agent was only ever going to see the 25 RSS articles while 1,542 filings
sat unread beside them. Filings are the *primary* catalyst source — an order win
arrives as a filing first and as journalism afterwards — and they carry
`SourceTier.EXCHANGE`, which the credibility scorer already scored at 1.0, so the
plumbing was half-built and pointed nowhere.

`verify 3_2`: **12 of 14.** Both failures are the labelled set.
`make check`: 771 passed, 4 contracts kept, mypy clean.

## 2026-10-19 (continued) — Step 3.3, scope decided before the code (rule 10)

Four specialists to build: fundamental, technical/quant, options strategist,
portfolio context. `docs/10` is explicit about their shape — "the agent gathers
inputs, the gate decides" — so the design question is not what each one *thinks*
but what each one is allowed to *touch*.

**Decision 1: three of the four make no model call at all.** The news agent needed
two, because classifying free text is a judgement. These four are not. Fundamental
reads a controlled vocabulary (the same NSE subject field that settled 60% of the
archive in 3.2), technical is arithmetic over bars, options is arithmetic over a
chain plus a documented structure matrix, portfolio is arithmetic over positions.
Adding a model to any of them would buy a worse answer at a price, and — given
ISS-025, where filing bodies average 144 characters — would mostly be asking a
model to reason about a stub. The agents are still agents: they decide what to
gather, they call gate functions, they report what they could not get. They just
do not need a model to do it. This is a departure from `docs/02`, which implies
an LLM in the technical and options agents, so it is written here rather than done
quietly.

**Decision 2: a specialist never invents a number, and never half-reports one.**
Each returns a typed view carrying `measurements` (rule 9: values, not verdicts),
`vetoes`, and `unavailable` — the inputs it could not get, with the reason. An
agent that cannot compute net delta says so; it does not return a partial sum that
reads like a real one (rule 6).

**New, with the reason:**

- `domain/specialists.py` — `SpecialistFinding`, the common shape above. One type
  so the 3.4 graph can merge four answers without four special cases.
- `domain/governance.py` — the veto table. NSE subject → governance flag, measured
  against the 1,542 filings held today, same method as `domain/filing_subjects.py`.
- `domain/options/selection.py` — the structure matrix from `docs/02` ("high IV
  rank into an event → defined-risk credit; low IV with a catalyst → debit") as
  code, because `docs/02` says to encode it rather than prompt it.
- `BarStorePort.daily_bars` — ATR, range and volume ratio need OHLCV, and the port
  only exposed closes and dates. A port method, not a new port.
- `NewsStorePort.filings_for` — the fundamental agent asks about one symbol; it
  had only `all_filings(limit)`, which means reading the archive to answer a
  question about one name.

**Not built, deliberately:** no Risk Narrator (it is step 3.5's, and it has no
approval authority), no position manager, no sector concentration — there is no
sector map anywhere in `config/`, and inventing one would be exactly the kind of
plausible-looking data these rules exist to keep out.

## 2026-10-19 (continued) — Step 3.3 built: four specialists, no model calls

`verify 3_3`: **15 of 15.** `make check`: 849 passed (up from 771), 4 contracts
kept, mypy clean.

**What the four actually are.** Fundamental reads one company's filings and
answers "is there a reason to leave this alone"; technical measures four
registered signals over bars; options picks an expiry, measures the chain and
builds a vertical through the encoded volatility matrix; portfolio sums what is
already held. Each returns a `SpecialistFinding` carrying measurements, vetoes
and — the field that earns its place — `unavailable`.

**The determinism check from `docs/12` runs now rather than at 3.4.** Each
specialist, three runs, same inputs: every measured value byte-identical. It is
nearly free here and would be expensive to retrofit once the graph exists, and
`docs/12` calls a drift in gate values "the most important invariant in the
system".

**Two findings, both measured, both now blocking on Ajay:**

- **ISS-026 — the Fundamental agent cannot see its own headline case.** `docs/02`
  names pledged shares as the primary disqualifier. **0 of 1,542 filings mention
  pledge, encumbrance or promoter.** Those arrive under SEBI SAST/PIT on an NSE
  endpoint step 1.5 does not poll. The agent reports the blind spot in
  `unavailable` on *every* call, including clean ones, so a silence cannot be
  read as a clean record — but the veto is weaker than `docs/06` assumes.
- **ISS-027 — there is no sector map.** 9 config files, none naming a sector. So
  the "seventh correlated long Nifty-bank bet" that `docs/02` names as the
  classic agentic failure is exactly the one this agent cannot catch. Writing a
  25-name sector map from memory would have looked authoritative and been
  unsourced; NSE's own sectoral index membership is the free, published answer
  and is one config file away.

**A third gap, smaller: `config/setups.yaml` gives Setup B's debit variant no
delta bands.** The credit variant has a short-leg band and both variants have
expiry windows, but nothing says which strikes a Setup B debit spread buys. The
agent refuses that path with the reason attached rather than borrowing Setup A's
bands, which would have been a silent reuse of numbers chosen for a different
instrument. The credit wing offset is in the same position and is a constructor
argument pending sign-off.

**Setup A never sells premium, swept rather than spot-checked.** 101 IV ranks,
0 credit structures, 61 debit, 40 refusals at the configured ceiling of 60. The
matrix in `domain/options/selection.py` takes `credit_above=None` for Setup A,
which says "this setup has no credit answer" rather than "the threshold is very
high" — a distinction that shows up in the refusal text a reader sees.

**One test was wrong before it was right, and the wrongness is the useful part.**
`test_a_chain_with_no_wing_refuses_rather_than_going_naked_short` passed
immediately, which was suspicious: it was refusing because the *short* leg was
unpriceable, not because the wing was missing — the FR-5.9 path was never
reached. Rebuilt with a chain whose short leg is priced and whose lower strikes
are removed, it now fails for the right reason and asserts on the words "naked
short". Same shape as ISS-018: a check that reported the right answer for the
wrong reason.

**The quantity introspection had the same problem and was caught the same way.**
The first version read `field.type`, which is a *string* in every module using
`from __future__ import annotations` — so it saw one level and reported a clean
sweep. It now resolves hints and descends through generics, reaching 15
dataclasses, and proves itself against a planted nested `quantity` field in the
same call. A detector that has never detected anything is not evidence.

**Port additions** (both method-level, no new port, so 0.6 still reads 17/17):
`BarStorePort.daily_bars` because closes alone cannot give an ATR, and
`NewsStorePort.filings_for` because the fundamental agent asks about one name.

Unrelated note for the next session: `verify 1_4`'s `close_matches_an_independent_endpoint`
reads FAIL today (daily close ₹23,146.15 against live chain spot ₹23,119.00). That
check compares a stored bar against a *live* feed, so it drifts with the market and
is only meaningful on a trading day — nothing in 3.3 touches that path.

Also tidied `ISSUES.md`: ISS-020 to ISS-025 had been appended below the
`## Closed` heading, so the section the session protocol tells you to read as
"open items only" was missing six open items. Moved, not rewritten.

## 2026-10-19 (continued) — Step 3.4, scope decided before the code (rule 10)

Three departures from `docs/10`'s task list, all in the same direction, all
written here before anything was built.

**1. No LangGraph.** `docs/10` names it. What 3.4 actually needs is a static
six-node DAG with one fan-out, a typed state object, and a resume path — and the
resume path we need is an unusual one (see 3 below), so the checkpointer that
would be the main reason to take the dependency is the part we cannot use as
shipped. Against that: LangGraph pulls `langchain-core` and its own checkpoint
packages into a tree that is `mypy --strict` clean today, for a graph that is
about 150 lines of plain Python. CLAUDE.md 4b says ship the version needing less
thought. **The graph is behind one module (`agents/strategist/graph.py`), so
swapping LangGraph in later changes one file** — if Ajay wants the named stack,
say so and it is a contained change rather than a rewrite.

**2. The fan-out is sequential, and the reason is the cap, not laziness.** The
five specialists are independent — no node reads another's output — so the shape
is a fan-out and the merge is deterministic. Running them on threads would buy
milliseconds on four arithmetic agents, and would introduce a genuine hazard on
the fifth: **two concurrent paid calls can both pass a cap check that neither
would pass second**. `domain/llm_cost.check_cap` compares spend-so-far against
an estimate, and spend-so-far is read before the call. Serialising the paid work
is what makes the cap a limit rather than a race.

**3. Resume is at the *paid* boundary, not the node boundary.** `docs/10` asks
for per-node checkpointing so a crash resumes "without re-running completed
nodes". Serialising each specialist's output would mean a codec per view type,
and would buy nothing: those four nodes are arithmetic, and `verify 3_3`
measured them byte-identical across three runs. What must never repeat is a
**paid model call**. So a cycle records every node run (for the delegation tree,
FR-4.3) and caches every model response against `(cycle_id, call_key)`; a
resumed cycle re-runs the free work, replays the paid work at zero cost, and
produces the identical result. The guarantee delivered is therefore "**a resumed
cycle repeats no paid work and reaches the same answer**", which is stronger
where it matters and weaker where it does not. Stated plainly rather than
claimed as the literal wording of FR-4.12.

**One thing added rather than removed: the strategist has an LLM node.** Exactly
one, on the synthesis tier, called at most twice per cycle — only for ideas that
have already passed every gate. It writes the thesis claim and the invalidation
condition as prose and **can return nothing else**: no direction, no number, no
level, no size. The gates decide; the model writes the sentence. This is what
`docs/12`'s determinism boundary assumes when it says "the *prose* will differ;
the **gate values must be identical**", and it is the first use of the Opus tier
that step 3.1 built.

**New, with the reason:**

- `ports/cycle_store.py` — the durable memory of one cycle: node runs and
  cached model responses. One port, because both answer "what has this cycle
  already done?" and splitting them would mean two stores to keep consistent.
- `domain/ranking.py` — ranking to at most two, deterministic, with the
  tie-break explicit. **There is no evidence that this ordering is predictive**
  and the module says so; it is a documented rule, not a model.
- `domain/sessions.sessions_between` — the catalyst-recency gate counts trading
  sessions, and the only calendar lives in `application/`, which agents may not
  import. The pure function takes the holiday set as an argument.
- `domain/call_identity.py` — one definition of "the same model call", shared by
  the response cache and the recorded-cassette client, which had its own copy.
- Migration **0007**: `cycle_nodes` and `cycle_responses`, append-only like the
  journal and the ledger.

## 2026-10-19 (continued) — Step 3.4 built: one cycle, six nodes, at most two ideas

`verify 3_4`: **17 of 17.** `make check`: 906 passed (up from 849), 4 contracts
kept, mypy clean. Migration **0007** applied.

A full cycle now runs end to end against fakes: 2 articles → 1 story → 1
candidate through 9 gates → 1 proposal, with a thesis, an invalidation, a stop,
a target and every gate value attached.

**The check worth reading is `different_prose_does_not_move_a_gate_value`.**
`docs/12` asks for three identical runs; three identical runs only prove the
code is deterministic. So there is a second, stronger form: run the same cycle
with the model made to write **different wording about the same facts**, and
require all nine gate values to be byte-identical while the claim changes. That
is the actual invariant — the model cannot reach the decision path — and the
weaker version would pass even if it could.

**Resume is induced, not asserted.** The resumed graph's model has *nothing
queued*, so any real call raises an AssertionError from `FakeLlm`. It does not
raise: 3 paid calls on the first run, 3 replayed on the second, 3 ledger rows in
total, identical proposal id and identical gate values. If the replay path broke,
this check would fail loudly rather than quietly re-charging.

**What the graph refuses, measured:**

- `no ideas` carries a reason that counts the killers ("1 candidate(s), all
  rejected: iv_rank (1)"), never a bare empty list (FR-4.9).
- Three tradable names produce **two** proposals and one rejection with
  `killed_by="rank"` — passing every gate and losing on rank is a different
  thing from being killed, and it is shown as such (FR-4.10).
- A single-publisher story **cannot become a thesis** (FR-4.6). On today's
  archive — exchange filings nobody has reported on yet — this is the common
  case, not the edge case, and it is worth expecting the briefing to be quiet
  for that reason rather than for a bug.
- An invented figure drops the idea and names the number. The synthesis model
  wrote "rise 37%" in the fixture; `domain/grounding.py` caught the 37.
- A ₹1 cap stops the cycle **before** the first node, with 0 calls made.

**A third new finding, ISS-028, and it is the uncomfortable kind.** Two of Setup
A's calendar inputs — the F&O ban list and the earnings calendar — have no source
at all, and `SetupACandidate` types them as a plain `bool` and an optional date.
So they default to `False` and `None`, which are the **permissive** answers: the
calendar gate reports PASS on a banned symbol. This is the only fail-open input
in the gate chain; everywhere else a missing input blocks. The mitigation is
disclosure, not a fix — every cycle reports both as missing and `verify 3_4`
fails if it stops.

**Two things I got wrong building the test harness, both the same mistake.**
The multi-symbol fixtures tagged every article `instrument_symbols=("RELIANCE",)`
regardless of company, so the deduper — which partitions by symbol before it
compares text — correctly merged six articles about three companies into one
story. Then, with that fixed, the three stories still merged, because they were
the same sentence with the company name swapped. Both times the code was right
and the fixture was wrong, which is the good direction, but it is worth writing
down that a templated fixture is a bad test of a deduper: it will either merge
everything or nothing, and neither tells you about real copy.

**Not built, with reasons:** no CLI command to run a cycle (step 3.5 delivers
the briefing and will own the entry point), and the journal still has no table
for rejected candidates — `FR-4.10` says rejections appear "in the briefing and
the journal", and only the briefing half is reachable today. Noted here rather
than widened into 3.4.

## 2026-10-19 (continued) — Steps 3.5 and 3.6, scope decided before the code (rule 10)

Built together because they are one loop: a briefing nobody answers is the
failure `docs/10` calls "the weakest link in v1", so the thing that asks and the
thing that listens are designed in one go.

**Telegram confirmed as the surface**, after Ajay asked about a web app. The
reasoning is in `docs/11` §1 and still holds: it pushes, it works on hotel wifi,
it needs no auth or session handling, and the message trail *is* the audit
trail. A read-only web dashboard stays where `docs/11` §4 puts it — v1.5, FR-15.

**Decision 1: the reason is captured by `force_reply`, not by session state.**
Telegram inline buttons cannot collect free text, so a tap has to be followed by
a typed reason. The obvious design keeps "which idea is awaiting a reason" in
memory, and loses it on every restart - silently, which is the worst way to lose
a decision. Instead the prompt message carries the idea id in its own text and
asks for a reply to itself; the user's reply quotes it, so the idea id comes back
with the answer. **The message trail is the state**, which is exactly the
property `docs/11` credits Telegram with. Nothing to persist, nothing to lose on
a restart, and a decision made at 09:00 can still be answered at 14:00.

**Decision 2: the briefing is more than one message, and the order is fixed.**
`docs/12` asks for two things that fight on a phone - act on it without scrolling
past the decision, *and* find every gate value. Telegram's limit is 4096
characters and two ideas carry eighteen gate values between them. So the first
message is the decision (market view, the ideas, their key numbers, the buttons)
and the detail follows behind it. Splitting is the **adapter's** job, because
4096 is a Telegram fact and the domain should not know it.

**Decision 3: no hypothetical size until there is a capital figure.** FR-6.2
wants the advisory risk gate's size in every briefing. The gate needs equity and
`docs/07` is blank (ISS-022). Rather than default to a plausible number, the
briefing prints "size unavailable: no capital figure (ISS-022)" and the section
stays in place so the absence is visible every single morning. A new
`config/risk.yaml` holds the figure with `verified: false`, so filling it in is
one line in one file and the briefing starts showing sizes.

**New, with the reason:**

- `ports/chat.py` — `ChatPort`, the **inbound** half. `NotifierPort` is one-way
  by design and 3.6 needs to listen. Long polling (`getUpdates`), not a webhook:
  a webhook needs a public HTTPS endpoint, which is a server, a certificate and
  an open port for a laptop that already reaches out fine.
- `NotifierPort.ask_decision` — "ask the user to decide about this idea" is a
  delivery-agnostic idea; inline keyboards are how Telegram spells it.
- `domain/briefing.py` — the five sections of `docs/06` §3 as data plus a pure
  renderer. No I/O, so the exact text is testable without a network.
- `domain/capture.py` — encoding and parsing a decision, and the completeness
  arithmetic ("you answered 4 of 5").
- `config/risk.yaml` — see decision 3.

**Not built:** `/kill` and `/flatten` (v2, there is no order path to kill),
approval cards (v2), and the weekly performance report (FR-7.11, a SHOULD that
belongs with calibration in 3.8).

## 2026-10-19 (continued) — Steps 3.5 and 3.6 built: the briefing, and answering it

`verify 3_5`: **11 of 13.** `verify 3_6`: **11 of 12.** `make check`: 966
passed (up from 906), 4 contracts kept, mypy clean. **19 ports, 19 fakes.**

The three failures are the three things that need Ajay rather than code: the
Telegram credentials, one real briefing received, and ten answered briefings.

**What the briefing measures today, from a real cycle:** all five sections
present, 2,153 characters in total; the decision message is **614 characters
over 12 lines** and carries the structure, the size line and the invalidation;
all **9 gate values** survive the trip with their measured numbers; 2 evidence
lines each with a publisher, a URL and a timestamp; cost ₹1.50 across 3 nodes.
The detail fits one message today and splits automatically when it does not.

**The size line is the part I expect to matter most.** With no capital figure
it reads "unavailable - no capital figure in config/risk.yaml (ISS-022)", and
it reads that **in the first message**, not in the detail. With ₹42,00,000
configured the same briefing says "1 lot(s), risking ₹5,500 of a ₹10,500
budget (advisory)". An absent number that is mentioned every morning gets
filled in; one that silently disappears does not. `config/risk.yaml` is new and
exists so filling it in is one line in one file.

**Decision capture keeps no session state, and that is the whole design.** A
tap sends a prompt whose *text* carries the idea id, and the reply quotes it
back. So the message trail is the state: a restart between the tap and the
reason loses nothing, and `verify 3_6` induces exactly that - a second
`DecisionCapture` instance completing a tap the first one asked about, two
hours later. The obvious design (a dictionary of who owes a reason) passes
every test until the first restart and then drops a decision silently, which
is the worst available failure for the one dataset this project is built to
collect.

**Measured, and worth keeping an eye on:** the longest button payload is **47
bytes of Telegram's 64**, so all three actions fit with room. If a fourth
action is ever added, that number is the constraint.

**Plain text, no parse_mode.** MarkdownV2 wants `. - ( ) ! +` escaped and the
briefing is made of them - "₹10,500.00", "0.52 x ATR", "(proxy)". One missed
escape is an HTTP 400, which is a briefing that did not arrive, which is the
single failure this step exists to prevent.

**Rule 8 has a new sharp edge and it is now induced.** Telegram puts the bot
token **in the URL path**, so any code that echoes a URL into a log or an
exception leaks a credential. `TelegramClient` never includes the URL in an
error - errors name the method - and both `verify 3_5` and the adapter tests
assert the token does not appear in a refused call's message.

**Two honest gaps the briefing now declares out loud:**

- `UnlinkedBroker` - v1 has no broker sync (step 1.10) and every trade is
  placed by hand, so the system genuinely does not know what is held. The
  class is named for that, and every cycle wired to it adds a line to the
  briefing's missing-inputs section. The dangerous version returns empty lists
  and lets the briefing print "0 open positions", which is a claim about the
  account rather than about our ignorance.
- No IV rank is wired into `trading-desk brief` yet: `application.iv_rank`
  needs the chain archive and wiring it belongs with the scheduler in 3.7.
  Until then every idea dies at the volatility gate, visibly, in the rejected
  list.

**One thing the drill caught that the in-memory tests could not.** `verify 3_6`
writes a real decision into `briefing_ideas` and `user_decisions` inside a
rolled-back transaction, and the first two attempts were rejected - once for
`used_iv_proxy` and once for `recorded_at`, both NOT NULL with no default. A
decision that passes every in-memory test and then fails on a column type is a
decision lost at 09:00 on a Monday, so the shape check earns its place.

**Not built:** `/status` and `/why <idea_id>` (docs/11 §3 lists them for v1;
they are read-only conveniences and neither blocks capture), the weekly
performance report (FR-7.11, a SHOULD that belongs with calibration in 3.8),
and anything from the v2 column.

## 2026-10-19 · Step 3.7 — scope, written before the code (rule 10)

- **Building:** `application/scheduler.py` (08:15 brief, 10:00 nag, a capture poll,
  15:45 review, the three snapshot times, nightly backup and expiry refresh — all
  trading-day aware, each run guarded and alerted on failure),
  `application/review_cycle.py`, and the IV-rank wiring the briefing has been
  missing since 3.5.
- **Scope widened on purpose, three ways.** (1) A **job-run log** — `ports/job_log.py`
  plus a JSONL adapter, mirroring `ports/tracing.py`. `docs/12` asks for "five
  consecutive unattended sessions, verified from logs, not memory", and there is no
  log today that records a job ran, was skipped for a holiday, or failed. (2) A
  **port method**, `JournalPort.ideas_without_outcome`: the 15:45 review needs every
  unresolved idea, and `ideas_awaiting_decision` returns only the *undecided* ones, so
  a taken idea would never be reviewed. (3) **Migration 0008**, three nullable columns
  on `briefing_ideas` — `stop_level_paise`, `target_level_paise`, `expiry_date`. The
  numbers exist on `TradeProposal` and are currently persisted only as English inside
  `gate_values` ("₹1,400 - below the long call strike…"). A review that parsed that
  prose back into a number would be the worst code in the repository, and 3.8 needs
  the same three columns to score anything.
- **Decision: the 15:45 review reports, it does not record outcomes.** `docs/10` gives
  the resolution rules per setup and hypothetical P&L after full costs to **3.8**, and
  `outcomes` is append-only. A placeholder P&L written today is permanent and
  indistinguishable from a measured one later, which would quietly poison the only
  dataset this project exists to build. So 3.7 states each open idea's standing
  against its own levels and leaves the row unwritten.
- **Decision: `domain/review.py` holds the standing comparison**, not the review
  cycle. It is pure (close, stop, target, direction, expiry, today → a standing) and
  direction-sensitive: for a bearish idea target sits *below* stop, so a
  direction-blind `close >= target` reads correct on every bullish test and never
  fires on a bearish one. That failure is induced in the tests, not assumed.
- **Not building:** outcome scoring and calibration (3.8), `/status` and `/why`
  (FR-9.5, read-only conveniences), deployment (3.9).

## 2026-10-19 · Step 3.7 — the scheduler and the post-close review

- **Built:** `application/scheduler.py` (a job table plus the guard every job runs
  behind), `application/review_cycle.py`, `domain/review.py`, `domain/schedule.py`,
  `ports/job_log.py` with a JSONL adapter, migration 0008, and `trading-desk review`.
  **The IV-rank service is now wired into `brief`**, so the volatility gate finally
  receives a number. `make check`: 1,020 passed. `verify 3_7`: **20 of 21**, the one
  failure being the five-unattended-sessions count, which reads 0 until it runs.
- **Decision: the guard writes to disk before it writes to the network.** If Telegram
  is the thing that is down, it is down at the same moment the job fails, so alerting
  first loses the record of the original failure as well - and then nothing anywhere
  says the 08:15 job died. `verify 3_7` induces exactly that: notifier down, one
  FAILED row on disk, zero alerts delivered. For the same reason the job log is a
  **file and not a table**: "Postgres was unreachable at 08:15" is precisely the
  morning it exists to explain, and a log living in Postgres cannot explain it.
- **Decision: the streak is two numbers, not one.** Five consecutive sessions that
  ended three weeks ago is not a working scheduler, and a job that ran this morning
  after a month of nothing is not five sessions. `SessionStreak` carries the length
  *and* how many sessions have passed since the latest run, and it is measured from
  the latest run rather than from today so a check at 07:00 does not score a healthy
  scheduler at zero for not having briefed yet.
- **Surprised by:** the capture poll would have written **2,880 lines a day** to the
  job log - one per 30-second fire, and on a holiday 2,880 identical SKIPPED lines
  burying the single row anyone actually wants. Polls now record failures only
  (`PollingJob.log_runs=False`), and the poll stopped being trading-day gated in the
  same change: a briefing sent on Friday is often answered on Saturday, and a tap
  nobody collects is a decision lost.
- **Surprised by:** `briefing_ideas` is still **empty** (0 rows), so migration 0008's
  nullable levels have no legacy rows to be null for. The honest-absence handling in
  `domain/review.py` is therefore unexercised by real data and stays anyway - it is
  what the column shape promises, and the first row written from here on carries all
  three numbers.
- **Rejected:** parsing the stop out of the prose already stored in `gate_values`
  ("₹1,400 - below the long call strike…"). It would have avoided a migration and
  would have been the worst code in the repository. The numbers were on
  `TradeProposal` all along and 3.8 needs the columns regardless.
- **Rejected:** placeholder nightly jobs that log "not wired yet". A timetable entry
  that fires and does nothing is worse than either alternative, so the backup and the
  expiry refresh are wired to the same services `make backup` and step 1.8 use.
- **Follow-up:** step 3.8 turns "ready to resolve" into an `outcomes` row with P&L
  after full costs. Until then the review says, every day, that nothing is scored yet.

## 2026-10-19 · Context cost — the state files were the second-biggest spend

No project code changed. A usage report showed 89% of spend above 150k context and 14% on
always-on context over 40k, so the always-on footprint was measured rather than guessed.

- **Measured:** `CLAUDE.md` + memory + skill descriptions is ~3.3k tokens, not 40k. The 40k
  was an account-level `small-business` plugin loading ~45 MCP tool schemas and a Shopify
  instruction block into a Python trading repo. Disabled in `~/.claude/settings.json`
  (`enabledPlugins`) — user-level, nothing in this repo. Measured usage count: **0**.
- **Decision — rollups are per phase, not per month.** The policy in `docs/13` said month
  end. Phases 0, 1 and 2 all closed inside five days, so `DEVLOG.md` reached **134 KB**
  before the first rollup was due and a single `cat` cost ~34k tokens. Phases 0–2 moved to
  `DEVLOG-ARCHIVE.md` behind three ≤10-line rollups; the live file is **46 KB**. Content
  verified byte-for-byte: every entry heading present, delta 25 chars (the split heading).
- **Decision — `ISSUES.md` opens with an index.** 22 open issues at ~20 lines each is ~500
  lines read every session to answer a question the one-liner answers. The table is
  generated from the file's own `Status`/`Owner` fields rather than written by hand, so it
  cannot drift into a paraphrase. Closed issues moved to `ISSUES-ARCHIVE.md`.
- **Surprised by:** `session-start` ran `sed -n '/^## Open/,/^## Closed/p'`. Removing the
  `## Closed` section turned that range into "print to EOF" — the saving would have become
  a 38 KB read. Caught by running the command, not by reading it.
- **Surprised by:** six issues filed under `## Open` carry `Status: closed`, and **ISS-010
  says closed while `STATE.md` lists it as blocked on the user.** Not re-triaged — one of
  the two is wrong and it is not mine to decide which. Left visible in the new index.
- **Surprised by:** the `STATE.md` verification sweep was stale — 966 passed, 19 ports, no
  3.7 row. Corrected while compressing it. `STATE.md` is 262 lines against its own ~40-line
  spec; the rest is the "Blocked on you" list, which is the user's to shorten.
- **Rejected:** trimming `CLAUDE.md`. It is the prompt-cache prefix, it is already ~1.7k
  tokens, and editing it mid-session invalidates everything behind it.

## 2026-10-19 · Step 3.8 — scope, written before the code (rule 10)

Reading `docs/06` §2 before writing anything turned up the thing that shapes this step:
**the documented exit rules are on the spread's premium, not on the underlying's level.**
Setup A is "target 60% of spread width · stop at 50% of debit lost · time stop 5 days to
expiry"; Setup B credit is "exit at 50% of credit captured · stop at 2x credit received".
Step 3.7's review compares the underlying's close to a stop and target `Price`, which
answers *where is this idea now* and is the right thing to put in a 15:45 message — but it
is **not** the resolution rule, and scoring on it would score a different strategy from the
one in the rulebook.

What that forces, and is therefore in scope:

- **`domain/scoring.py`** — the per-setup exit rules expressed on premium, resolution into
  `TARGET | STOP | TIME_STOP | INVALIDATED`, and hypothetical P&L after full costs using
  `domain/costs.py` with the **real exit premium**, not the entry premium reused (which is
  what `round_trip_cost` does today, and its docstring says the caller must recompute).
- **Exit numbers go to `config/setups.yaml`** with the existing `effective_from`, not into
  the code (rule 7). Setup A's five-day time stop is in `docs/06` and in **no config file**
  today, which is exactly the kind of number that drifts.
- **Migration 0009: the legs, with their entry premiums.** A `RecordedIdea` has no legs —
  only `structure` as prose — and `ProposedLeg` has no premium at all, deliberately, because
  a proposal is research. But a P&L cannot be computed from "bull call spread 1400/1440", so
  the premium observed at briefing time is recorded on the journal row. **The premium is an
  observation, not an instruction**, and it goes on the journal, never on `TradeProposal` —
  rule 1 is about what the system may tell you to do, and this is a record of what a price
  was. Same shape of problem as 0008 and the same answer.
- **`ChainArchivePort` gains one read method** (`premium_on`) so an exit premium can be
  recovered from the Parquet archive. A port widening, recorded here.
- **`domain/calibration.py`** — Brier score, reliability curve, resolution, against a naive
  base-rate baseline. Proven against the known case `docs/12` specifies before being pointed
  at anything real.
- **`domain/sizing_lock.py`** — the lock, returning a measured value not a boolean (rule 9),
  plus a structural test that confidence cannot reach sizing (FR-7.8).
- **`domain/monitors.py`** — the no-trade-rate monitor with its 40–60% band, and the
  briefing-engagement metric (FR-7.9, FR-7.10).
- `application/scoring.py`, `application/calibration.py`, a 16:00 scoring job behind the
  3.7 guard, CLI commands, and `scripts/verify/step_3_8.py`.

Three limits stated now rather than discovered later:

1. **Resolution is measured at a snapshot, not intraday.** The archive holds three chain
   snapshots a day, so "50% of the debit lost" is detected at 09:30, 12:30 or 15:15 — not at
   the moment it was breached. That is recorded as a field on the outcome, not left implicit,
   because a backtest that silently assumes a perfect intraday fill flatters itself.
2. **Nothing already briefed can be scored.** `briefing_ideas` holds 0 rows and no idea has
   legs or premiums stored, so scoring starts from the first briefing after 0009. The
   calibration maths is therefore proven on synthetic resolved ideas with a known answer,
   which is what `docs/10`'s "Done when" and `docs/12` both ask for.
3. **An unscoreable idea gets no outcome row.** `outcomes` is append-only and
   `hypothetical_pnl` is not nullable, so a missing exit premium means the cycle reports a
   refusal with the reason and writes nothing (rule 6). Carried forward from 3.7.

## 2026-10-19 · Step 3.8 — the scorekeeper, and the lock that stays shut

Built: `domain/scoring.py` (per-setup exit rules on premium, resolution, P&L after
costs), `domain/calibration.py` (Brier with Murphy's decomposition, the reliability
curve, a base-rate baseline), `domain/sizing_lock.py`, `domain/monitors.py`,
`application/scoring.py`, `application/calibration.py`, `application/health.py`,
migration 0009, and three CLI commands on the timetable at 16:00, 16:15 and 21:00.
`verify 3_8` is **32 of 32**; `make check` 1128 passed, 4 contracts kept, mypy clean.

- **Decision: the known case before anything else.** `docs/12` says "verify the maths
  against a known case before trusting it on real data", so the first thing written was
  the hand computation: 70 winners and 30 losers all claimed at 70% gives
  `0.7(0.09) + 0.3(0.49) = 0.21`. The code returns **0.210000**, and the Murphy
  decomposition closes to the last decimal on a second, discriminating case.
- **Surprised by, and the most useful thing here:** that same known case must **not**
  open the sizing lock. A forecaster that always says 0.7 and is right 70% of the time
  has reliability 0, resolution 0 and *zero skill* — perfectly calibrated and carrying
  no information. `docs/12` §6 predicts exactly this clustering and says it is not a
  bug. Measured: skill `+0.000000`, `beats_baseline` False. If the lock had keyed on
  calibration alone it would have opened on a forecaster that repeats the base rate.
- **Decision: success is net money, not the resolution label.** A `TIME_STOP` that
  netted a profit is a success; a `TARGET` that lost money after costs is not. Costs
  are ~13% of a vertical's best case (ISS-023), so the two genuinely disagree, and
  scoring the label would measure whether levels were reached rather than whether the
  idea made money.
- **Decision: two rulebooks, never one with a flag.** A credit spread's net entry is
  negative, so "50% of the debit lost" computed from it turns a winner into a loss.
  Both `verify 3_8` and a domain test induce it: the credit rulebook says TARGET and
  the debit rulebook **refuses the structure** rather than returning a number.
- **Surprised by: `right` is a SQL reserved word.** `CHECK (right IN ('CALL','PUT'))`
  failed to parse (`syntax error at or near "IN"`). Renamed the column to
  `option_right` rather than quoting it, because a reserved-word column needs quoting
  at every use site and one missed quote is a runtime error, not a test failure.
- **Surprised by: the domain purity check was over-broad.** It substring-matches
  `open(`, which also matches `is_open(` — a property name, not a file. Same family as
  the clock check in 0.4. Fixed with a word boundary plus
  `test_the_scan_still_catches_a_real_call`, so narrowing the guard could not silently
  weaken it.
- **Found while wiring:** `domain/costs.round_trip_cost` models the exit at the entry
  premium and its own docstring says the caller must recompute. `round_trip_cost_at_exit`
  is that caller. Measured at 100 contracts on real config rates: the winner's round
  trip costs **Rs 199.92** against the loser's **Rs 182.85** — so reusing the entry
  premium understates the cost of exactly the trades that look best.
- **Decision: the no-trade rate counts job runs, not trading days.** Three days with
  the box off would otherwise read as three disciplined quiet sessions, and a dead feed
  would look like a well-behaved system. Only `RAN` counts: a `FAILED` job produced no
  briefing and a `SKIPPED` one was a holiday. Induced both ways in `verify 3_8`.
- **Rejected:** a time stop for Setup B's credit variant. It is flat the session before
  expiry by `flat_by_sessions_before_expiry`, which is a scheduling rule the review
  enforces — inventing an outcome for it would resolve trades the rulebook says are
  still open.
- **Rejected:** letting the debit time stop claim an idea already past expiry. At -4
  days `days_to_expiry <= 5` is true, so the time stop fired and recorded "flat with
  five days left" for a contract that no longer exists. Expiry is now checked first,
  the same ordering `domain/review.py` uses, and the basis is `EXPIRY`.
- **Honest state:** `briefing_ideas` holds 0 rows and nothing has legs, so **nothing in
  the database can be scored today.** `verify 3_8`'s last check reads that count from
  Postgres rather than asserting it. Scoring starts with the first briefing after 0009.
- **Follow-up:** `config/setups.yaml` gained `exits`, `credit_exits` and `debit_exits`,
  still `verified: false`. Setup A's five-day time stop existed only in `docs/06` and in
  no config file until today.

## 2026-10-19 · A live bot token reached stderr (ISS-029)

Wiring up Telegram for the first time, `telegram-check` delivered successfully and
printed the token in full in an httpx log line. Rule 8, broken by our own logging.

- **Cause:** `SecretRedactingFilter._redact_args` redacted only `str` arguments, and
  httpx passes `request.url` as an `httpx.URL` object. The filter skipped it; the
  formatter stringified it afterwards. Every piece of the redaction machinery worked
  as designed and none of it looked at that argument.
- **Surprised by:** the `str`-only rule was not an oversight. It was added to stop an
  int being stringified into httpx's `%d` status specifier - a bug the docstring says
  reached a live run. Correct reasoning about numbers, over-applied to every
  non-string, and the gap it opened was a credential.
- **Decision:** redact on the *rendered* text, not the type. `_redact_arg` stringifies
  any argument and substitutes only when that text contains a registered secret, so a
  number is still handed to `%d` as a number - its text cannot contain a 40-character
  token. A `__str__` that raises returns the value untouched rather than dropping the
  record, because losing the line would hide the event.
- **Verified:** one test against the real `httpx.URL` type rather than a stand-in, and
  `telegram-check` re-run with its output grepped for the live token - the line now
  reads `.../bot***/sendMessage`. 1132 passed.
- **Follow-up:** token revoked and reissued. `verify 3_5` went 11/13 to **12/13**; the
  last check needs a real briefing, which needs `ANTHROPIC_API_KEY`.
- **Worth keeping:** a redaction filter that keys on *type* is guessing which arguments
  can carry a secret. Only the rendered text knows.

## 2026-10-19 · The first live Telegram session found three bugs the suite could not

Wiring up the bot and tapping one button end to end turned up ISS-029, 030, 031 and 032.
All four are now fixed, with tests. 1140 passed.

- **The big one (ISS-030): every idle `capture` poll failed.** The httpx read timeout is
  15s; `getUpdates` asks Telegram to hold the connection for `POLL_SECONDS = 25`. The
  client gave up while the server waited exactly as asked. It only ever succeeded when
  an update was already queued - which is why every manual test to date had passed.
  **In 3.9 this would have produced a FAILED row and a CRITICAL alert every 30 seconds,
  about 2,880 a day**, looking for all the world like a Telegram outage.
- **Decision:** derive the socket timeout from the request's own `timeout` field rather
  than raising the global default. Whatever `POLL_SECONDS` becomes, the socket follows,
  so the two cannot drift apart again. A test asserts it against the real constant.
- **Surprised by:** no fake could have caught any of this. A fake answers instantly, so
  the long poll never happens; a fake never refuses a stale callback. `docs/12` keeps
  R-checks for exactly this class, and this is the clearest evidence yet of why.
- **ISS-031: one stale tap discarded the decisions behind it.** Telegram refuses to
  answer a callback older than about a minute, and the exception escaped `drain()`. The
  acknowledgement is a toast; the prompt is what captures the decision. A poll that was
  down for a few minutes makes every queued tap stale at once, so the batch would be
  lost exactly when the backlog matters most. Now logged, reported, and the flow
  continues to ask for the reason.
- **ISS-032: we threw away Telegram's explanation.** The body said "query is too old and
  response timeout expired"; we surfaced "HTTP 400". Fixed, redacted on the way out.
- **ISS-029, earlier the same session:** a live bot token reached stderr through an
  `httpx.URL` argument the redaction filter skipped as "not a string". Separate entry.
- **Worth keeping:** four bugs in one evening of real use, in code that had 1,132 passing
  tests. Every one needed a live server, and three of the four were in the error paths
  rather than the happy path.

## 2026-10-19 (night) · First full live run, end to end

Universe backfilled, first paid cycle run, every command exercised against real
services. **Cost: Rs 4.15 for one complete cycle.**

- **Backfill: 26/26 instruments, 22,560 bars inserted, 0 without indicators.** The
  universe blocker is gone - `the_universe_has_the_bar_history_the_proxy_needs` went
  from 2 of 26 to **26 of 26**. Dhan returned a 429 mid-run; the limiter backed off ten
  seconds and recovered, which is the first live exercise of that path.
- **`verify 1_1b` reached PASS 8/8** for the first time: 26/26 instruments captured
  live, 9,912 rows. It had been waiting for a trading day and a valid token together.
- **The cycle cost Rs 4.15 and made zero Opus calls.** 196 Haiku triage calls
  (137 classifier, 59 impact analyst); synthesis never ran because there were no
  proposals to write up. The expensive tier is only reached when there is something
  worth paying for, which is the design and now the measurement.
- **"No ideas today" was the correct answer, with the numbers to show it.** 525 articles
  in, 379 distinct stories, 59 catalysts out - and **43 of 1,544 filings name a universe
  company at all** (31 in the last four days). None survived the catalyst gate on type,
  recency and credibility. docs/06 predicted this: "the not-already-priced gate kills
  most ideas, which is correct". ISS-025 removes the two most common real catalysts.
- **Decision deferred, not taken: ISS-033.** 19 Oct was NIFTY expiry, so the nearest
  expiry chain had zero time value - ATM call Rs 0.05, ATM put at intrinsic, IV ~1-2%.
  The capture is right and `atm_iv_from_our_archive_is_believable` is right to refuse
  it. But `atm_iv_series` has no expiry filter, so roughly one day in five would enter
  the IV-rank window at ~2%. No effect until the archive reaches 30 days. Logged with
  two candidate fixes; Ajay's call which.
- **The four fixes from earlier tonight held under real traffic.** The 30-second capture
  poll ran under the scheduler and wrote nothing and alerted nobody - before ISS-030 it
  would have produced a FAILED row and a CRITICAL alert on every single fire.
- **Surprised by:** three capabilities have no entry point. `BackfillService` is called
  only by `verify 1_4` with two hardcoded instruments, and `_refresh_expiries` and
  `_take_backup` exist only inside scheduler jobs. Tonight's backfill needed a scratchpad
  script to call code that already existed. 3.9 produces a runbook, and a runbook cannot
  say "run X" when there is no X.
- **Verified live:** all twelve timetable entries registered, `review`, `score`,
  `calibration` and `health` each delivered their honest empty state, and the sizing
  lock reads "locked: 0 resolved ideas, need 50".

## 2026-10-20 · Pre-3.9 — two cost/correctness fixes, and a measured base rate
- **Built:** ISS-034 (the archive read took the *oldest* 500 rows) and ISS-035 (the news
  agent never received the universe, so it paid to classify 804 companies to find 26).
  Both were found by answering "you read 500 articles and took no action?" — neither was
  reachable by the 1,140 tests, because both were *correct on a small archive* and only
  wrong once the data outgrew the limit.
- **Decision:** the universe is a **run argument**, not constructor state. `CycleRequest`
  already carries the day's universe; a second copy inside `NewsAgent` could drift, and a
  stale copy would quietly classify names we no longer trade. Rejected the constructor
  parameter even though `PortfolioAgent` sets that precedent.
- **Decision:** `_closed_beyond_the_range` moves from `agents/technical/agent.py` to
  `domain/indicators.closed_beyond_range`. It is pure maths over bars and belongs there by
  rule 3, but the real reason is the backtest below: a backtest that re-implements the gate
  is measuring a different gate. One implementation, two callers.
- **New scope, declared (rule 10):** `scripts/backtest_technical.py`, a research script
  measuring `technical_confirm`'s real directional base rate over the 940 trading days of
  bars already stored. Asked for by Ajay, who wants 90% directional accuracy; docs/07 sets
  40% as the *failure* line. Neither number is measured, and this is the only validation
  the existing data can support — a spread's P&L needs option price history, and we hold
  two days of it (ISS-037).
- **Decision:** the script reports the gate's hit rate **against the unconditional base
  rate over the same horizon**, not alone. A gate that is right 55% of the time when any
  random day is right 54% has no edge, and reporting the 55% by itself would be the most
  flattering true number available.
- **Surprised by:** the 20 Oct snapshot refused itself — Dussehra, corroborated by the
  expiry shift in `holidays.yaml`. The fail-closed guard worked; no IV day was lost.
- **Follow-up:** ISS-034, ISS-035 closed below; ISS-036 (no market-wide regime input),
  ISS-037 (the scheduled captures have never run), ISS-038 (`llm_calls` over-counts) open.
- **Measured, and it is the finding of the session:** `technical_confirm` has **no
  directional edge** over 22,056 bar-days, and the only two statistically distinguishable
  cells are *negative* — a bullish breakout on volume is 5.4% *less* likely to be higher
  in a week than a random day (sigma -2.7). Six of eight cells are indistinguishable from
  noise. The gate is selective (fires 4.6% of days) and that selectivity buys nothing
  directional. Logged as ISS-039; it blocks the `setups.yaml` sign-off, which was always
  `verified: false` and now has its first evidence.
- **Why the base-rate column was worth the extra code:** the raw bullish hit rates read
  47-55%, which looks like a working coin-flip-plus signal. Against the base rate for the
  same days they are *below* it. Printing the hit rate alone would have been the most
  flattering true number available, and would have sent us tuning thresholds on a signal
  that points the wrong way.
- **Trade-level simulation added** (`scripts/backtest_trades.py`), because a directional
  hit rate says nothing about money. 1,023 stock trades: **37.0% win rate, -0.50%
  expectancy per trade**; 39 index trades at 23.1% and -0.86%. Breakeven at the realised
  1.16 win/loss ratio is 46.3%. Only 4 of 24 names are net positive. Appended to ISS-039
  rather than opened as a new issue - same gate, same conclusion, deeper evidence.
- **Decision:** a bar whose range contains both the stop and the target is scored as a
  **stop**. Daily bars cannot order the two, and assuming the favourable fill is the most
  common way a backtest flatters itself. Worth stating because it is the single assumption
  most able to move this result.
- **The exits are mine, not `setups.yaml`'s, and the script says so in its docstring.**
  Setup A's configured exits are fractions of the spread premium and need option price
  history we do not have (ISS-037). Anyone reading this later must not mistake 1.0/1.5 ATR
  for a signed-off rule.

## 2026-10-20 · New scope — a strategy framework and a backtest dashboard
- **New scope, declared before the code (rule 10).** Neither of these is a numbered step in
  `docs/10`. Asked for directly by Ajay after ISS-039 showed the one measurable signal has
  negative expectancy: he wants to iterate on strategies, so the backtest has to stop being
  a one-off script and the results have to be inspectable.
- **Decision: a strategy is a Protocol in `domain/backtest/`, carrying its own exit
  policy.** The index strategy is independent of the stock strategy by construction rather
  than by convention — separate thresholds, separate exits, separate instrument kind. ISS-039
  measured the stock rule at 37% and the same rule on indices at 23%, so sharing parameters
  between them was never defensible.
- **Decision: a signal returns `GateResult`s, not a boolean.** Rule 9 already requires this
  of production gates; applying it to backtest signals is what makes "why was this trade
  taken" answerable per trade, which is the dashboard's main job.
- **Decision: React from a CDN in one HTML file, served by `trading-desk dashboard`.** The
  repo has no node toolchain and adding one for a results viewer is not worth it. The page
  reads `data/backtests/*.json`, so a new strategy run appears on refresh with no build step
  and no re-publishing.
- **Refused to fabricate two of the requested columns.** "Traded against the news" cannot be
  answered for 2023-2025: the filings archive starts 2026-10-14 and holds six days. The
  field exists and reads `unavailable (no news archive before 2026-10-14)` rather than
  defaulting to "no" — the same reasoning as `NO_DATA` in the gates. Market direction *is*
  available, from NIFTY's own bars, and is populated.
- **"What can we do to improve it" is mechanical, deliberately.** The diagnosis names what
  happened (stopped inside one session, gapped through the stop, time-stopped without
  movement) and the parameter implicated. It does not generate trading advice; an LLM
  inventing a fix for a losing strategy is how a backtest becomes a story.
- **Built and measured.** `domain/backtest/` (strategy protocol, engine, two strategies),
  `application/backtest.py`, `trading-desk backtest`, `trading-desk dashboard`, 28 new tests.
  First run: `stock_breakout_volume` 988 trades, 37.0% win rate, **-0.72%** expectancy;
  `index_trend_pullback` 68 trades, 33.8%, **-0.35%**. Both lose money.
- **My own reporting was wrong on the first run, and the fix is the useful part.** It
  printed "beats breakeven" for the index strategy because 33.8% clears the *theoretical*
  33.3% implied by a 1:2 stop/target. Its expectancy was negative. The theoretical figure
  assumes every win is the full target and every loss the full stop; gaps overshoot the
  stop and time stops exit part-way, so the **realised** breakeven is always higher - 49.7%
  and 44.9% here against theoretical 40.0% and 33.3%. The verdict is now expectancy, and
  `realised_breakeven` is what the dashboard colours against. A win-rate-versus-theoretical
  comparison is a number that can flatter a losing strategy, which is how it behaved.
- **Surprised by: 147 of 988 stock trades (15%) gapped through the stop.** Combined with
  180 stopped inside one session, over a third of all trades die on or before the first
  session. A gap is the one failure a tighter stop makes worse, so this bounds how much any
  stop-tuning can recover - worth knowing before anyone tunes one.
- **Engine honesty rules, each with an induced test:** entry at the open *after* the signal;
  a bar containing both stop and target scores as a stop; a gap fills at the open rather
  than at the stop level. Those three assumptions move the result more than the strategies
  do, which is why they are tested rather than commented.
- **Fixed on Ajay's feedback: "still not found reason why u take the trade".** The reasoning
  was in the JSON and correct, but the terminal report never printed it and the dashboard
  hid it behind a row click. Both were my framing, not a data gap. The report now prints the
  criteria every run plus worked examples with measured values, and the dashboard carries a
  visible `why this trade was taken` column and a chevron so the row expansion is findable.
  Worth recording because the lesson generalises: a measured value that is stored but not
  shown is indistinguishable from one that was never measured.

## 2026-10-20 · New data source — filing documents, read but never stored
- **New scope, declared before the code (rule 10).** Filing PDF attachments become an
  input. ISS-025 has blocked the catalyst gates since 19 Oct: stored filing bodies average
  143 characters because the content is in an attachment the 1.5a adapter records a URL for
  and never fetches. Verified today on the live archive: **1,917 of 1,946 filings carry an
  `nsearchives.nseindia.com` URL, and 100% of every subject that needs reading does.**
- **Verified rather than assumed, in one probe:** a real fetch returned HTTP 200,
  8.76 MB, `application/pdf`, with a **genuine text layer** — 53,531 characters over 21
  pages, against the 144 characters the database holds for the same filing. Revenue, total
  income, PBT, profit for the period, EPS and EBITDA all appear as extractable text.
- **Decision, forced by the terms re-read (ISS-040): the document text is never stored.**
  NSE's copyright page says no portion of the Content may be "stored in any other website
  or in other form of electronic retrieval system", while separately permitting users to
  "view, print copies and download" for personal, non-commercial purposes. Reading a filing
  for one's own research fits the second clause; putting its text in Postgres runs into the
  first. So the adapter fetches at classification time, hands the text to the classifier,
  and discards it. What persists is the derived catalyst type, the confidence, and the short
  evidence quote the grounding check already requires.
- **Rejected: a `filing_documents` table.** It was the obvious design and it is the one the
  terms forbid. It would also have cost ~17 GB and introduced a stale-copy problem. The
  transient shape is cheaper and simpler; the real cost is that re-classifying an old filing
  means re-fetching it, so a prompt change is no longer free.
- **Decision: `EARNINGS_SURPRISE` is not unlocked by this and must not pretend to be.** A
  surprise is reported *versus expected*, and no PDF contains anyone's estimate. The five
  prose types plus `GUIDANCE_CHANGE` are genuinely unlocked. The earnings types need either
  a consensus-estimate source (a purchase, Ajay's call) or honest renaming to growth.
- **Surprised by the extraction quality, and it changes the plan.** The revenue line comes
  out as `4,548 4,163 3 ,701 8,711 7 , 2'22` — spacing artefacts, an apostrophe for a comma,
  and **no column headers**. Which figure is the current quarter versus the year-ago quarter
  is not recoverable from the line alone, and guessing produces a confident wrong growth
  number. So the first pass does **no numeric table parsing at all**: prose types only.
- **Cost, measured not guessed:** ~3-10 in-universe filings a day need a document, at
  ~13,400 tokens each, which is **₹0.40-1.20/day** on Haiku. Before the ISS-035 universe
  fix the same work would have been ~₹25/day and ~₹900 to backfill. The bug fix is what
  makes this affordable.
- **Follow-up:** ISS-040 (the terms reading, needs Ajay), ISS-025 (unblocked by it).
- **Owner decision: stop NSE collection** (20 Oct, ISS-041). The Terms of Use prohibit
  "any systematic or automated data collection activities" with no personal-use exception.
  `verify 1_5a` no longer polls; NSE is in `excluded` in `news_sources.yaml` v4 with the
  clause quoted; the document fetcher stays unwired.
- **My error, worth recording because the guard already existed.** I read the copyright
  page, found "users may view, print copies and download ... for personal, non-commercial
  purpose", and reported the fetch as defensible. The prohibition was in the Terms of Use,
  a document I had started and not finished. ISS-014's standing rule - *a source is used
  when its terms clearly allow it, not when they merely fail to forbid it* - is exactly
  the rule that would have caught this, and I applied it to sixteen publishers on 18 Oct
  and not to the source we already depended on. The lesson is not "read more"; it is that
  the standard has to be applied hardest to the source you most want to keep.
- **The working poller is kept as unreachable code**, not deleted. If permission or a
  licensed vendor arrives, one constant changes. Deleting it would lose a verified
  implementation to re-earn later, and the compliance position is recorded in config where
  a future session will actually read it.
- **Owner instruction: remove, not halt.** The NSE scraper, the PDF fetcher, `FilingsPort`,
  `FilingsIngest`, both fakes, their tests, `verify 1_5a` and the `pypdf` dependency are
  deleted. My earlier choice to keep the poller as unreachable code was wrong and the
  override is the better call: unreachable code is still code a future session can switch
  back on, and the reasoning belongs in `config/news_sources.yaml`, which a session reads,
  rather than in a comment beside a dead function.
- **Verified by audit rather than asserted:** the only remaining outbound HTTP paths are
  the three Dhan hosts (paid, licensed), `api.telegram.org` (the owner's own bot), and
  `rss.py`, whose feed list in config contains SEBI alone. Two `nseindia.com` strings
  survive in test fixtures; both are `url=` fields on constructed objects with no fetch.
- **`verify 1_5a` is deleted rather than left failing.** The step verified a capability
  that no longer exists, and a permanently-red check trains people to ignore red checks.
  Ports drop from 20 to 19 in `step_0_6`.
- **Owner instruction: delete the stored filings.** 1,946 rows gone. The useful part was
  that looking for *copies* found three more than the table: ten of eleven backup dumps
  (confirmed with `pg_restore -l`), and `data/labels/news_catalysts.csv`, which held 175
  rows of filing text for the step 3.2 labelling task. A fresh backup was taken and
  restore-drilled **before** deleting the old ones, so there was no window without a
  working backup.
- **Not removed, and it needs a decision:** 123 `cycle_responses` payloads carry a
  one-line verbatim excerpt from a filing inside `evidence_quote`. The table's append-only
  trigger blocks DELETE deliberately - it is what makes the cost ledger tamper-evident - so
  removing them means dropping a safety guard to delete one-line fragments. Flagged rather
  than decided.
- **`verify 3_2` is now permanently unpassable** and that is the honest state, not a
  regression to fix: the subject-router check degraded to "no filings in the archive to
  measure against" rather than crashing, and the 200-item labelling set was to be drawn
  from the archive that no longer exists.

### 2026-10-20 — owner instruction: warn on legality *before* the code

> "From now onward make sure if its not legal or have compliance issue, u will warn about
> update the doc accordingly"

This is the lesson of ISS-041 turned into a rule. The NSE failure was not that the terms
were hard to read — it was the **order of operations**. The adapter was written, the data
was stored for six days, and the binding clause was found afterwards, only because the
owner asked a direct question. By then the remedy was deleting 1,946 rows, ten backup
dumps, a 175-row label sheet and a verification step.

Now **CLAUDE.md rule 11**: a legal or compliance doubt is raised in that message, before
any code is written, and the finding is recorded quoted in the config a session actually
reads. Three specifics, each one a mistake already made once:

- **"Says nothing about terms" is blocked, not allowed.** The default on silence is no.
- **Content status is not access status.** A legally-mandated public disclosure may still
  not be automatically collected. Reading the first as permission for the second was the
  whole of ISS-041.
- **Finish the terms before quoting them.** The copyright page permitted viewing and
  downloading; the prohibition was in the Terms of Use, a document I had opened and not
  finished reading.

Unchecked sources still in use, now owed a written finding under this rule: **Telegram**
(the owner's own bot, bot API terms never read), **Anthropic** (commercial API, terms never
read), **cdnjs** (the dashboard's React tag), and **Dhan**, whose paid API is licensed but
whose long-term-storage position was never asked — the one that matters, because the IV
series it feeds is irreplaceable.

### 2026-10-20 — evaluated Drishti (Manasija) as a licensed replacement for NSE

Owner asked whether `platform.manasija.in` suits news and company information. It is the
best-fitting candidate found so far and the first whose terms actively help us: storage
permitted for licensed use, internal research named as a permitted purpose, derived signals
owned by us, and an IP indemnity. Recorded quoted in `config/news_sources.yaml` under a new
`candidates` block rather than in prose, so a session reads it where it chooses sources.

**Not adopted, for one reason.** Their Customer Agreement §1.3 allows the content to be
*"aggregated from public sources"*. That is the phrase that would cover scraping NSE, which
is precisely what ISS-041 prohibits us from doing, and paying a third party for it does not
obviously change the position. They claim "empanelled" vendors but name none and hold no
visible NSE/BSE authorisation. **This is the ISS-041 mistake offered back to us at
Rs 2,000/month** — the fix is three written questions, not a subscription, and under rule 11
that happens before any code.

Also recorded: on the free sandbox tier their privacy policy permits indefinite retention
and model training on our queries *and outputs*, so the free credits are for checking the
JSON shape only, never the real universe.

While in the file, corrected its footer, which still claimed NSE announcements were in use
via a deleted adapter.

### 2026-10-20 — new data source: Drishti news (rule 10 entry, written before the code)

Owner added `DRISHTI_API_KEY` and asked for the news-journal integration. Scope widens by
one adapter against the existing `NewsPort`; no new port, so the one-fake-per-port
invariant is untouched.

**Why this source and not another.** ISS-041 left Setup A's catalyst input at 27 SEBI
articles. Measured against our own 24 names over 30 days, Drishti carries 299 symbol-tagged
items across **24 of 24** names, from Economic Times, Moneycontrol, Mint and Business
Standard, on 31 distinct days, with the freshest item 28 minutes old at the time of the
probe. That is the gap closed, with evidence rather than a brochure.

**It fits the posture `rss.py` already set.** Drishti returns a headline, its own `summary`
and `long_summary`, the publisher name and a link — **not the publisher's article text**.
That is the same line `rss.py` draws deliberately: store what is syndicated, never fetch
the page body. So the body we persist is Drishti's derived summary, and the docstring says
so, because a future reader must not mistake it for the publisher's prose.

**One genuine mismatch, recorded rather than smoothed over.** `domain/news.py` says "the
raw body is kept, never a summary", written when the body was an NSE filing's own text.
No source we may now lawfully read supplies a full body. The field keeps the richest text
the source permits, which is `long_summary` (median 1,095 chars, max 1,756). The
credibility scorer reads wording, and summary wording is Drishti's, not the publisher's —
that is a real loss of signal and it is the price of being inside the terms.

**Three limits found by probing, which bound what may be claimed:**
- **The archive starts mid-2025.** 2022, 2023 and 2024 return zero rows for both news and
  announcements; per-symbol news for RELIANCE begins Nov 2025. So catalyst gates can be
  backtested over ~12 months, not the ~4 years of bars we hold. Better than
  `NEWS_UNAVAILABLE`, far short of the bar history.
- **Sentiment is skewed 282 positive / 153 neutral / 51 negative.** A feature that says
  "positive" 58% of the time is close to constant and must not be treated as an edge until
  it is measured against outcomes, the way ISS-039 measured `technical_confirm`.
- **Caps:** `limit` max 50, `symbols` max 20 per call, sandbox 10 rpm / 1,000 per day. Our
  24 names therefore need two batched calls, which the adapter does rather than the caller.

**Costs, measured not assumed:** news ~1 credit per *page*; announcements 1 credit per row;
`detailed=true` 2 per row. News is the cheap endpoint and announcements are the expensive
one, which is the opposite of what the pricing page implies.

**Compliance position is unchanged and still open (ISS-043).** The owner has chosen to
proceed, which is his call and is recorded as such. The §1.3 "aggregated from public
sources" question is NOT answered, so this adapter is written so that losing the source
costs nothing irreplaceable: raw article text is a droppable cache, and anything we derive
goes in our own tables. Announcements and earnings are deliberately **not** wired yet —
each needs its own port and its own entry here.

**Built and verified the same day.** `adapters/news/drishti.py` (NewsPort),
`adapters/news/combined.py` (several sources behind one port, so SEBI and Drishti arrive in
one pass and syndication counting still works), `DrishtiSettings`, a `drishti:` config block
and `trading-desk news-check`. 50 new tests. Live run: 169 articles over 7 days, 30 EXCHANGE
from SEBI and 139 TIER_2 from Drishti, 22 of 24 names covered, HINDUNILVR and KOTAKBANK
silent - which the command prints, because a name with no news cannot clear a catalyst gate.

**A test caught a real flaw before the live run.** The API key was set as a header on the
client the adapter builds, so an injected client - every test, and any future caller that
supplies its own - sent no key at all. It is now attached per request. A guard that
disappears exactly when someone substitutes the transport is worse than no guard.

**A second bug, found by printing the raw records rather than the summary.** Asked what the
vendor actually sends, I dumped a real RELIANCE row and its `symbol` field read
`"RELIANCE:ONGC"`. Multi-company articles are **colon**-separated, while their *request*
parameter takes commas - so commas are the obvious guess, and the obvious guess silently
dropped every symbol on every multi-company row. Measured: **55 of 150 rows (37%)** use the
colon form, including sector stories naming eight banks, which is precisely the kind a
catalyst gate wants. After the fix the same 7-day window goes from 93 symbol links to
**168**, and from 22 of 24 names covered to 23; HINDUNILVR was never actually silent.

Worth recording how it was missed: the first coverage probe and the adapter made the *same*
wrong assumption, so the probe confirmed the adapter instead of testing it. The number
looked plausible (93 of 169) and nothing failed. It surfaced only when the owner asked what
the data actually looked like. A measurement that shares a bug with the thing it measures is
not a measurement.
