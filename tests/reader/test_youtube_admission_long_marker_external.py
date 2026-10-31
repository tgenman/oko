import json,secrets
import psycopg
from oko.storage.postgres_migrations import migrate
from oko.storage.postgres_schema import MIGRATIONS

def test_admission_migration_keeps_long_foreign_marker(postgres_database_url):
    value=secrets.token_hex(2000)
    body=json.dumps(dict(code='youtube-admission',operation='producer:youtube-admission',youtube_admission_owner=value,objects=[]))
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:80])
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('legacy','foreign','background',%s,1)",(body,))
        migrate(db,MIGRATIONS)
        assert db.execute("SELECT body FROM reader_problems WHERE id='legacy'").fetchone()[0]==body
