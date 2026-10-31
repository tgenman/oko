import json,psycopg,pytest
from oko.storage.postgres_migrations import Migration,migrate,verify
from oko.storage.postgres_schema import MIGRATIONS
from tests.support.storage import Store

def test_identity_migration_rolls_back_and_recovers_legacy(tmp_path,postgres_database_url):
    legacy=dict(code='identity-unconfirmed',section='object',title='old',consequence='saved',next_step='retry',objects=[dict(kind='material',id='p')],action=None,operation='producer:status',state='current',next_at=None)
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:82])
        db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','telegram','telegram','connected','[]')")
        db.execute("INSERT INTO publications(id) VALUES ('p')")
        db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
        db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','p','failed','failure','identity-unconfirmed')")
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:identity-unconfirmed:f:fetch:p','background',%s,1)",(json.dumps(legacy),))
        mig=MIGRATIONS[82];bad=Migration(83,mig.name,mig.sql+'; SELECT nonexistent FROM jobs')
        with pytest.raises(psycopg.errors.UndefinedColumn):migrate(db,(*MIGRATIONS[:82],bad))
        assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==82
        assert db.execute("SELECT body FROM reader_problems WHERE id='old'").fetchone()[0]==json.dumps(legacy)
        migrate(db,MIGRATIONS);verify(db,MIGRATIONS)
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url,wall_clock=lambda:100)
    try:
        row=store.db.execute("SELECT id,body FROM reader_problems WHERE identity='job:identity-unconfirmed:f:fetch:p' AND finished IS NULL").fetchone()
        assert row and row['id']=='old' and json.loads(row['body'])['operation']=='producer:fetch'
    finally:store.db.close()
