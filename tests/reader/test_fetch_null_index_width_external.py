import json,secrets
import psycopg
from oko.storage.postgres_migrations import migrate
from oko.storage.postgres_schema import MIGRATIONS

def test_migration_handles_valid_long_identity_and_occurrence_id(postgres_database_url):
    item=secrets.token_hex(750);rid=secrets.token_hex(750)
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:79])
        body=json.dumps(dict(code='work-fetch',operation='producer:status',objects=[dict(kind='material',id=item)]))
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES (%s,%s,'background',%s,1)",(rid,'job:work-fetch:f:fetch:'+item,body))
        migrate(db,MIGRATIONS)
        assert db.execute('SELECT 1 FROM reader_problems WHERE id=%s',(rid,)).fetchone()
