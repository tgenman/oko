# PostgreSQL foundation verification — 2026-10-24

Scope: the first preparatory change for [#127](https://github.com/tgenman/oko/issues/127).
The application still uses SQLite. This report does not establish PostgreSQL
product compatibility or deployment readiness.

## Changes

- Connection locking, transaction ownership and commit callbacks are separated
  from reader projection SQL. ReaderVersions registers pre-commit maintenance;
  offline fixture connections register the same maintenance.
- PostgreSQL migration history records consecutive versions, names and SHA-256
  checksums. An exclusive advisory lock protects a release; all pending SQL and
  ledger writes commit together. Failed release DDL rolls back. A read-only
  verifier rejects missing, incomplete, modified and newer schema histories.
- Migration SQL is trusted release code and cannot contain transaction control.
- Psycopg 3.3.6 is pinned. An obligatory PostgreSQL 18 CI job runs integration
  checks separately from the existing checks and shard coverage audit.

## Executed checks

The connection refactor was checked in an isolated checkout based on
`1aef592c8fc55e47d68d5b8be4c81acdede8ef2e` with the task tree
`287e7fa296d2c72d87120f2c38a48b3185d013fa`.

`./scripts/check all --compact --in-place`:

- Contracts, types, syntax, lint, build, HTTP, entrypoint and diff passed.
- Python: 911 passed. Development tools: 264 passed. Node: 113 passed.
- Playwright: 220 passed, 2 skipped, 1 cancelled by its 20-second timeout:
  `suite browser releases every owned process after success`.
- Repeating the entire `tests/browser/test_browser_suite.ts` file passed all
  three success/failure/interrupt process-cleanup cases. The full run remains
  recorded as failed; the repeat covers this file only.

An earlier Python run passed 911 tests but the runner rejected it because a
trailing blank line was removed during execution. The isolated checkout run
above supersedes that mutable-checkout evidence.

Real PostgreSQL 18 in a dedicated local container with temporary synthetic data:

- Failed second migration rolls back the entire release, including first DDL and ledger.
- Successful migration is idempotent and preserves data after reconnection.
- Pending, altered, missing-history and newer schemas are rejected.
- A competing connection cannot migrate while another owns the advisory lock.
- Verification succeeds inside a read-only transaction; migration rejects a
  connection already inside a transaction.

All four PostgreSQL scenarios passed. A prepared checkout also passed 20 tests
covering the first three PostgreSQL scenarios, reader versions and action
transactions. Strict mypy and staged checks passed. Independent review found no
blocking issue in the connection extraction or migration framework.

Sandbox restrictions prevented the first runner and a direct CI-shard test
attempt from using `ps`; those attempts do not count as successful checks.

## Remaining scope of #127

The native PostgreSQL schema, SQL and triggers, reader database credentials,
Store integration, recovery, full HTTP and Playwright product scenarios on
PostgreSQL remain to implement and verify. No personal data or working instance
was changed by these tests.
