from tests.support.cards import read_all_cards
from tests.support.presence import presence
from tests.support.sources import LateSource
from tests.support.reader import Clock, wait_for
import time
from tests.support.storage import database
from pathlib import Path
from fastapi.testclient import TestClient
from tests.support.app import create_app
from tests.support.synthetic_source import SyntheticSource

HEADERS = {'Authorization': 'Bearer test-token'}

def retained(root, identity):
    with database(root) as db:
        return db.execute('SELECT 1 FROM publications WHERE id = %s',(identity,)).fetchone() is not None


def test_corrupt_database_is_reported_and_never_replaced(tmp_path: Path, postgres_url):
    import psycopg
    with psycopg.connect(postgres_url, autocommit=True) as db:
        db.execute("UPDATE oko_schema_migrations SET checksum='preserve-corruption-evidence' WHERE version=1")
    with TestClient(create_app(tmp_path, token='test-token', database_url=postgres_url), headers=HEADERS) as client:
        assert client.get('/api/v3/cards').status_code == 503
        assert client.get('/api/v3/status').json()['runtime_state']=='unavailable'
    with psycopg.connect(postgres_url, autocommit=True) as db:
        assert db.execute('SELECT checksum FROM oko_schema_migrations WHERE version=1').fetchone()[0] == 'preserve-corruption-evidence'
