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
