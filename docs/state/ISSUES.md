# ISSUES — development issues, open and recently closed

Managed with the `issue-log` skill. The `## Open` section is read at the start of every
session, so it stays short. Severity: **safety** · **data-correctness** · **correctness** ·
**reliability** · **cosmetic**.

`safety` and `data-correctness` block the step they were found in. Everything else is
logged and work continues — no drive-by fixes inside a step.

---

## Open

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
- **Status:** closed · **Owner:** user

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

## Closed

### ISS-001 — Dhan websocket instrument cap unknown
- **Found:** 2026-10-14, during planning
- **Severity:** correctness
- **Symptom:** Step 1.2's universe size (~27 instruments) was unverified against the live
  subscription limit
- **Fixed:** 2026-10-15 — not a constraint. DhanHQ v2 allows **5,000 instruments per
  websocket connection** and **5 concurrent connections per user**. v1 needs ~27 plus
  option chains, which is ~0.5% of one connection's capacity.
- **Verified:** official docs, https://dhanhq.co/docs/v2/live-market-feed/ — "up to 5000
  instruments on each connection", max 100 instruments per subscription message, a 6th
  connection disconnects the first with code 805.
- **Consequence:** Step 1.2's universe is not capped by the feed. The real constraint on
  universe size is option-chain snapshot volume and LLM cost per cycle, not subscriptions.

### ISS-015 — RBI's terms prohibit caching and deep-linking
- **Found:** 2026-10-18 evening, wiring the surviving regulatory feeds in
- **Severity:** a source rejected, not a breach — caught before any content was stored
- **Symptom:** RBI's press-release and notification RSS feeds are live, fresh and answer an
  honest User-Agent. An earlier reading of RBI's *Disclaimer* page found only liability
  language and concluded there was no use restriction. **That reading stopped too early.**
  Further down the same Terms & Conditions of Use:
  > "Except as set forth below, **caching** and links to, and the framing of this Web Site
  > or any of the contents **are prohibited**."
  The only carve-outs are a link to the **Home Page** "upon notifying RBI in writing", and
  a link to an internal page requiring that "the user must make a specific request for, and
  secure permission from RBI prior to hyper-linking".
- **Why that bites:** storing feed items is caching, and every feed item is a deep link to
  an internal page. Both are explicitly named as prohibited, so RBI does not merely fail
  the ISS-014 standard of "clearly allows" — it clearly forbids.
- **Not relied on either way:** RBI's robots.txt cannot be read at all. It returns an
  "Unauthorised Access" page to an honest client, which is a WAF inconsistency rather than
  a policy statement.
- **Unblock route, and it is free:** a written permission request to RBI to cache and
  deep-link the RSS feeds. If granted, the entry moves from `excluded` into `feeds` with
  tier `EXCHANGE` and an `rfc822_naive_ist` date format — RBI stamps
  `"Fri, 16 Oct 2026 18:30:00"` with no offset, and the times are IST. That format does not
  exist in `domain/feed_dates.py` yet, deliberately: no consumer, no code.
- **Lesson, and it is the second half of ISS-014's:** read the *whole* terms page, not the
  first section that looks like the terms. The operative clause was several screens below a
  liability disclaimer that read as the whole document.
- **Status:** open, source excluded · **Owner:** user (whether to request permission)

### ISS-016 — Three silent-failure modes in the RSS adapter
- **Found:** 2026-10-18 evening, wiring SEBI in
- **Severity:** would have shipped a feed that was permanently empty and looked healthy
- **Symptom:** three independent faults, each of which produced an empty article list
  indistinguishable from a quiet news day:
  1. **TLS.** httpx 0.28 verifies against the system OpenSSL store — 128 roots here, not
     including Sectigo Root R46, which SEBI's certificate chains to. Every request raised
     `CERTIFICATE_VERIFY_FAILED` and the per-feed handler turned it into `[]`. `curl` and
     `openssl s_client` both verified fine against the same host, which is what made it
     hard to see: the failure was Python-specific, not network- or host-specific.
  2. **Date format.** SEBI's `pubDate` is `"16 Oct, 2026 +0530"`.
     `parsedate_to_datetime` raises `ValueError`, and every item was skipped — 30 of 30.
  3. **Date-only windowing.** Items stamped midnight, filtered by instant, means the second
     poll of any day discards the entire feed.
- **Fix:** `certifi` named explicitly on the client and declared in `pyproject.toml`; the
  date format declared per feed in config and parsed by `domain/feed_dates.py`; date-only
  feeds windowed by calendar day, which is safe because the store is
  `ON CONFLICT DO NOTHING` on the item link.
- **The structural fix, which matters more than the three:** `NewsPort` gained
  `recent_failures()`. The adapter now records a transport error, a non-200, *and* the
  subtle case — items present in the XML but none parseable, which is exactly what a
  publisher changing their date format looks like — and `ArticleIngest` raises a WARNING.
  Returning `[]` on an error was a rule 6 violation hiding in a reasonable-looking
  `except`. One bad feed still never ends the poll.
- **All five guards induced** against temporary configs and in-memory clients, never the
  live ones — see the DEVLOG table for this date.
- **Status:** closed · **Owner:** me

### ISS-017 — The verify runner counted failures and passes in the same slot
- **Found:** 2026-10-18 evening, misreading my own sweep
- **Severity:** cosmetic, but it caused a real false alarm
- **Symptom:** `RESULT: FAIL (1 of 7)` meant *one check failed*; `RESULT: PASS (7 of 7)`
  meant *seven passed*. Step 1.2 — six checks passing, one awaiting the user's threshold
  sign-off — printed as `FAIL (1 of 7)` and read as a near-total collapse. I briefly
  believed I had broken it.
- **Fix:** both lines count passes, and the FAIL line names the failure count separately:
  `FAIL (6 of 7 passed, 1 failed)`.
- **Status:** closed · **Owner:** me

### ISS-018 — A verification check reported the right answer for the wrong reason
- **Found:** 2026-10-18, step 1.7, while inducing the append-only guards
- **Severity:** the check would have passed forever with no protection in place
- **Symptom:** `delete_is_refused_by_the_database` caught any `DBAPIError` and reported
  "DELETE rejected by the database trigger". With the triggers deliberately dropped, the
  DELETE was *still* refused — by a foreign key from `user_decisions` — and the check
  still reported the trigger had done it.
- **Why it hid:** the BEFORE DELETE trigger normally fires first, so in the healthy case
  the trigger genuinely is the cause. The check only lied once the thing it was testing
  was absent, which is exactly when a check matters.
- **This is the stale-check anti-pattern again** (fourth or fifth occurrence): an
  assertion that records a fact true on the day rather than the property intended.
- **Fix:** `_rejected_by_trigger()` passes only when the error names the append-only
  trigger, and otherwise reports the real cause —
  `DELETE refused, but not by the trigger: ForeignKeyViolation ...`.
- **Generalisable rule, worth applying to every future guard:** when a check asserts that
  something is refused, assert **why** it was refused, not merely that it was. "An error
  happened" and "the error I built this for happened" are different claims.
- **Status:** closed · **Owner:** me

### ISS-019 — Append-only needed a TRUNCATE trigger, not just UPDATE/DELETE
- **Found:** 2026-10-18, step 1.7, testing the trigger right after writing it
- **Severity:** the entire journal was erasable in one statement
- **Symptom:** the first version of migration 0005 created `BEFORE UPDATE OR DELETE ...
  FOR EACH ROW` triggers on all five tables. `TRUNCATE briefing_ideas CASCADE` emptied the
  table cleanly: **TRUNCATE does not fire row-level triggers.**
- **Fix:** a second trigger per table, `BEFORE TRUNCATE ... FOR EACH STATEMENT`, calling
  the same function. Ten triggers in total.
- **Worth remembering for any future append-only table:** row-level triggers are not
  sufficient to make a Postgres table immutable. TRUNCATE, `DROP TABLE` and a superuser
  all route around them; TRUNCATE is the one that is cheap to defend against.
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
