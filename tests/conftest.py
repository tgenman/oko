"""PostgreSQL fixtures use only explicitly supplied disposable databases."""
import os
import psycopg
import pytest

from tests.support.postgres import isolated_database
from oko.storage.postgres_migrations import migrate
from oko.storage.postgres_schema import MIGRATIONS


@pytest.fixture(autouse=True)
def release_scenario_schemas():
    import gc
    from tests.support.storage import cleanup, owned_schemas
    previous = owned_schemas()
    yield
    gc.collect()
    cleanup(created_after=previous)


@pytest.fixture
def postgres_database_url():
    dsn = os.environ.get('OKO_TEST_POSTGRES_URL')
    if not dsn:
        pytest.fail('Set OKO_TEST_POSTGRES_URL to disposable PostgreSQL; storage tests cannot be skipped')
    with isolated_database(dsn) as private_dsn:
        yield private_dsn


@pytest.fixture
def postgres_url(postgres_database_url):
    with psycopg.connect(postgres_database_url, autocommit=True) as installer:
        migrate(installer, MIGRATIONS)
    yield postgres_database_url
