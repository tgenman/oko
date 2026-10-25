# PostgreSQL storage

Oko keeps the local SQLite backend and supports PostgreSQL for a cloud reader.
The HTTP contract, reader versions, three workers and file lifetime are shared.
There is no automatic SQLite-to-PostgreSQL import or fallback on database errors.

## Explicit offline SQLite import

Stop both readers, preserve consistent source and target backups, and prepare a
new empty schema with the release migrations above. Use the schema owner's
private DSN, not the runtime reader's restricted credential:

```sh
python -m oko.storage.import_sqlite --source /private/stopped-source-snapshot \
  --target-data /srv/oko/new-library --database-url-file /run/secrets/migrator-dsn \
  --report /private/import-report.json
```

The source must have its existing unlocked `instance.lock` and current publication
schema. The importer refuses live readers, populated target schemas, existing
target directories/reports, symlinks, special files and incompatible table/column
sets. It never switches a running installation. It imports the complete logical
tables and reader projections under the schema lease and a single transaction,
preserves insertion identities and sequence allocation, and disables work before
startup. Source and copied media hashes and per-table counts/checksums are checked.

Regular source files are copied privately, except the SQLite database/sidecars,
instance lock and runtime address/token metadata. Browser/reader account files are
included: explicitly retain the destination's separately backed-up access files
before activation when preserving server login is required. Codex host auth is
outside this library and is not copied. Check absolute paths and portability of
Telegram/client settings before enabling work.

A private `migration-archive/state.sqlite` preserves the complete source database,
including historical `migration_retired_rows`; those historical rows are reported
as archived rather than silently discarded or loaded into the live schema.
The known legacy tables `content_current`, `content_versions`, `filter_restores`,
`retained_theses`, `telegram_read_topics` and `youtube_attempts` are accepted only
when empty; their schemas remain in that archive and zero counts in the report.
Any rows in those tables refuse import rather than hiding live data in an archive.
The legacy `feeds.telegram_topic` column is accepted only if every value is NULL;
its schema is retained in the full SQLite archive and named in `archived_columns`.
A non-NULL topic or any other unknown column refuses import.
Only `reader_feed_counts` rows with a NULL `feed` are archived instead of loaded:
these derived rows cannot join an authoritative feed ID or contribute to reader
navigation/folder counts. The complete SQLite archive retains their original
values; `archived_row_counts` and each table's `source_rows`/`rows` make the
transformation explicit. Counts for actual feeds follow existing Store recovery.
Other invalid rows or NULL constraints are not normalized or silently omitted.
Unknown tables are refused. The source is unchanged. The report and archive may
contain private metadata; do not publish them unredacted. After interruption,
reconcile the prepared directory, transaction outcome and receipt before retrying;
there is no cross-filesystem/database atomic commit or automatic replacement of a
partially attempted target. Keep the original installation and backups until HTTP
and browser acceptance and the explicit operator switch have passed.

## Data ownership

Publication text, structured content, processing results, subscriptions, reader
versions and durable jobs live in the database, as they do in the current SQLite
backend. Publication media remain files under the reader data root. PostgreSQL
stores their descriptions, identities and storage keys. Small Telegram account
avatars retain their existing database representation.

This clarifies the deployment plan's earlier description of all collected
content as files: current publication text is already SQL data. Backups must
capture a consistent database dump together with the corresponding media and
configuration, independently of the running containers.

## Credentials and schema ownership

Use two database identities. The migration identity owns the Oko schema and its
objects. The reader needs schema USAGE, table SELECT/INSERT/UPDATE/DELETE and
sequence USAGE/SELECT. Give it SELECT only on `oko_schema_migrations`; it needs no
CREATE privilege or schema ownership. Database/server administration credentials
are not reader credentials.

The connection string must select the intended schema through `search_path`.
Keep each identity's DSN in its own private regular file, readable only by its
service account. The CLI accepts a file path so passwords stay out of argv and
process environment. Mode 0600 is suitable; a read-only private file also works.
Never commit these files or include them in an image.

## Migration and startup

Stop the reader before applying a release schema:

```sh
python -m oko.storage.postgres_migrations --database-url-file /run/secrets/oko-migration-dsn
python -m oko.storage.postgres_migrations --check --database-url-file /run/secrets/oko-reader-dsn
python -m oko --persistent --no-browser --data /var/lib/oko --database-url-file /run/secrets/oko-reader-dsn
```

Files in `oko/storage/postgres/` are native, ordered PostgreSQL migrations.
Never edit an applied migration. The ledger verifies versions, names and SHA-256
checksums, and rejects missing, incomplete, modified or newer release histories.
All pending migrations and ledger updates commit as one transaction. Migration
SQL is trusted release code and must not contain its own transaction control.

Migration takes an exclusive, schema-scoped advisory lock. A live Store holds a
shared lease that blocks migrations, plus an exclusive instance lease that
prevents another reader from recovering the same schema, even with a different
data directory. Startup verifies compatibility again after obtaining the lease.
Connection close releases both leases. Independent test schemas do not block
each other.

The reader verifies the ledger and never installs PostgreSQL tables or triggers.
A new PostgreSQL library starts with work disabled. `--initially-disabled` remains
the local SQLite first-start option; restarting a PostgreSQL reader preserves
its saved power state.

## Transactions and SQL

`storage/database.py` defines the row/cursor and transaction boundary. The
PostgreSQL adapter keeps plain reads outside transactions and joins repository
writes to the caller's owned transaction. Nested repository contexts cannot
commit or rescue a failed outer action. Reader projections flush before commit;
callbacks run after durable commit and disappear on rollback.

`postgres_sql.py` compiles the repository's bounded SQL dialect, not arbitrary
user SQL. Values stay bound. It handles placeholders, null-safe comparisons,
conflict policies and the existing JSON scalar contract. Native release DDL and
145 native triggers implement the same reader, source, processing and journal
projections. Unsupported statements fail; they never switch to SQLite.

## Verification

Use an explicitly supplied synthetic PostgreSQL, never a personal database:

```sh
OKO_TEST_POSTGRES_URL='postgresql://test-user:test-password@127.0.0.1:5432/test-db' \
  ./scripts/check python --files tests/reader/test_postgres_migrations.py tests/reader/test_postgres_transactions.py tests/reader/test_postgres_processing.py tests/reader/test_postgres_browser.py tests/reader/test_postgres_cli.py
```

Each test creates and drops only its own randomly named schema. The role test
also creates and removes a synthetic reader role. Browser acceptance uses
Playwright, a private temporary DSN file, synthetic sources/models and isolated
contexts. The mandatory PostgreSQL CI job runs these checks against PostgreSQL
18 and retains browser screenshots. Ordinary local checks skip these integration
cases when the explicit database variable is absent.
