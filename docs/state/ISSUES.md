# ISSUES — open development issues

Managed with the `issue-log` skill. Closed issues move to `ISSUES-ARCHIVE.md`; this file
carries only open work. The index below is what a session reads — the `### ISS-` sections
are opened one at a time, when that issue is being worked on. Severity: **safety** · **data-correctness** · **correctness** ·
**reliability** · **cosmetic**.

`safety` and `data-correctness` block the step they were found in. Everything else is
logged and work continues — no drive-by fixes inside a step.

---

## Open

**Read this table, not the sections below.** One line per open issue; the detail lives under
the matching `### ISS-` heading and is read only when that issue is being worked on. That is
the difference between ~30 lines a session and ~500.

| Issue | Status | Owner | What it is |
|---|---|---|---|
| ISS-002 | open | ? | No option-chain history exists |
| ISS-033 | open | me | Expiry-day chains poison the ATM IV series |
| ISS-003 | open | user | Read-only Dhan API key availability unconfirmed |
| ISS-004 | open | ? | Expiry dates shift off Tuesday for holidays |
| ISS-005 | open, mitigated | me | Access token expires every 24 hours |
| ISS-006 | open, mitigated | me | Dhan's chain response cannot tell you which session it describes |
| ISS-007 | open, contained | me | Stale `last` prints give deep-ITM strikes impossible IVs |
| ISS-006b | closed | me | Two entry points could archive a non-trading day |
| ISS-008 | open | me | Muhurat session hours are not modelled |
| ISS-009 | closed | me | NSE announcements truncate silently without a date range |
| ISS-010 | open | user | News articles and near-duplicate detection need provider decisions |
| ISS-011 | open | me | The near-duplicate threshold is tuned on constructed headlines |
| ISS-012 | closed | me | Text similarity alone cannot group news, at any threshold |
| ISS-013 | open, accepted | user | The NSE poller must present a browser User-Agent |
| ISS-014 | closed | me | Both RSS publishers' terms prohibit this use |
| ISS-020 | open | me | A TimescaleDB restore failed once and will not reproduce |
| ISS-021 | open | user | Setup A's delta bands are unsatisfiable on most real NSE chains |
| ISS-022 | open | user | Flat 0.25% risk implies a much larger account than expected |
| ISS-023 | open, informational | me | Trading costs are ~13% of a real spread's best case |
| ISS-025 | open | user | Stored filings are subject lines, not content; EARNINGS_SURPRISE is unreachable |
| ISS-026 | open | user | The Fundamental agent is blind to promoter pledge and sell-down |
| ISS-027 | open | user | No sector map exists, so correlated names cannot be detected |
| ISS-028 | open | user | Two of Setup A's calendar inputs have no source, and both fail open |
| ISS-036 | open | me | No market-wide regime input: a stock is judged without reference to the index |
| ISS-037 | open | me | The three scheduled chain captures have never run; only 2 days of IV history exist |
| ISS-038 | open | me | `PipelineReport.llm_calls` over-counts: it includes free subject-routed classifications |
| ISS-039 | open | user | Measured: `technical_confirm` has no directional edge, and is negative at 1-2 weeks |

---

### ISS-002 — No option-chain history exists
- **Found:** 2026-10-14, during planning
- **Severity:** data-correctness
- **Symptom:** IV rank needs ~1 year of per-instrument IV history; none exists
- **Impact:** both v1 setups gate on IV rank. Until 6 months accrue, ranks come from a
  realised-vol proxy and must be tagged as such
- **Recoverable:** **no** — this series cannot be reconstructed from price data
- **Status:** open · mitigated by Step 1.1 starting immediately; a vendor purchase would
  close it outright

### ISS-003 — Read-only Dhan API key availability unconfirmed
- **Found:** 2026-10-15, writing docs/11
- **Severity:** safety
- **Symptom:** v1 broker sync needs read access; unclear whether a read-only key is issuable
- **Impact:** if not, mitigation is architectural only — no order adapter in the v1
  codebase, enforced by `import-linter`
- **Finding 2026-10-15:** DhanHQ documents no read-only or data-only token scope. Trading
  APIs (free) and Data APIs (paid) are separate product lines, but the access token is one
  credential and the docs describe no scope choice at generation. **Assume the token can
  place orders.**
- **Status:** open, downgraded to confirmation only · the architectural mitigation (FR-13.6,
  no order adapter in the v1 codebase) is now the primary control, not the fallback
- **Owner:** user — worth one question to Dhan support, but do not block Step 1.1 on it

### ISS-004 — Expiry dates shift off Tuesday for holidays
- **Found:** 2026-10-16, live verification of Step 1.1a
- **Severity:** data-correctness
- **Symptom:** docs/06 states "NIFTY weekly expiry, Tuesday". Dhan's live expiry list for
  NIFTY reads 2026-10-19 (Mon), 10-27 (Tue), 11-03 (Tue), 11-09 (Mon), 11-17 (Tue),
  11-23 (Mon). BANKNIFTY: 10-27 (Tue), 11-23 (Mon), 12-29 (Tue).
- **Cause:** when the Tuesday is a trading holiday the expiry moves to the previous
  trading day. 2026-10-20 is Dussehra and 2026-11-10 is Diwali Balipratipada, which
  accounts for the Monday dates exactly.
- **Impact:** any code that *computes* an expiry from a weekday rule would pick a contract
  that does not exist. The DTE gates in both setups, and the expiry-eve flatten rule in
  v2, would be wrong on those weeks.
- **Mitigation already in place:** `DhanOptionChainAdapter.available_expiries()` reads the
  list from the exchange and never computes it. The adapter was written that way on
  principle; this is the evidence that the principle was right.
- **Closed 2026-10-18, step 1.6.** The rule is now enforced rather than merely followed:
  `verify 1_6` induces the weekday rule against the live list and requires them to
  disagree, so a future "simplification" fails CI. `docs/06` has been corrected - it still
  said "Weekly, Tuesday" with no caveat.
- **Unexpected dividend:** the displacement works as evidence in the other direction. A
  weekly expiry that is not on a Tuesday means the exchange was shut that Tuesday, which
  makes the expiry list a primary source for the holiday calendar - better than any
  published calendar, because it is the venue's own behaviour. That audit immediately
  found 2026-11-24 (Guru Nanak Jayanti) missing from `config/holidays.yaml`.
- **Status:** open - needs a docs/06 wording fix ("Tuesday, moving earlier for holidays")
  and a rule that nothing anywhere computes an expiry date.

### ISS-005 — Access token expires every 24 hours
- **Found:** 2026-10-16, step 1.1
- **Severity:** reliability
- **Symptom:** the snapshotter cannot run unattended. A web-generated token lasts 24
  hours; the API key + secret flow needs a browser login.
- **Impact:** without a fix, capture depends on a manual paste every morning - and a
  missed day is IV history that cannot be recovered.
- **Options:** (a) daily manual paste, (b) PIN + TOTP via
  `auth.dhan.co/app/generateAccessToken`, which is fully unattended but puts the account
  PIN and TOTP seed in `.env`, (c) the browser consent flow, which needs one login per
  token but keeps the PIN off disk.
- **Decision:** (c), chosen by the user 2026-10-16.
- **Measured 2026-10-17:** a consent-flow token lasts **exactly 24 hours** - issued
  23:46 IST on the 16th, expiring 23:46 IST on the 17th. A token therefore covers all
  three of one session's captures (09:30, 12:30, 15:15) with hours to spare, so the
  scheduler runs a full trading day unattended after a single login.
- **Residual:** one browser login per 24 hours. `ensure_valid_token` refreshes when
  under an hour remains, so the login lands whenever the app is next started or a
  capture begins with a stale token - not on a fixed clock. Unattended operation across
  multiple days still needs (b); that remains the user's security call, not a default.
- **Status:** mitigated - not closed, because an unattended multi-day run is still
  blocked · **Owner:** user

### ISS-006 — Dhan's chain response cannot tell you which session it describes
- **Found:** 2026-10-17, step 1.2
- **Severity:** correctness of any volume-derived metric
- **Symptom:** two feed behaviours that combine badly.
  1. Volume is session-cumulative, and on a closed day Dhan keeps serving the *previous*
     session's figures rather than zeroes. A Saturday survey of NIFTY measured ₹5,126 cr
     of premium turnover - real numbers, from Friday.
  2. The chain's `exchange_time` is the time Dhan answered the request, not the time the
     figures describe. A survey at 00:24 IST on Saturday reported `exchange_time`
     00:24 IST on Saturday over Friday's volume. Age computed as `-0.0` hours.
- **Impact:** an out-of-hours run looks completely healthy. Nothing in the response
  distinguishes live figures from days-old ones, so any ranking, IV rank or turnover
  average built from volume can silently describe a session nobody intended.
- **Fix in place:** freshness is decided from the trading calendar in the caller, never
  from the feed. `trading-desk survey` refuses to run on a non-trading day before it
  spends half an hour on requests (`--force` overrides), and the generated universe
  records `measured_on` so a stale file is visible afterwards. `LiquidityMeasure`
  deliberately carries no timestamp, with the reason in its docstring.
- **Still to watch:** the snapshotter writes `exchange_time` into every Parquet row. That
  is correct as a record of when the row was received, but it must never be read as
  "when this quote was traded". Anything later that computes a time-weighted figure from
  the archive needs to use the IST partition date instead.
- **Status:** mitigated · **Owner:** me

### ISS-007 — Stale `last` prints give deep-ITM strikes impossible IVs
- **Found:** 2026-10-17, re-running Phase 0/1 verification during step 1.2
- **Severity:** data quality; contained, but poisons any chain-wide IV aggregate
- **Symptom:** 49 of 4,942 archived rows carry an implied volatility above 200%, peaking
  at **419%** on MARUTI 9800 CE with spot at 11,489. The `last` on that row is 3,980
  against an intrinsic value of 1,689 - 2.4x intrinsic, which no rational price reaches.
  Several names show the same shape at 3.9-4.8x intrinsic.
- **Cause:** deep in-the-money strikes that did not trade keep a stale last-traded price
  from a session when spot was far higher. Dhan's solver backs an IV out of that price.
  **All 49 rows have volume 0.** Their bid and ask are live and sane, so a two-sided-quote
  filter does not catch them - only the `last` is wrong.
- **Why the data is still correct as captured:** it faithfully records what the exchange
  published, and CLAUDE.md keeps trimming out of captured data. Two things also make it
  harmless where it matters:
  - **ATM IV is unaffected.** Measured over the same archive: 6.2% to 35.5% across 52 ATM
    strikes. The IV-rank series is built from ATM, so it is clean.
  - **Turnover is unaffected.** Premium turnover is `last x volume`, and every affected
    row has volume 0, so a stale price multiplies to nothing.
- **What must not be done:** no mean, median or percentile of IV across a whole chain
  without excluding zero-volume strikes. An unfiltered chain-wide average on this archive
  is wrong by roughly 40 percentage points of standard deviation.
- **Fix in place:** `verify 1_1b` now bounds **ATM** IV (< 100%) rather than chain-wide IV,
  and separately asserts that every implausible IV sits on a strike that did not trade -
  so if a *traded* strike ever shows one, that is a new problem and the check says so.
- **Not changed, deliberately:** `LiquidityMeasure.legs_priced` counts a leg as priced when
  IV is non-null, which includes these rows. Requiring a two-sided quote instead drops
  coverage to 27-33% even for NIFTY, which would invalidate the 25% floor set the same
  day. Recalibrate both together after the first real survey, not one without the other.
- **Status:** open, contained · **Owner:** me

### ISS-006b — Two entry points could archive a non-trading day
- **Found:** 2026-10-17, same re-run
- **Severity:** would have been unrecoverable archive corruption
- **Symptom:** `scripts/snapshot_once.py` and `scripts/verify/step_1_1b.py` both built a
  `ChainSnapshotter` and captured with no calendar check. Only the CLI had one. Running
  either on a Saturday would have written a `date=2026-10-17` partition holding Friday's
  quotes - and because partitions are keyed by IST date, that phantom session would be
  indistinguishable from a real one in the IV-rank history.
- **Caught by:** running `make verify-phase PHASE=0` and the 1.x steps after step 1.1c,
  which had not been done since the guard was added to the CLI only.
- **Fix:** the guard moved into `ChainSnapshotter.run()`, which raises `MarketClosedError`
  on a non-trading day. Every entry point inherits it; `force=True` is the single
  deliberate override and the scheduler never sets it. mypy flagged all four call sites
  the moment the constructor changed, which is how the two unguarded ones surfaced.
- **Lesson recorded:** a guard placed in one caller is not a guard. It belongs at the point
  of the write.
- **Status:** closed · **Owner:** me

### ISS-008 — Muhurat session hours are not modelled
- **Found:** 2026-10-18, step 1.3a
- **Severity:** low, but silently wrong if it fires
- **Symptom:** `config/exchange_rules.yaml` carries one session, 09:15-15:30 IST. Muhurat
  trading - one evening a year, around 18:00-19:00 - runs entirely outside it.
  `config/holidays.yaml` already lists 2026-11-08 as a special session.
- **Impact:** every Muhurat tick would be rejected as `outside_session` and that day would
  produce no bars. The rejections are recorded, so the data is not corrupted and the loss
  is visible - but nothing currently reads them and nobody would notice on the night.
- **Why not fixed now:** the exchange publishes Muhurat timings by circular a few weeks
  ahead, and inventing plausible hours is worse than having none. It needs a
  `special_session_hours` block keyed by date, filled from the circular.
- **Due before:** 2026-11-08.
- **Status:** open · **Owner:** me (needs the NSE circular, same one as the holiday list)

### ISS-009 — NSE announcements truncate silently without a date range
- **Found:** 2026-10-18, step 1.5a
- **Severity:** would have lost filings while looking healthy
- **Symptom:** `GET /api/corporate-announcements?index=equities` returns the **twenty most
  recent** announcements and gives no indication it has truncated - no count, no cursor,
  no flag. The same call with `from_date`/`to_date` returned **1,368** for a three-day
  window and **1,542** for four days.
- **Impact:** during market hours twenty market-wide announcements can be a few minutes.
  A poller on a fifteen-minute interval would have missed most filings every single run
  and reported success each time. Caught only by noticing that a four-day lookback
  returned exactly 20 rows.
- **Fix:** the adapter always sends `from_date` and `to_date`, derived from `since` in IST.
  `verify 1_5a` requires at least 100 filings in a four-day window, so a silent revert to
  the truncating form fails.
- **Also worth knowing:** the endpoint refuses a plain request entirely - not a 403, no
  response at all. It needs a browser User-Agent and a Referer naming the filings page, and
  the adapter visits that page once to pick up a cookie.
- **Status:** closed · **Owner:** me

### ISS-010 — News articles and near-duplicate detection need provider decisions
- **Found:** 2026-10-18, step 1.5a
- **Severity:** blocks FR-3.2 and FR-3.3
- **What is done:** exchange filings (FR-3.1) are live and free from NSE. Entity resolution
  (FR-3.4) passes its fixture set. Untrusted-input handling (FR-3.9) is in place. Exact
  duplicate grouping by content hash works.
- **What is blocked, and why it is the user's call not mine:**
  1. **A news API for articles.** The build plan says to check each provider's licence
     terms, some of which prohibit automated storage - and FR-3.2 requires storing the full
     raw body. That is a terms-and-cost decision.
  2. **An embedding model for near-duplicate detection.** Hashing catches verbatim
     syndication; it cannot catch the same story reworded, which needs embeddings. Either a
     local model (free, no key, a sizeable dependency) or a hosted one (an API key and a
     per-call cost).
- **Prepared for both:** `articles` exists with an unused `embedding vector` column, with no
  dimension pinned so either model lands without a migration. An ANN index needs a fixed
  dimension and is deliberately deferred until the model is chosen.
- **REOPENED 2026-10-18, same day.** The RSS answer was wrong. See ISS-014: both candidate
  publishers' terms of use prohibit this use, and the feeds have been removed. A licensed
  news API is now the only route to FR-3.2, which makes this a purchase decision again.
- **Partially addressed 2026-10-18 evening.** SEBI's RSS is now wired in and compliant, so
  the `articles` pipeline is live rather than dormant. It does **not** close FR-3.2: of the
  first 25 articles stored, **0 linked to a universe instrument** — the feed is dominated
  by Securities Appellate Tribunal appeals naming individuals. SEBI informs enforcement
  risk and eligibility; it is not a substitute for journalism and not a sentiment source.
  A licensed API remains the only route to news articles, and remains the user's call.
- ~~Resolved 2026-10-18. The user chose RSS-only plus local similarity. No API
  purchase, no key, no recurring cost.~~ FR-3.2's "full raw text" is met as *the full text
  the publisher syndicates* - a 170-300 character summary - and the reasoning is that the
  authoritative detail for any corporate event is already held in full, free, from the NSE
  filing. A news article contributes timing and framing, both of which survive a summary.
  Near-duplicate detection uses character-shingle Jaccard rather than embeddings: no model
  download, deterministic, explainable, and it would not have been improved by embeddings
  (see ISS-012).
- **Status:** open · **Owner:** user — a purchase decision.
  (The line here read `closed` until 2026-10-19. That was stale: the entry was
  REOPENED the same day it was closed, and the paragraph above says a licensed API
  remains the only route to FR-3.2. The struck-through 'Resolved' line is history,
  not the current state.)

### ISS-011 — The near-duplicate threshold is tuned on constructed headlines
- **Found:** 2026-10-18, step 1.5b
- **Severity:** low now, rises with volume
- **Symptom:** `DEFAULT_THRESHOLD = 0.22` in `domain/similarity.py` sits in a measured but
  narrow gap: for one company, different events scored 0.068-0.179 and the same event
  reworded scored 0.26 upward. The measurements came from about a dozen headlines written
  by hand, not from a month of real feed data.
- **Impact:** too low merges two genuine stories and loses one, which is the worse
  direction because a missing story cannot be noticed downstream. Too high counts one
  story several times and overstates consensus, which is what the credibility gate is
  meant to weigh.
- **Mitigation:** grouping is partitioned by instrument, so the threshold can only ever
  merge stories about the same company - which bounds the damage considerably.
- **Due:** re-measure against stored articles once a few weeks have accumulated. The data
  to do it with is being collected now.
- **Status:** open · **Owner:** me

### ISS-012 — Text similarity alone cannot group news, at any threshold
- **Found:** 2026-10-18, step 1.5b, before shipping
- **Severity:** would have attributed one company's news to another
- **Symptom:** measured on realistic headline shapes:
  - `"TCS wins $1 billion deal with European retailer"` against
    `"Infosys wins $1 billion deal with European retailer"` -> **0.837**
  - `"Infosys Q2 profit rises 12% beating estimates"` against
    `"Infosys second quarter profit up 12 percent, beats estimates"` -> **0.260**
  Same-story scores ranged 0.26-0.60 and different-story reached 0.84. The distributions
  overlap completely, so no cut-off separates them.
- **Why embeddings would not have helped:** those two deal headlines are semantically
  near-identical. A semantic model scores them *higher*, not lower. This is a property of
  the problem, not of the method - which is worth recording, because "use embeddings
  instead" is the obvious and wrong response.
- **Fix:** grouping is partitioned by resolved instrument and similarity only decides
  within a partition. Entity resolution already passes its fixture set at 100%, so the
  partition is reliable. Within one company the gap was clean.
- **Enforced by:** `verify 1_5b` asserts the cross-company pair stays in two groups *and*
  that its text similarity is above 0.7, so the check cannot pass for the wrong reason.
- **Status:** closed · **Owner:** me

### ISS-013 — The NSE poller must present a browser User-Agent
- **Found:** 2026-10-18, compliance review of step 1.5
- **Severity:** unresolved tension, not a defect
- **Symptom:** NSE's `robots.txt` says `Allow: /` and does not disallow
  `/api/corporate-announcements`. Their bot protection nonetheless returns **no response at
  all** to any non-browser User-Agent - including the conventional honest-bot form
  `Mozilla/5.0 (compatible; trading-desk/0.1; +url)`. Only a full Chrome string works.
- **So:** there is no honest-identification route to data their own robots.txt permits.
- **Kept, on these grounds:** the content is SEBI LODR Regulation 30 disclosure, which
  companies are legally *required* to publish for public dissemination - it is regulatory
  filing data, not a publisher's copyrighted reporting. The path is explicitly allowed by
  robots.txt. Volume is a few requests a day. Use is personal and non-redistributive.
- **But it is a tension, not a clean position**, and the user has been told rather than
  left to discover it. If they would rather not carry it, the poller is one adapter and
  removing it costs nothing but the filings archive.
- **Status:** open, accepted with the user informed · **Owner:** user

### ISS-014 — Both RSS publishers' terms prohibit this use
- **Found:** 2026-10-18, hours after shipping 1.5b, when the user asked for per-feed legal
  detail
- **Severity:** compliance breach, corrected the same day
- **What I checked before building:** robots.txt for every host (all permitted the feed
  path, no crawl-delay) and whether each served an honestly-identified client.
- **What I did not check, and should have:** the terms of use. **robots.txt is not a
  licence.** It says what a crawler may fetch, not what the fetcher may then do.
- **Economic Times** (terms: economictimes.indiatimes.com/terms-conditions) carries an
  explicit "AI and Text/Data Mining Restriction": absent a separate written licence, no one
  may "access, copy, extract, scrape, crawl, index, **cache**, mine or otherwise use the
  content ... for the purpose of ... **(d) any other automated or technology-enabled
  summarization or aggregation in order to generate real-time answers, outputs or
  derivative content**", and it "applies regardless of whether such access is automated ...
  or manual". The only carve-out is search-engine indexing. An automated daily briefing is
  the described use, almost word for word.
- **Mint / HT Media** (terms: hindustantimes.com/terms-of-use) prohibits using "any
  'deep-link', 'page-scrape', '**robot**', '**spider**' or other **automatic device,
  program, algorithm or methodology** ... to access, acquire, copy or monitor any portion of
  the Platform/Website or any content or information". No personal-use exception on that
  clause.
- **Action taken the same session:**
  - `config/news_sources.yaml` `feeds:` is now empty; both are under `excluded` with the
    clause quoted and a link to the terms.
  - **All 64 collected articles and the source rows were deleted.** Content that should not
    have been gathered should not be retained.
  - `verify 1_5b` now fails if any feed from a forbidding publisher is configured, and
    asserts zero article rows. Induced to confirm both fire.
  - The adapter and grouping logic are kept and unit-tested. What is missing is permission,
    and a licensed feed drops into the same list.
- **Lesson:** robots.txt plus an honest User-Agent was not sufficient diligence. The terms
  of use are the binding document and must be read *before* a source is wired in, not after.
- **Second-order lesson:** inducing this guard by editing the real config caused a real
  fetch of 28 ET articles, which then had to be purged too. A compliance guard must be
  induced against a temporary config, never the live one.
- **Status:** closed · **Owner:** me

### ISS-020 — A TimescaleDB restore failed once and will not reproduce
- **Found:** 2026-10-18, step 1.9, on the first restore drill ever run
- **Severity:** unknown, which is the problem
- **Symptom:** `pg_restore` of a `pg_dump` taken minutes earlier aborted with
  ```
  COPY failed for table "metadata":
  duplicate key value violates unique constraint "metadata_pkey"
  Key (key)=(exported_uuid) already exists
  ```
- **What fixed it:** TimescaleDB's documented restore procedure — `CREATE EXTENSION`,
  `timescaledb_pre_restore()`, restore, `timescaledb_post_restore()`.
- **Why it is open rather than closed:** inducing the guard afterwards showed the naive
  path succeeding against **the same dump that produced the failure**, and against all
  five dumps taken that day. The failure is real (the error text is verbatim) but not
  reproducible, so the mechanism is unknown. A plausible story — that `exported_uuid` is
  written into the source catalogue by `pg_dump` and then re-inserted by the restore —
  does not survive the fact that the same dump restores cleanly now.
- **My error, recorded because it is the instructive part:** I wrote "the TimescaleDB
  dance is mandatory, not optional" and "every backup taken before today would have been
  worthless" into the adapter docstring, from a single observation. Both claims were
  unsupported. The induction is the only reason they did not stay in the repo.
- **Current position:** the pre/post restore calls stay — cheap, documented, harmless, and
  the one time it mattered it mattered completely. The *claim* that they are required has
  been removed from the code and from `docs/15`.
- **What would close this:** a reproduction. Most likely candidate to try is a dump taken
  from a database that has never been dumped before, restored into a database created
  before the extension exists. Worth ten minutes the next time the schema changes.
- **Status:** open · **Owner:** me

### ISS-021 — Setup A's delta bands are unsatisfiable on most real NSE chains
- **Found:** 2026-10-19, step 2.5, running the structure builder against archived chains
- **Severity:** **high.** Setup A cannot produce a structure for 15 of 25 universe names,
  so the setup would almost never fire — and would look like a working system doing it
- **Measured, on 25 real chains captured 2026-10-16 at 11 DTE:**
  **10 of 25** have a strike in *both* the long band (delta 0.50–0.55) and the short band
  (0.25–0.30). No structure is buildable for ADANIENT, BHARTIARTL, COALINDIA, HINDUNILVR,
  INFY, ITC, KOTAKBANK, LT, M&M, NTPC, RELIANCE, SBIN, SUNPHARMA, TATASTEEL or TCS.
- **Why:** NSE strike spacing is coarse relative to a 0.05-wide delta band. Delta steps
  straight over the band between adjacent strikes — on the NIFTY weekly it goes 0.73 →
  **0.55** → 0.41, skipping 0.25–0.30 entirely. Compounding it, only 15–47 of each chain's
  strikes carry a delta at all; the rest come back null from the feed.
- **Why it matters now rather than at 2.8:** `docs/10`'s Phase 2 exit says "If the dry run
  shows the setups almost never fire, fix that now — it's a threshold problem, and
  discovering it after the agents are built means rebuilding them." The archived chains
  make the discovery available today, before any agent exists.
- **Options, none taken yet — this is a rulebook change and so the user's call:**
  1. **Widen the bands** (0.45–0.60 long, 0.20–0.35 short). Simplest, and changes the
     risk profile of every structure.
  2. **Select the nearest strike to a delta target** rather than requiring band
     membership, with a documented maximum deviation. Keeps intent, always yields a
     structure where the chain is liquid at all.
  3. **Select by moneyness** (% from spot) instead of delta, with delta reported.
  4. Accept a much smaller tradable universe per day.
- **My recommendation: option 2.** "Closest to 0.525, reject beyond 0.45–0.60" preserves
  the rulebook's intent and degrades honestly on a coarse chain, rather than silently
  trading a different structure from the one specified.
- **Not acted on:** changing a documented rulebook threshold is a decision, not a fix.
  `verify 2_5` fails on it so it stays visible.
- **Status:** open · **Owner:** user

### ISS-022 — Flat 0.25% risk implies a much larger account than expected
- **Found:** 2026-10-19, step 2.7, by writing a sizing fixture that could not trade
- **Severity:** not a defect — a fact about the rulebook that the capital figure in
  `docs/07` has to reckon with
- **The arithmetic:** a NIFTY debit spread with a ₹140 max loss per unit and a 75 lot
  risks **₹10,500 per lot**. At flat 0.25% risk per trade (FR-6.3), one lot needs
  **₹42,00,000** of equity. At ₹10,00,000 the budget is ₹2,500 and the correct answer is
  **zero lots** — which the sizing returns, with a reason, rather than rounding up.
- **Why it is easy to miss:** the gate behaves perfectly. It refuses, states why, and
  never breaches. Nothing looks broken; the briefing simply never produces a tradable
  size, and the cause is capital rather than code.
- **Options:** raise the risk fraction (changes the whole risk model and needs a DEVLOG
  entry); prefer narrower spreads with a smaller max loss per unit; trade stock options
  where lot value is lower; or accept that index structures need a larger account.
- **What I did:** nothing to the rulebook. `verify 2_7` reports the minimum viable
  capital as a measured line so the number is visible rather than inferred.
- **Related:** `docs/07` has a blank for the capital figure, and this is the input it
  needs. Also interacts with ISS-021 — a narrower spread would help both.
- **Status:** open · **Owner:** user

### ISS-023 — Trading costs are ~13% of a real spread's best case
- **Found:** 2026-10-19, step 2.8, costing the AXISBANK structure built in 2.5
- **Severity:** informational now, decisive once the dry run has data
- **Measured:** a four-order round trip on a ₹7,500-per-leg turnover costs **₹272.30**
  (brokerage ₹80, STT ₹15, slippage ₹150, the rest statutory) against a **₹2,086.50**
  max gain per lot. **Costs consume 13% of the best possible outcome**, and far more of a
  typical one, since the exit target is 60% of spread width rather than the maximum.
- **The largest single component is slippage at ₹150**, which is an *assumption*
  (0.5% of premium per leg) and not a measured number. It cannot be measured until there
  are real fills, so the figure above is a guess that happens to dominate the total.
- **Why it matters:** docs/10 wants hypothetical P&L "after full costs". A 13% haircut on
  the best case means marginal setups are losers after costs, and the dry run will show
  that once it has history to run over.
- **Status:** open, informational · **Owner:** me (remeasure slippage once fills exist)

### ISS-025 — Stored filings are subject lines, not content; EARNINGS_SURPRISE is unreachable
- **Found:** 2026-10-19, step 3.2, generating the labelling sheet from the real archive
- **Severity:** high — it blocks the most important catalyst type in Setup A
- **Measured:** 1,542 filings, body length **min 17 / mean 144 / max 879 characters**. The
  bodies are stubs of the form *"X has informed the Exchange about Y"*. The actual content
  is in a PDF attachment that the 1.5a adapter records a URL for and never fetches.
- **Consequence:** the subject router settles 60% of filings (718 routine + 207 catalyst),
  and the remaining **617 that "need the document" have no document to read**. The LLM
  classifier is handed a 144-character stub and can only answer NONE honestly.
- **The specific gap:** `EARNINGS_SURPRISE` and `GUIDANCE_CHANGE` — the two types Setup A
  was designed around — are **structurally unreachable**. They live inside "Outcome of
  Board Meeting" (139 filings) and "Press Release" (97), and the subject says only that
  results were declared, never whether they beat. No amount of classifier tuning fixes
  this; the information is not in the database.
- **Also:** the RSS side holds **25 articles, all SEBI, spanning 3 days**. Journalism would
  carry the earnings numbers, but no commercial Indian publisher permits this use
  (ISS-010), so that route is closed on compliance grounds rather than technical ones.
- **Options, cheapest first:**
  1. **Fetch and parse the PDF attachments.** Checked, not assumed: **1,524 of 1,542
     filings carry a real `https://nsearchives.nseindia.com/...` URL** in
     `filings.attachments`, and **all 507 of the "needs reading" subjects have one**
     (Outcome of Board Meeting 139/139, Press Release 97/97, General Updates 178/178,
     Updates 93/93). So nothing needs re-crawling — the pointers are already here.
     NSE's terms permit the documents (mandated public disclosure, the same basis as
     the filings themselves — to be re-read before acting, per the ISS-014 standard).
     Earnings PDFs are tabular and awkward, but the numbers are in them.
  2. **Dhan's fundamentals endpoint**, if the paid Data API exposes reported vs estimated
     — turns EARNINGS_SURPRISE into arithmetic rather than classification, which is
     better anyway. Needs the ₹499/month subscription to check.
  3. **Narrow Setup A to the five reachable catalyst types** for v1 and drop the two
     earnings ones until a source exists. Honest, and it ships.
- **My recommendation:** (3) now so Phase 3 is not blocked, with (1) as the next data
  step. Taking (3) alone permanently would gut Setup A, so it is a staging decision, not
  a final one.
- **Status:** open, needs Ajay's call · **Owner:** Ajay to choose, me to build
### ISS-026 — The Fundamental agent is blind to promoter pledge and sell-down
- **Found:** 2026-10-19, building step 3.3
- **Severity:** correctness
- **Symptom:** `docs/02` names pledged shares as the Fundamental agent's headline
  disqualifier. **0 of 1,542 stored filings mention pledge, encumbrance or promoter**
  (measured, `verify 3_3`). Those disclosures are filed under SEBI's SAST and PIT
  regimes, which NSE publishes on a different endpoint from the one step 1.5 polls.
- **Impact:** a clear report from the Fundamental agent does not mean the company is
  clean, it means nothing was found in the half of the disclosure universe we fetch.
  The agent says so in `unavailable` on **every** call, so the briefing cannot read the
  silence as evidence — but the veto itself is weaker than `docs/06` assumes.
- **Related:** the same shape as ISS-025 — the gate exists, the data does not.
- **Options:** (1) add an NSE SAST/PIT poller in a Phase 1 follow-up, same pattern as
  the filings poller and free; (2) accept the gap for v1 and keep the disclosure;
  (3) check whether the paid Dhan Data API exposes shareholding-pattern changes.
- **Recommendation:** (1), as a small data step after the filing PDFs (ISS-025). It is
  the same crawler with a different path.
- **Status:** open · **owner: user** — a decision about data scope, not a code defect

### ISS-027 — No sector map exists, so correlated names cannot be detected
- **Found:** 2026-10-19, building step 3.3
- **Severity:** correctness
- **Symptom:** `docs/02` asks the Portfolio Context agent to report sector
  concentration. **9 config files, none naming a sector** (measured, `verify 3_3`),
  and the Dhan instrument master carries none either.
- **Impact:** the agent can tell you that you hold three positions; it cannot tell you
  they are three private banks. That is the exact failure `docs/02` names — "the
  seventh correlated long Nifty-bank bet". Per-symbol concentration still works.
- **Why not just write one:** a hand-written sector map for 25 names would look
  authoritative and be unsourced, which is the class of invented data CLAUDE.md rule 6
  exists to keep out. The agent reports it as unavailable instead.
- **Options:** (1) take NSE's own sectoral index membership (NIFTY BANK, NIFTY IT,
  NIFTY FMCG …) as the sector map — published, free, and already the market's
  definition of correlated; (2) buy a classification; (3) leave it for v1.
- **Recommendation:** (1). It is one config file with a source URL, and it is the
  definition the market actually trades.
- **Status:** open · **owner: user** — one decision, then an hour of work

### ISS-028 — Two of Setup A's calendar inputs have no source, and both fail open
- **Found:** 2026-10-19, wiring the strategist graph in step 3.4
- **Severity:** correctness
- **Symptom:** `SetupACandidate` carries `in_ban_list: bool` and
  `results_date_in_window: date | None`. Nothing in the system fetches either, so every
  cycle supplies `False` and `None` — and both of those are the **permissive** answer.
  The calendar gate therefore reports PASS on a banned symbol and on a company
  reporting results inside the expiry window.
- **Why it matters:** this is the one place in the decision path where missing data
  reads as "fine" rather than NO_DATA. Everywhere else an absent input blocks (rule 6);
  here the types have no room for "unknown", so the gate cannot tell.
- **Mitigation now:** the cycle reports both as missing inputs on **every** run
  (`CycleOutcome.missing_inputs`), and `verify 3_4` fails if it ever stops doing so.
  That makes the gap visible in the briefing rather than silent.
- **Options:** (1) fetch the NSE F&O ban list daily — it is published, free, and one
  more poller of the kind step 1.5 already has; (2) widen both fields to tri-state so
  unknown blocks, which is correct but makes every cycle fail until (1) is done;
  (3) accept it for v1 with the disclosure.
- **Recommendation:** (1), then (2). The ban list is a few hours of work and removes
  the only fail-open input in the gate chain. An earnings calendar is harder — there is
  no free authoritative source — and interacts with ISS-025.
- **Status:** open · **owner: user** — a data-scope decision

### ISS-033 — Expiry-day captures poison the ATM IV series
- **Found:** 2026-10-19, first universe-wide run of `verify 1_8` after the backfill
- **Severity:** data-correctness — it moves `iv_rank`, which gates both setups
- **Symptom:** `atm_iv_from_our_archive_is_believable` failed with NIFTY at **2.1%**
  against a 5-80% band, while every stock read 17-21%.
- **Not a capture bug.** 19 Oct is NIFTY expiry (DTE=0) and the snapshotter takes
  `expiries[0]`. The ATM call printed Rs 0.05 (11 points OTM, no time value) and the
  ATM put Rs 11.00 (intrinsic is 11.05). An IV near zero is the correct answer for a
  contract with no time left, so Dhan, the adapter and the archive are all right.
- **Impact:** `atm_iv_series` takes the last snapshot of each day with **no expiry
  filter**. NIFTY expires weekly, so about one day in five contributes a ~1-2% reading.
  When the series switches from the realised-vol proxy to real IV (needs 30 days of
  history), the percentile window carries a ~20% artificial floor and every normal
  day's IV rank reads too high. Setup A gates on `max_iv_rank: 60`; Setup B flips
  regime at 70 and 35. Inflated ranks block Setup A and push Setup B to the credit
  variant wrongly.
- **Also latent:** even away from expiry the series compares a 3-DTE IV with a 25-DTE
  IV. A proper IV-rank series uses a constant-maturity reading, which is a larger
  piece of work than the expiry filter.
- **Recoverable?** Yes, unusually for chain data. The raw snapshots are complete and
  carry their expiry, so this is a derivation to fix, not history that was lost.
- **Not fixed yet:** no effect until the archive reaches 30 days, and the fix is a
  scope decision (filter low DTE, or capture a second, further expiry).
- **Status:** open · **Owner:** me, pending Ajay's call on which fix

### ISS-036 — No market-wide regime input: a stock is judged without reference to the index
- **Found:** 2026-10-20, from the owner's requirement that ideas respect "prevailing
  market condition, going up or going down"
- **Severity:** correctness — a gap in the setup, not a defect in the code
- **Symptom:** `technical_confirm` measures a 20-day range break and a volume ratio
  on the stock alone. `SetupACandidate` carries `trend_regime`, which the Technical
  agent computes **for that stock**. Nothing in the gate chain consults the index.
- **Impact:** RELIANCE breaking out while NIFTY rolls over is a materially worse
  idea than the same breakout in an index uptrend, and the nine gates cannot tell
  the two apart. Both would be briefed with the same confidence.
- **Not yet a fix.** Adding a gate changes which ideas are briefed, so it needs a
  DEVLOG decision and a measured base rate first (see the ISS-034 backtest work).
- **Status:** open · **Blocks:** nothing

### ISS-037 — The three scheduled chain captures have never run; only 2 days of IV history exist
- **Found:** 2026-10-20, counting the parquet archive while testing the claim that
  "you have option data"
- **Severity:** data-correctness — **and the affected data is not recoverable**
- **Symptom:** `data/chains/` holds two dates, 2026-10-16 and 2026-10-19, with one
  snapshot per symbol per day, timestamped 23:13 and 18:13 IST. Those are our two
  manual runs. The scheduled 09:30 / 12:30 / 15:15 captures have produced nothing.
- **Cause (hypothesis):** `uv run trading-desk run` has never been left running as a
  daemon. The timetable exists and `verify 3_7` passes; nothing has hosted it.
- **Impact:** `MIN_SAMPLES = 30` trading days before `iv_rank` returns any value and
  `DEFAULT_WINDOW = 252` for a meaningful percentile. We have **2**. Until 30 accrue,
  the IV gate returns NO_DATA and blocks every Setup A candidate that gets that far,
  and Setup B cannot run at all. An after-close snapshot is also not the intraday
  reading the series is meant to hold.
- **Not recoverable:** an option chain cannot be backfilled from any source we have.
  Every trading day without a capture is permanently absent from the percentile
  window. This is the only clock in the project that cannot be rewound, which makes
  it the most urgent item regardless of severity ranking.
- **Status:** open · **Blocks:** 3.9 sign-off, and every day it stays open costs a day

### ISS-038 — `PipelineReport.llm_calls` over-counts billed calls
- **Found:** 2026-10-20, writing the ISS-035 induced tests — an assertion on
  `llm_calls == 1` failed with 2 while the fake client had recorded one call.
- **Severity:** cosmetic
- **Symptom:** `NewsAgent.run` does `classified = self._classifier.classify(...)`
  then `calls += 1` unconditionally. When the exchange subject already decides the
  catalyst type the classifier answers without calling a model, but the counter
  still increments.
- **Impact:** the `model_calls` measurement recorded against the NEWS node in
  `cycle_nodes` reads high. **Billing is unaffected** — real cost comes from the
  `llm_calls` ledger table, which records actual requests. So this misleads a
  human reading the node log, nothing more.
- **Fix is one line** (increment only when a call was made), deliberately not done
  inside the ISS-035 change to keep that diff reviewable.
- **Status:** open · **Blocks:** nothing

### ISS-039 — Measured: `technical_confirm` has no directional edge, and is negative at 1-2 weeks
- **Found:** 2026-10-20 by `scripts/backtest_technical.py`, the first backtest of any
  signal in this project. Run because the owner asked for 90% directional accuracy.
- **Severity:** correctness — it questions the premise of half of Setup A
- **Method:** the production gate (`domain/setups/setup_a.technical_confirm`) with the
  live thresholds from `config/setups.yaml`, over 22,056 bar-days: 24 stocks, Jan 2023 to
  Oct 2026. Window ends at the tested bar, so there is no lookahead. Every hit rate is
  reported against the unconditional base rate for the same direction, horizon and
  symbols, because 2023-2026 trended up and a long-biased signal looks good against it.
- **Measured:** the gate fires on 1,021 of 22,056 bar-days (4.6%) — selective, as intended.

  | direction | horizon | fired | hit rate | base rate | edge | sigma |
  |---|---|---|---|---|---|---|
  | BULLISH | 5d | 618 | 47.2% | 52.6% | **-5.4%** | -2.7 |
  | BULLISH | 10d | 617 | 48.3% | 53.5% | **-5.2%** | -2.6 |
  | BULLISH | 15d | 617 | 54.1% | 54.9% | -0.8% | -0.4 |
  | BULLISH | 20d | 617 | 54.6% | 55.7% | -1.1% | -0.6 |
  | BEARISH | 5d | 403 | 46.9% | 47.4% | -0.5% | -0.2 |
  | BEARISH | 10d | 399 | 48.4% | 46.5% | +1.9% | +0.7 |
  | BEARISH | 15d | 387 | 45.0% | 45.1% | -0.1% | -0.1 |
  | BEARISH | 20d | 382 | 44.8% | 44.3% | +0.5% | +0.2 |

- **Six of eight cells are noise.** The only two distinguishable effects are both
  **negative**: a bullish close beyond the 20-day range edge on 1.5x volume is *less*
  likely to be higher in one or two weeks than a randomly chosen day. That is consistent
  with short-term reversion after a volume spike, and it is the opposite of what a
  "continuation" setup assumes.
- **Magnitude is the second problem.** Median forward moves are -0.22% to +0.82%. ISS-023
  measured trading costs at ~13% of a real spread's best case, so a move of this size
  would not pay for the structure even when the direction is right.
- **What it does NOT show:** that Setup A has no edge. This is one gate of nine, and the
  catalyst gates carry the thesis — `technical_confirm` is only meant to confirm. What it
  does show is that the confirmation contributes no directional information on its own,
  so it cannot be the reason an idea is briefed.
- **Impact on a trading decision:** `config/setups.yaml` is still `verified: false`, and
  this is the first evidence bearing on it. Three options, none of them mine to pick:
  keep the gate as a liquidity/participation filter while dropping any claim that it
  predicts direction; test the catalyst gates the same way once filings carry documents
  (ISS-025); or re-specify the setup around the reversion the data actually shows.
- **Trade-level follow-up, 2026-10-20** (`scripts/backtest_trades.py`). The directional
  test above says nothing about money, so the signal was simulated as a trade: entry at
  the open *after* the signal, stop 1.0x ATR(14), target 1.5x ATR, 20-session time stop,
  0.25% round-trip costs. A bar containing both stop and target is scored as a stop,
  because a daily bar cannot say which came first.

  | | stocks (24) | indices (NIFTY, BANKNIFTY) |
  |---|---|---|
  | trades | 1,023 | 39 |
  | win rate | **37.0%** | **23.1%** |
  | average win | +2.90% | +1.22% |
  | average loss | -2.50% | -1.48% |
  | expectancy per trade | **-0.50%** | **-0.86%** |
  | exits | 370 target, 638 stop, 15 time | 9 target, 30 stop |

- **The arithmetic that matters:** at a 1.5:1 target-to-stop the breakeven win rate is
  40%. Realised wins average +2.90% against losses of -2.50%, a ratio of 1.16, which needs
  **46.3%** to break even. The measured rate is 37.0%. The gap is not a tuning problem; the
  signal is on the wrong side of its own breakeven.
- **Only 4 of 24 names are net positive** (MARUTI +15.5%, INFY +13.8%, HCLTECH +8.8%,
  SUNPHARMA +8.3%) against 20 negative, the worst being ADANIENT -71.9% and HINDALCO
  -61.6% in summed per-trade returns. With ~40 trades per name none is individually
  conclusive; the 20-to-4 split is.
- **And this is the generous version.** It is the *underlying*, with equity costs. Setup A
  proposes a debit vertical, which pays a bid-ask spread on two legs and bleeds time value
  daily — ISS-023 put that at ~13% of a spread's best case. The options expression of the
  same signal is worse than these figures, not better.
- **Status:** open · **Blocks:** `setups.yaml` sign-off (`verify 2_3`), and any claim about
  expected hit rate
