#!/usr/bin/env python3
"""Compare direct Store opening on one explicitly synthetic two-publication fixture.

Run baseline first with a NEW --source path, then current code with the same path.
Only a temporary PostgreSQL schema is created; no working installation is used.
"""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    checkout = args.checkout.resolve()
    sys.path.insert(0, str(checkout))
    os.chdir(checkout)
    dsn = os.environ.get('OKO_TEST_POSTGRES_URL')
    if not dsn:
        raise SystemExit('Set OKO_TEST_POSTGRES_URL to the isolated synthetic test database')

    import psycopg
    from tests.support.postgres import isolated_database
    # Use the shared fixture harness even when profiling an older checkout
    # that predates its extraction; product imports and CLI use --checkout.
    helper_path = Path(__file__).resolve().parents[3] / 'tests/support/sqlite_import.py'
    spec = importlib.util.spec_from_file_location('startup_cost_fixture', helper_path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    source_library, credential_file = helper.source_library, helper.credential_file
    from oko.storage.postgres_migrations import migrate
    from oko.storage.postgres_schema import MIGRATIONS
    from oko.storage.postgres_connection import PostgresConnection
    from oko.storage.store import Store

    source = args.source.resolve()
    marker = source / '.synthetic-startup-cost.json'
    if not source.exists():
        source_library(source, processing=True)
        marker.write_text(json.dumps({'synthetic_only': True, 'publications': 2})+'\n')
    elif not marker.is_file() or json.loads(marker.read_text()) != {'synthetic_only': True, 'publications': 2}:
        raise SystemExit('Refusing an existing source without the exact synthetic-fixture marker')
    source_hash = hashlib.sha256((source/'state.sqlite').read_bytes()).hexdigest()
    relevant = ['oko/storage/'+name+'.py' for name in (
        'store', 'postgres_connection', 'connection', 'subscriptions', 'settings_cache',
        'processing_coordination', 'publications', 'material_read', 'reading',
        'job_repository', 'reader_versions', 'read_sync', 'processing_input', 'filter_results',
    )]
    def hashes():
        return {name: hashlib.sha256((checkout/name).read_bytes()).hexdigest()
                for name in relevant if (checkout/name).is_file()}
    before = hashes()
    adapter, wire = Counter(), Counter()
    commit_calls = [0]
    batch_sizes = []

    class ObservedConnection(psycopg.Connection):
        def execute(self, query, *parameters, **options):
            text = query if isinstance(query, str) else query.as_string(self)
            wire[' '.join(text.split())] += 1
            return super().execute(query, *parameters, **options)

    original_connect = psycopg.connect
    original_execute = PostgresConnection.execute
    original_commit = PostgresConnection.commit
    original_many = PostgresConnection.executemany
    def traced(self, query, parameters=()):
        adapter[' '.join(query.split())] += 1
        return original_execute(self, query, parameters)
    def committed(self):
        commit_calls[0] += 1
        return original_commit(self)
    def batched(self, query, parameters):
        batch_sizes.append(len(parameters))
        return original_many(self, query, parameters)

    with tempfile.TemporaryDirectory(prefix='oko-startup-cost-') as temporary, isolated_database(dsn) as private_dsn:
        work = Path(temporary)
        with psycopg.connect(private_dsn, autocommit=True) as db:
            migrate(db, MIGRATIONS)
        result = helper.invoke(source, work/'target', credential_file(work, private_dsn), work/'receipt.json', checkout=checkout)
        if result.returncode:
            raise SystemExit('Synthetic import failed: '+result.stderr)
        store = None
        try:
            psycopg.connect = ObservedConnection.connect
            PostgresConnection.execute = traced
            PostgresConnection.commit = committed
            PostgresConnection.executemany = batched
            started = time.perf_counter()
            store = Store(work/'target', lambda _: None, database_url=private_dsn)
            elapsed = time.perf_counter()-started
        finally:
            if store is not None:
                store.db.close()
            psycopg.connect = original_connect
            PostgresConnection.execute = original_execute
            PostgresConnection.commit = original_commit
            PostgresConnection.executemany = original_many

    kinds = Counter()
    for query, count in adapter.items():
        kinds[query.split(None, 1)[0].upper()] += count
    output = {
        'checkout': str(checkout),
        'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'fixture': {'synthetic_only': True, 'publications': 2, 'feeds': 1,
                    'completed_filter_and_theses': True, 'source_database_sha256': source_hash},
        'measurement': 'direct Store constructor with default synchronous recovery; import/setup excluded',
        'open_seconds': round(elapsed, 6),
        'adapter_execute_calls': sum(adapter.values()),
        'adapter_sql_kinds': dict(sorted(kinds.items())),
        'settings_select_calls': sum(count for query, count in adapter.items()
                                     if query.startswith('SELECT') and re.search(r'\bFROM settings\b', query)),
        'scoped_settings_select_calls': adapter['SELECT field,value FROM settings WHERE scope=? AND owner=?'],
        'item_by_id_select_calls': adapter['SELECT * FROM items WHERE id=?'],
        'feed_by_id_select_calls': adapter['SELECT f.*,b.binding AS reader_binding FROM feeds f LEFT JOIN telegram_bindings b ON b.feed=f.id WHERE f.id=?'],
        'adapter_executemany_calls': len(batch_sizes),
        'adapter_executemany_rows': sum(batch_sizes),
        'commit_method_calls': commit_calls[0],
        'observed_connection_execute_calls': sum(wire.values()),
        'observed_clock_setup_sql_calls': sum(count for query, count in wire.items()
                                               if "set_config('oko.clock'" in query),
        'top_adapter_queries': [{'count': count, 'sql_template': query} for query, count in adapter.most_common(15)],
        'code_sha256': before,
        'changed_during_measurement': sorted(name for name in before if before[name] != hashes().get(name)),
        'source_unchanged': source_hash == hashlib.sha256((source/'state.sqlite').read_bytes()).hexdigest(),
        'limitations': ['single local sample; not production latency',
                       'driver execute calls, not TCP packet/roundtrip counts',
                       'cursor executemany statements are not included in connection.execute count'],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2)+'\n')
    print(json.dumps({key: output[key] for key in ('head', 'open_seconds', 'adapter_execute_calls',
        'observed_clock_setup_sql_calls', 'changed_during_measurement', 'source_unchanged')}))
    if output['changed_during_measurement'] or not output['source_unchanged']:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
