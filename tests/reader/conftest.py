"""Real database fixtures are opt-in and always use an isolated synthetic schema."""
import os
import psycopg
import pytest

from tests.support.postgres import isolated_database
from oko.storage.postgres_migrations import migrate
from oko.storage.postgres_schema import MIGRATIONS


@pytest.fixture
def postgres_database_url():
    dsn = os.environ.get('OKO_TEST_POSTGRES_URL')
    if not dsn:
        pytest.skip('Set OKO_TEST_POSTGRES_URL to an isolated PostgreSQL for integration checks')
    with isolated_database(dsn) as private_dsn:
        yield private_dsn


@pytest.fixture
def postgres_url(postgres_database_url):
    with psycopg.connect(postgres_database_url, autocommit=True) as installer:
        migrate(installer, MIGRATIONS)
    yield postgres_database_url
