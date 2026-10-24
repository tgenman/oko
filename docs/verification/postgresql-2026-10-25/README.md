# PostgreSQL reader verification — 2026-10-25

Implementation of [#127](https://github.com/tgenman/oko/issues/127), following
[the foundation PR #134](https://github.com/tgenman/oko/pull/134).
All database, source, model and account data below are synthetic. No working
reader installation or personal source was used.

## Implemented boundary

- Native PostgreSQL schema: 43 original tables, derived membership work, 25
  indexes, the reading membership view and 145 native projection triggers.
- Separate, atomic release migrations; immutable version/name/checksum ledger;
  reader startup verifies compatibility and never installs PostgreSQL DDL.
- A schema-scoped shared reader lease excludes migrations while the reader is
  live. A separate exclusive lease prevents two Stores recovering one schema.
- Shared row/cursor and transaction boundary, bound query parameters, explicit
  null-safe comparisons, conflict policies and JSON scalar functions.
- Reader credentials come from a private regular file. The reader role can read
  and mutate application data but cannot create schema or write migration history.
- Publication text/results stay SQL data, as in the current SQLite backend;
  publication media stay files. This corrects the earlier plan's description of
  all collected content as files and preserves current product storage behavior.

## Executed targeted scenarios

Actual PostgreSQL 18, with a separate disposable schema for each case:

- Whole-release DDL/ledger rollback, idempotent migration and reconnection;
  modified, pending, missing and newer schema history rejection; concurrent
  migration exclusion and read-only verification.
- Atomic publication of state/jobs to an independent reader; nested failure
  poisoning, pre-commit projection failure, discarded callbacks and close rollback.
- One Store owner across different data roots; migration blocked by a live reader;
  incompatible schema produces an explicit HTTP storage failure, never SQLite.
- RSS verification and feed/folder settings over HTTP, repeated collection
  without duplicates, text/media delivery, reader version protocol, restart and
  explicit Viewed. Injected file-cleanup failure remains recoverable by replay.
- One YouTube publication shared by two feeds; deleting its primary feed keeps
  the publication and PNG for the remaining feed; Viewed removes the media.
- Filter result and job completion commit/rollback together; summary after
  restart; terminal/source-wait preservation; rejection of late old success/error;
  hidden-material folder completion; captured execution choice recovery; ADR-0001
  keeps completed results when future model/reasoning/Fast choices change.
- Real CLI uses the private DSN file, preserves data on restart and creates no
  SQLite file. A group/world-readable credential file is rejected before launch.
- Playwright logs in, creates an RSS feed through the UI, renders its image,
  filters/summarizes it, restarts the reader with its remembered session, marks
  it Viewed and logs out. No browser page errors; screenshots inspected.

Strict mypy and browser TypeScript checks passed. Independent review found one
startup error-handling omission (`SchemaMismatch`), fixed and verified with an
HTTP regression case. Follow-up review accepted the schema/instance leases.
The final isolated full-project run and CI results are recorded below once complete.

![Password login backed by PostgreSQL](login.png)

![Collected material, image, filtering and summary](reader.png)

## Limits

These are synthetic storage and product tests, not a production load benchmark.
The cloud VM, TLS, host Codex helper, backups, monitoring and deployment are
separate subsequent tickets. Historical SQLite-only reports do not prove this
PostgreSQL implementation.
