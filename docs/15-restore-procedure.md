# Restore procedure

**A backup you have never restored from is not a backup.** This file is the
procedure, and the drill log at the bottom is the evidence it has been run.

---

## 1. What is being protected, and what is not replaceable

| Data | Replaceable? |
|---|---|
| **Option-chain archive** (`data/chains/`, Parquet) | **No.** Three IV-surface captures a day. Nobody sells this history cheaply and it cannot be re-fetched. A lost month is a lost month |
| **Journal** (`briefing_ideas`, `user_decisions`, `outcomes`, `override_log`, `calibration_snapshots`) | **No.** It records what was believed at the time, which is the one thing that cannot be reconstructed afterwards |
| `bars_1d` | Yes, slowly — re-fetchable from Dhan history |
| `filings`, `articles` | Partly. NSE's announcements endpoint serves a limited window, so anything older than that window is gone |
| Schema | Yes — `make db-migrate` rebuilds it from `alembic/versions/` |

The two "No" rows are the reason this step exists.

## 2. Taking a backup

```
make backup
```

Writes `data/backups/<UTC timestamp>/` containing:

- `postgres.dump` — a custom-format `pg_dump` of the whole database
- `chain_archive.sha256` — a digest over every Parquet file in `data/chains/`,
  hashing each file's **path as well as its contents**, so a renamed or missing
  partition changes the digest

It then reports backup freshness and alerts CRITICAL if the newest backup is
older than 30 hours.

## 3. Proving it works — run this, do not assume it

```
make restore-drill
```

This restores the newest backup into a **scratch database**
(`trading_desk_restore_drill`), compares every table's row count against the
live database, checks the archive digest, and drops the scratch database again
— including when the restore fails.

It passes only when rows were actually compared. Fifteen empty tables match
fifteen empty tables and prove nothing, so a drill that compared zero rows is
reported as a failure.

**The drill never writes to the live database.** The target is computed by
`domain.backup.scratch_name` and checked by `refuse_unsafe_restore` before
anything is created.

## 4. Restoring for real, after an actual loss

Do this deliberately, not in a hurry.

1. **Stop everything that writes.** `make down`, and confirm no scheduler is
   running. A restore racing the snapshotter produces a database that matches
   neither.
2. **Back up the damaged database first**, even though it is damaged:
   `make backup`. It may hold rows the older backup does not, and you cannot
   get them back after step 4.
3. **Pick the backup** — `ls data/backups/`. Newest is not automatically right:
   if the damage was silent, the newest backup contains it.
4. **Restore into a scratch database first and look at it**, never straight
   over the live one:
   ```
   docker compose exec -T postgres dropdb -U trading_desk --if-exists trading_desk_restore_drill
   docker compose exec -T postgres createdb -U trading_desk trading_desk_restore_drill
   docker compose exec -T postgres psql -U trading_desk -d trading_desk_restore_drill \
       -c "CREATE EXTENSION IF NOT EXISTS timescaledb" \
       -c "SELECT timescaledb_pre_restore()"
   docker compose exec -T postgres pg_restore -U trading_desk \
       -d trading_desk_restore_drill --no-owner < data/backups/<stamp>/postgres.dump
   docker compose exec -T postgres psql -U trading_desk -d trading_desk_restore_drill \
       -c "SELECT timescaledb_post_restore()"
   ```
   Query it. Confirm the data you expect is there and the damage is not.
5. **Only then** rename: drop the live database and rename the scratch one into
   its place. There is no shortcut that is safer than this.
6. **Verify the chain archive separately.** It is on disk, not in Postgres.
   Compare its digest against `chain_archive.sha256` in the backup.
7. `make verify STEP=1_9` and `make verify STEP=1_5a` to confirm the system
   agrees the data is intact.

### The TimescaleDB step in 4 is not optional decoration

The first drill ever run failed with:

```
pg_restore: error: COPY failed for table "metadata":
duplicate key value violates unique constraint "metadata_pkey"
Key (key)=(exported_uuid) already exists
```

`timescaledb_pre_restore()` / `post_restore()` is Timescale's documented
procedure for it. **The failure has not been reproduced since** — the naive
path now succeeds against that same dump and every later one — so the trigger
is unknown and tracked as ISS-020. Include the calls anyway: they are cheap and
documented, and the one time it mattered, it mattered completely.

## 5. Known gap: there is no offsite copy

`data/backups/` is on the same disk as the database. That protects against a
dropped table, a bad migration and a mistaken `DELETE`. **It does not protect
against losing the laptop**, which is the scenario the option-chain archive
most needs protecting from.

Closing it needs a versioned object-storage bucket — a provider, an account and
credentials, which is a decision and a cost for the operator rather than
something to assume. `verify 1_9` fails the `backups_are_copied_offsite` check
by design until then, so the gap stays visible.

## 6. Drill log

Append a row every time a drill runs. A drill that was not recorded did not
happen.

| Date | Taken by | Backup | Result |
|---|---|---|---|
| 2026-10-18 | automated, `verify 1_9` | `20261018T180805Z` | **PASS** — 15 tables (5 non-empty) matched exactly, 3,448 rows, archive digest matched over 26 Parquet files |
| 2026-10-18 | automated, first attempt | `20261018T180220Z` | **FAIL** — `pg_restore` aborted on `metadata_pkey` / `exported_uuid`. Fixed by adding the TimescaleDB pre/post restore calls; see ISS-020 |

**Still outstanding: a restore performed by a human.** `docs/10` says the step
is done when *you* have personally restored from backup once. The automated
drill proves the mechanism; it does not prove the procedure in section 4 reads
clearly at the moment you need it. Walk section 4 once by hand and add a row.
