# ISSUES-ARCHIVE — closed issues, kept for grepping

Moved out of `ISSUES.md` when closed, so the live file carries only open work. A closed
issue is still evidence: it records a failure mode that was real once and can return.

---

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

### ISS-024 — A trigger drill wrote a row into a ledger that cannot be deleted
- **Found:** 2026-10-19, step 3.1, first run of the `llm_calls` append-only drill
- **Severity:** resolved, but the lesson generalises to every append-only table
- **What happened:** the drill inserted a 134-paise row into the real database to prove
  UPDATE, DELETE and TRUNCATE are refused. All three were refused — correctly — which
  meant the verification artefact itself could not be removed, and it would have counted
  against October's LLM cap for the rest of the month.
- **Cleared by** `alembic downgrade 0005 && alembic upgrade head`, which dropped and
  re-created the table. That also proved the downgrade path works, which was untested.
- **Fix:** every database check in `verify 3_1` now runs inside a transaction that is
  rolled back in a `finally`. The triggers still fire inside the transaction, so the
  induction is just as real; nothing survives it. `llm_calls` reads 0 rows after a full
  verify run, checked.
- **The general rule:** an append-only table cannot be drilled against in place. Either
  roll the transaction back, or drill against a scratch database — the same conclusion
  `docs/15` reached for restores, arrived at from the opposite direction.
- **Checked, not assumed:** the step 1.7 journal tables were drilled the same way, so
  they looked like they would have the same problem. They do not — all five read 0 rows.
  The database has been reset since. Nothing to clean up there.
- **Status:** fixed · **Owner:** me

