"""The real CLI consumes a private DSN file; migration is a separate operation."""
from tests.support.cards import read_all_cards
import os
from pathlib import Path
import subprocess
import sys

from tests.support.processes import launch, connect, cleanup, wait_cards


def test_private_database_file_runs_cli_without_sqlite(postgres_url, tmp_path):
    root = Path(__file__).resolve().parents[2]
    credential = tmp_path / 'database-url'
    credential.write_text(postgres_url)
    credential.chmod(0o600)
    check = subprocess.run([sys.executable, '-m', 'oko.storage.postgres_migrations', '--check',
                            '--database-url-file', str(credential)], cwd=root, capture_output=True, text=True, timeout=20)
    assert check.returncode == 0, check.stdout + check.stderr
    assert 'version 26' in check.stdout
    assert postgres_url not in check.stdout + check.stderr
    data = tmp_path / 'reader'
    process = launch(data, api_only=True, database_url_file=credential)
    try:
        assert postgres_url not in process.args
        with connect(data, process) as client:
            assert client.patch('/api/v3/settings/oko/global', json={'theses': False, 'prompt_filters': ''}).status_code == 200
            assert client.put('/api/v3/power', json={'enabled': True}).status_code == 200
            assert len(wait_cards(client, 3)) == 3
            assert not (data / 'state.sqlite').exists()
            assert client.put('/api/v3/power', json={'enabled': False}).status_code == 200
    finally:
        cleanup(process)
    process = launch(data, api_only=True, database_url_file=credential)
    try:
        with connect(data, process) as client:
            assert len(read_all_cards(client, '/api/v3/cards')) == 3
            assert not (data / 'state.sqlite').exists()
    finally:
        cleanup(process)


def test_cli_rejects_readable_database_credentials_before_launch(postgres_url, tmp_path):
    root = Path(__file__).resolve().parents[2]
    credential = tmp_path / 'database-url'
    credential.write_text(postgres_url)
    credential.chmod(0o644)
    result = subprocess.run([sys.executable, '-m', 'oko', '--database-url-file', str(credential),
                             '--data', str(tmp_path / 'reader'), '--api-only', '--no-browser'],
                            cwd=root, capture_output=True, text=True, timeout=20)
    assert result.returncode != 0
    assert 'private regular file (0600)' in result.stderr
    assert postgres_url not in result.stdout + result.stderr
    assert not (tmp_path / 'reader' / 'runtime.json').exists()


def test_postgres_startup_waits_for_delayed_database(postgres_url, monkeypatch):
    import psycopg
    from oko.storage import readiness
    original = readiness.psycopg.connect
    attempts = []
    def connect(*args, **options):
        attempts.append(True)
        if len(attempts) < 3: raise psycopg.OperationalError('synthetic delayed database')
        return original(*args, **options)
    monkeypatch.setattr(readiness.psycopg, 'connect', connect)
    monkeypatch.setattr(readiness.time, 'sleep', lambda _:None)
    readiness.wait_database(postgres_url)
    assert len(attempts) == 3


def test_failed_database_startup_exits_without_disclosing_dsn(monkeypatch):
    import psycopg
    import pytest
    from oko.storage import readiness
    ticks=iter([0,31])
    monkeypatch.setattr(readiness.time,'monotonic',lambda:next(ticks))
    def unavailable(*args,**options): raise psycopg.OperationalError('synthetic secret DSN')
    monkeypatch.setattr(readiness.psycopg,'connect',unavailable)
    with pytest.raises(SystemExit,match='PostgreSQL is unavailable') as error:
        readiness.wait_database('synthetic secret DSN')
    assert 'secret' not in str(error.value)
