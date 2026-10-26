"""Real PostgreSQL read plans; never re-execute a mutation to measure its cost."""
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass
class ReadWork:
    rows: int = 0
    buffers: int = 0
    reads: int = 0
    writes: int = 0


def scan_rows(plan):
    own = (plan.get('Actual Rows', 0) + plan.get('Rows Removed by Filter', 0) + plan.get('Rows Removed by Index Recheck', 0)) * plan.get('Actual Loops', 1) if 'Relation Name' in plan else 0
    return own + sum(scan_rows(child) for child in plan.get('Plans', []))


@contextmanager
def measure_reads(db, *, enabled: Callable[[], bool] = lambda: True):
    """Instrument SELECTs on the synthetic connection; writes are only counted.

    The second execution is EXPLAIN ANALYZE of read-only repository queries.
    Timing assertions must use a separate run without this instrumentation.
    Trigger work on mutations is deliberately not included in the read metric.
    """
    original = db.execute
    work = ReadWork()
    def execute(query, parameters=()):
        cursor = original(query, parameters)
        if enabled():
            command = query.lstrip().split(None, 1)[0].upper()
            if command in ('SELECT', 'WITH'):
                from tests.support.reading_cost import request_sql
                token = request_sql.set(None)
                try:
                    plan = db._db.execute('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) '+query, parameters or None).fetchone()[0][0]['Plan']
                finally:
                    request_sql.reset(token)
                work.reads += 1
                work.rows += scan_rows(plan)
                work.buffers += plan.get('Shared Hit Blocks', 0) + plan.get('Shared Read Blocks', 0)
            else:
                work.writes += 1
        return cursor
    db.execute = execute
    try:
        yield work
    finally:
        db.execute = original
