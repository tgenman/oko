"""Native PostgreSQL connection with the repository transaction ownership rules.

Repositories supply native PostgreSQL statements; values remain bound. Schema installation belongs to
release migrations, never to reader startup or repository construction.
"""
from _thread import RLock
from collections.abc import Callable, Sequence, Mapping
from typing import Any, Literal

import time
import re
from functools import lru_cache

import psycopg
from psycopg.pq import TransactionStatus

from oko.storage.rows import Record
from oko.storage.postgres_migrations import verify, verify_reader_permissions, schema_lock_key
from oko.storage.postgres_schema import MIGRATIONS


@lru_cache(maxsize=2048)
def _command(query: str, *, allow_write_cte: bool = False) -> str:
    """Validate transaction ownership without translating the SQL dialect."""
    tokens = re.findall(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|--[^\n]*|/\*[\s\S]*?\*/|[A-Za-z_]\w*|;", query)
    words: list[str] = [token.upper() for token in tokens if not token.startswith(("'", '"', '--', '/*'))]
    if not words:
        raise ValueError('Empty storage query')
    if ';' in words[:-1]:
        raise ValueError('One repository statement is required')
    command = words[0]
    if command not in ('SELECT', 'WITH', 'INSERT', 'UPDATE', 'DELETE'):
        raise ValueError('Use the owned transaction interface; schema changes require migrations')
    if command == 'WITH' and not allow_write_cte and any(word in ('INSERT', 'UPDATE', 'DELETE') for word in words[1:]):
        raise ValueError('Data-changing CTEs must use an explicit repository write')
    return command


def _records(cursor: psycopg.Cursor[Any]) -> Callable[[Sequence[Any]], Record]:
    columns = tuple(column.name for column in cursor.description or ())
    return lambda values: Record(columns, values)


class PostgresConnection:
    def __init__(self, dsn: str, *, clock: Callable[[], float] = time.monotonic, wall_clock: Callable[[], float] = time.time) -> None:
        self._clock, self._wall_clock = clock, wall_clock
        self._db = psycopg.connect(dsn, autocommit=True, row_factory=_records)
        try:
            verify(self._db, MIGRATIONS)
            verify_reader_permissions(self._db)
        except BaseException:
            self._db.close()
            raise
        self.access_lock = RLock()
        self._trace: Callable[[str], None] | None = None
        self.transaction_depth = 0
        self._read_only = False
        self.transaction_failed = False
        self.commit_callbacks: list[Callable[[], None]] = []
        self.rollback_callbacks: list[Callable[[], None]] = []
        self.before_commit: Callable[[], None] | None = None
        self.membership_feeds: set[str] = set()
        self.membership_folders: set[str] = set()

    def claim_store(self) -> None:
        # Data roots can differ while pointing to one PostgreSQL schema. Only
        # one Store may recover and dispatch its persisted work at a time.
        schema_key = schema_lock_key(self._db)
        shared = self._db.execute('SELECT pg_try_advisory_lock_shared(%s)', (schema_key,)).fetchone()
        if not shared or not shared[0]:
            raise ValueError('A schema migration is running')
        # Recheck after acquiring the lease: migration may have committed after
        # the initial connection verification and before this lock acquisition.
        verify(self._db, MIGRATIONS)
        locked = self._db.execute('SELECT pg_try_advisory_lock(hashtextextended(current_schema(),%s))',
                                  (0x4F4B4F53544F5245,)).fetchone()
        if not locked or not locked[0]:
            raise ValueError('Another Oko reader owns this database schema')

    @property
    def in_transaction(self) -> bool:
        status = self._db.info.transaction_status
        if status == TransactionStatus.UNKNOWN:
            # A broken connection is a database failure, not an unowned action.
            raise psycopg.OperationalError('the connection is closed or broken')
        return status != TransactionStatus.IDLE

    def incomplete_row_pages(self, sql: str, parameters: Sequence[Any] = ()) -> Any:
        """Stream a bounded batch of event inputs through a server cursor."""
        from uuid import uuid4
        if not self.in_transaction or self._read_only or _command(sql) != 'SELECT':
            raise RuntimeError('Incomplete cursor requires a write transaction and SELECT')
        if self._trace:self._trace(sql)
        with self._db.cursor(name='incomplete_'+uuid4().hex) as cursor:
            cursor.execute(sql,parameters or None)
            while True:
                if self._trace:self._trace('FETCH 100 FROM incomplete cursor')
                batch=cursor.fetchmany(100)
                if not batch:break
                yield batch
            if self._trace:self._trace('CLOSE incomplete cursor')

    def ensure_incomplete_workset(self) -> bool:
        """Create transaction-local scratch state; rollback and commit discard it."""
        if not self.in_transaction or self._read_only:
            raise RuntimeError('Incomplete workset requires a write transaction')
        probe="""SELECT to_regclass('pg_temp.oko_incomplete_work'),
            EXISTS(SELECT 1 FROM feeds WHERE kind='youtube' LIMIT 1) OR EXISTS(SELECT 1 FROM jobs WHERE youtube_admission_candidate LIMIT 1)"""
        if self._trace:self._trace(probe)
        result=self._db.execute(probe).fetchone()
        assert result is not None
        if result[0] is None:
            if self._trace:self._trace('CREATE TEMP TABLE oko_incomplete_work')
            self._db.execute('CREATE TEMP TABLE oko_incomplete_work (item text COLLATE "C" PRIMARY KEY, old_key text COLLATE "C", new_key text COLLATE "C", sticky boolean NOT NULL DEFAULT false, forgotten boolean NOT NULL DEFAULT false, unknown boolean NOT NULL DEFAULT false) ON COMMIT DROP')
            if self._trace:self._trace('CREATE INDEX oko_incomplete_work_old_key')
            self._db.execute('CREATE INDEX ON oko_incomplete_work(old_key)')
            if self._trace:self._trace('CREATE INDEX oko_incomplete_work_new_key')
            self._db.execute('CREATE INDEX ON oko_incomplete_work(new_key)')
            if self._trace:self._trace('CREATE TEMP TABLE oko_incomplete_materialized')
            self._db.execute('CREATE TEMP TABLE oko_incomplete_materialized (id text COLLATE "C" PRIMARY KEY) ON COMMIT DROP')
            if self._trace:self._trace('CREATE TEMP TABLE oko_incomplete_groups')
            self._db.execute('CREATE TEMP TABLE oko_incomplete_groups (key text COLLATE "C" PRIMARY KEY, is_old boolean NOT NULL DEFAULT false, weight bigint NOT NULL DEFAULT 0) ON COMMIT DROP')
        return bool(result[1])

    def ensure_youtube_admission_workset(self) -> None:
        if not self.in_transaction or self._read_only:raise RuntimeError('Admission workset requires a write transaction')
        probe="SELECT to_regclass('pg_temp.oko_youtube_admission_work')"
        if self._trace:self._trace(probe)
        result=self._db.execute(probe).fetchone()
        assert result is not None
        if result[0] is None:
            if self._trace:self._trace('CREATE TEMP TABLE oko_youtube_admission_work')
            self._db.execute('CREATE TEMP TABLE oko_youtube_admission_work (job_id bigint PRIMARY KEY, had_candidate boolean NOT NULL, old_objects jsonb NOT NULL) ON COMMIT DROP')
            if self._trace:self._trace('CREATE TEMP TABLE oko_youtube_admission_control')
            self._db.execute('CREATE TEMP TABLE oko_youtube_admission_control (refresh boolean PRIMARY KEY) ON COMMIT DROP')
            if self._trace:self._trace('CREATE TEMP TABLE oko_youtube_admission_delta')
            self._db.execute('CREATE TEMP TABLE oko_youtube_admission_delta (item text COLLATE "C" PRIMARY KEY) ON COMMIT DROP')
    def set_trace_callback(self, callback: Callable[[str], None] | None) -> None:
        """Observe native statement text without expanding bound values."""
        self._trace = callback

    def begin(self) -> None:
        if not self.in_transaction:
            if self._trace:
                self._trace('BEGIN')
            self._db.execute('BEGIN')
            self._db.execute("SELECT set_config('oko.clock',%s,false),set_config('oko.wall',%s,false)",
                             (str(self._clock()), str(self._wall_clock())))

    def begin_read(self) -> None:
        if self.in_transaction:
            raise RuntimeError('Unowned transaction at read entry')
        if self._trace:
            self._trace('BEGIN READ ONLY')
        self._db.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
        self._read_only = True

    def finish_read(self, *, success: bool) -> None:
        if self._trace:
            self._trace('COMMIT' if success else 'ROLLBACK')
        try:
            if success:
                self._db.commit()
            else:
                self._db.rollback()
        finally:
            self._read_only = False

    def execute(self, query: str, parameters: Sequence[Any] | Mapping[str, Any] = ()) -> psycopg.Cursor[Record]:
        # Match repository implicit DML transactions while keeping plain reads idle.
        compiled = query
        command = _command(query)
        if command in ('INSERT', 'UPDATE', 'DELETE'):
            self.begin()
        bound = {key: int(value) if isinstance(value, bool) else value for key, value in parameters.items()} if isinstance(parameters, Mapping) else tuple(int(value) if isinstance(value, bool) else value for value in parameters)
        if self._trace:
            self._trace(compiled)
        return self._db.execute(compiled, bound or None)

    def execute_write(self, query: str, parameters: Sequence[Any] | Mapping[str, Any] = ()) -> psycopg.Cursor[Record]:
        """Execute a native write, including a data-changing CTE, under its owner."""
        if not self.transaction_depth:
            raise RuntimeError('An owned transaction is required for an explicit write')
        _command(query, allow_write_cte=True)
        if self._trace:
            self._trace(query)
        return self._db.execute(query, parameters or None)

    def executemany(self, query: str, parameters: Sequence[Sequence[Any]]) -> None:
        compiled = query
        if _command(query) not in ('INSERT','UPDATE','DELETE'):
            raise ValueError('Batch execution requires a repository write')
        self.begin()
        if self._trace:
            self._trace(compiled)
        with self._db.cursor() as cursor:
            cursor.executemany(compiled,
                               (tuple(int(value) if isinstance(value, bool) else value for value in row) for row in parameters))

    def __enter__(self) -> 'PostgresConnection':
        self.access_lock.acquire()
        return self

    def after_commit(self, callback: Callable[[], None]) -> None:
        if self._read_only:
            raise RuntimeError('Callbacks are not allowed in a read-only snapshot')
        if self.transaction_depth:
            self.commit_callbacks.append(callback)
        else:
            callback()

    def flush(self) -> None:
        if self.in_transaction and self.before_commit is not None:
            self.before_commit()

    def after_rollback(self, callback: Callable[[], None]) -> None:
        if self._read_only:
            raise RuntimeError('Callbacks are not allowed in a read-only snapshot')
        if not self.in_transaction:
            raise RuntimeError('Rollback callback requires an active transaction')
        self.rollback_callbacks.append(callback)

    def commit(self) -> None:
        if self.transaction_depth:
            return
        try:
            self.flush()
            if self._trace and self.in_transaction:
                self._trace('COMMIT')
            self._db.commit()
        except BaseException:
            self.rollback()
            raise
        callbacks, self.commit_callbacks = self.commit_callbacks, []
        self.rollback_callbacks.clear()
        for callback in callbacks:
            callback()

    def rollback(self) -> None:
        if self.transaction_depth:
            self.transaction_failed = True
            return
        try:
            if self._trace and not self._db.closed and self.in_transaction:
                self._trace('ROLLBACK')
            self._db.rollback()
        except BaseException as error:
            # Discard an uncertain transaction before restoring cached values.
            try:
                self._db.close()
            except BaseException as close_error:
                raise error from close_error
            raise
        finally:
            self.commit_callbacks.clear()
            callbacks, self.rollback_callbacks = self.rollback_callbacks, []
            self.membership_feeds.clear()
            self.membership_folders.clear()
            for callback in reversed(callbacks):
                callback()

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> Literal[False]:
        try:
            self.commit() if exc_type is None else self.rollback()
        finally:
            self.access_lock.release()
        return False

    def close(self) -> None:
        try:
            self._db.close()
        finally:
            callbacks, self.rollback_callbacks = self.rollback_callbacks, []
            self.commit_callbacks.clear()
            self.before_commit = None
            self.membership_feeds.clear()
            self.membership_folders.clear()
            for callback in reversed(callbacks):
                callback()
