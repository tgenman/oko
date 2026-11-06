"""Retry settings and migration preserve source-requested waits."""
import psycopg
import pytest

from oko.storage.postgres_migrations import migrate
from oko.storage.postgres_schema import MIGRATIONS
from tests.support.app import create_app
from tests.support.reader import HEADERS
from tests.support.v3 import TestClient


def test_retry_delay_bounds_through_http(tmp_path):
    with TestClient(create_app(tmp_path, token='test', api_only=True), headers=HEADERS) as client:
        for field in ('retry_initial_seconds', 'retry_max_seconds'):
            for value in (1, 59, 3601, 86400):
                response = client.patch('/api/v3/settings/oko/global', json={field: value})
                assert response.status_code == 422, (field, value, response.text)
        response = client.patch('/api/v3/settings/oko/global', json={
            'retry_initial_seconds': 60, 'retry_max_seconds': 3600,
        })
        assert response.status_code == 200, response.text


def test_migration_bounds_saved_settings_without_shortening_waits(postgres_database_url):
    with psycopg.connect(postgres_database_url, autocommit=True) as db:
        migrate(db, MIGRATIONS[:123])
        db.execute("INSERT INTO settings(field,value) VALUES ('retry_initial_seconds','1'),('retry_max_seconds','86400')")
        db.execute("INSERT INTO jobs(kind,item,status,retry_at,retry_delay,wait_scope) VALUES ('fetch','p','pending',2000000000,86400,'example')")
        migrate(db, MIGRATIONS)
        values = dict(db.execute("SELECT field,value FROM settings WHERE field LIKE 'retry_%'").fetchall())
        assert values == {'retry_initial_seconds': 60, 'retry_max_seconds': 3600}
        wait = db.execute("SELECT retry_at,retry_delay,wait_scope FROM jobs WHERE kind='fetch' AND item='p'").fetchone()
        assert wait == (2000000000, 86400, 'example')


def test_migration_rejects_malformed_setting_without_changing_data(postgres_database_url):
    with psycopg.connect(postgres_database_url, autocommit=True) as db:
        migrate(db, MIGRATIONS[:123])
        db.execute("INSERT INTO settings(field,value) VALUES ('retry_initial_seconds','\"broken\"')")
        with pytest.raises(psycopg.errors.RaiseException, match='invalid stored retry delay'):
            migrate(db, MIGRATIONS)
        assert db.execute("SELECT value FROM settings WHERE field='retry_initial_seconds'").fetchone()[0] == 'broken'
        assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0] == 123
