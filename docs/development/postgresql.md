# PostgreSQL storage and development

PostgreSQL is the only Oko database in deployment, local development and tests.
Legacy application storage and import commands have been removed.

## Data ownership

Publication text, structured content, processing results, subscriptions, reader
versions and durable jobs live in PostgreSQL. Publication media remain files under the reader data root. PostgreSQL
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
A new library starts with work disabled. Restarting preserves its saved power state.
The reader requires `--database-url-file` and does not create a local database.

## Transactions and SQL

The Store owns an in-memory snapshot of global, folder and feed setting overrides,
Codex/llama.cpp configuration, and the processing-related feed fields (kind,
folder, importance, public-AI permission and transport). Each snapshot is loaded
once when its owner opens. Configuration writes update SQL and memory together
under the connection lock; rollback restores previous memory values in reverse
order. A failed rollback discards the SQL connection before undoing memory. A
new Store loads committed state again. Dynamic feed progress and description
remain ordinary live data, and reader revision counters remain freshness facts.
Maintenance that edits configuration directly in SQL must reopen the Store.

PostgreSQL trigger clocks are set once when a write transaction begins, including
its derived projection flush. Plain reads do not send clock-setting SQL. This
makes time consistent within the write operation and refreshes it at the next
transaction. No SQL transaction is retained while background recovery yields
to HTTP requests or waits for external work.

`storage/database.py` defines the row/cursor and transaction boundary. The
PostgreSQL adapter keeps plain reads outside transactions and joins repository
writes to the caller's owned transaction. Nested repository contexts cannot
commit or rescue a failed outer action. Reader projections flush before commit;
callbacks run after durable commit and disappear on rollback.

Repositories use native PostgreSQL statements and psycopg `%s` / `%(name)s`
parameters. There is no runtime dialect translator. Bound values are not SQL.
The connection validates transaction ownership and single-statement execution;
schema changes belong to immutable release migrations.

## Verification

Use an explicitly supplied synthetic PostgreSQL, never a personal database:

```sh
OKO_TEST_POSTGRES_URL='postgresql://test-user:test-password@127.0.0.1:5432/test-db' \
  ./scripts/check python --files tests/reader/test_postgres_migrations.py tests/reader/test_postgres_transactions.py tests/reader/test_postgres_processing.py tests/reader/test_postgres_browser.py tests/reader/test_postgres_cli.py
```

Storage checks require `OKO_TEST_POSTGRES_URL`; absence is an error, not a skip.
Python and browser scenarios use isolated synthetic schemas. Closed seed schemas
are cloned for each scenario; filesystem copies never share a mutable database.
The seed owner keeps schemas alive across child restarts and drops them on exit.
PostgreSQL plan checks use EXPLAIN ANALYZE/BUFFERS; latency measurements run without
that extra work. CI provides PostgreSQL to every storage-dependent test job.

## Local development

Use a separate PostgreSQL database or schema and a private DSN file. Install
migrations explicitly before starting the reader. A different media directory
alone does not create a different library: the database owns its identity.

For disposable checks only:

```sh
docker run --rm -d --name oko-test-postgres -p 127.0.0.1:55439:5432 \
  -e POSTGRES_PASSWORD=synthetic -e POSTGRES_DB=oko_test postgres:18-bookworm
OKO_TEST_POSTGRES_URL='postgresql://postgres:synthetic@127.0.0.1:55439/oko_test' ./scripts/check all --compact
```

Production backup/restore and releases follow [cloud backup](cloud-backup.md) and
[cloud deployment](cloud-deploy.md). A file-only backup is incomplete. Old local
file-copy update, rollback, backup, restore and import commands refuse before
changing an installation. Existing data and historical backups are not deleted.
External Codex/Telethon formats remain owned by those applications; their security
checks do not provide an alternative Oko storage backend.
