import hashlib
import json
import psycopg
import pytest
from oko.storage.postgres_migrations import Migration,migrate,verify
from oko.storage.postgres_schema import MIGRATIONS
from tests.support.storage import Store
from oko.storage.youtube_admission import current


def test_080_to_081_rolls_back_and_preserves_legacy_with_long_ids(tmp_path,postgres_database_url):
    long_id=''.join(hashlib.sha256(str(n).encode()).hexdigest() for n in range(24))
    malformed=json.dumps({'code':'youtube-admission','operation':'producer:status','objects':[None]})
    long_feed=''.join(hashlib.sha256(('feed-'+str(n)).encode()).hexdigest() for n in range(64))
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:80])
        db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected','[]')")
        db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES (%s,'youtube','long',0,1,'{}',1)",(long_id,))
        db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt',%s,true)",(long_id,))
        db.execute("INSERT INTO jobs(kind,item,status,problem_code) VALUES ('fetch',%s,'pending','')",(long_id,))
        db.execute("INSERT INTO jobs(kind,item,feed_id,status,problem_code) VALUES ('fetch','orphan',%s,'pending','youtube-admission')",(long_feed,))
        db.execute('UPDATE youtube_access SET blocked_until=200 WHERE id=1')
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('bad','youtube:admission','background',%s,1)",(malformed,))
        mig=MIGRATIONS[80]
        bad=Migration(81,mig.name,mig.sql+'; SELECT nonexistent FROM jobs')
        with pytest.raises(psycopg.errors.UndefinedColumn):migrate(db,(*MIGRATIONS[:80],bad))
        assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==80
        assert db.execute("SELECT body FROM reader_problems WHERE id='bad'").fetchone()[0]==malformed
        migrate(db,MIGRATIONS);verify(db,MIGRATIONS)
        assert db.execute("SELECT feed_id FROM jobs WHERE item='orphan'").fetchone()[0]==long_feed
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url,wall_clock=lambda:100)
    try:
        assert store.db.execute("SELECT body FROM reader_problems WHERE id='bad'").fetchone()[0]==malformed
        row=current(store)
        assert row['id']!='bad' and json.loads(row['body'])['objects']==[dict(kind='material',id=long_id)]
        snap=store.problems.snapshot(100)
        assert {p['id'] for p in snap['current']} >= {'bad',row['id']}
    finally:store.db.close()


@pytest.mark.parametrize('bad_deadline',[float('inf'),1e300])
def test_unrepresentable_legacy_deadline_does_not_break_startup(tmp_path,postgres_database_url,bad_deadline):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:80])
        db.execute('UPDATE youtube_access SET blocked_until=%s WHERE id=1',(bad_deadline,))
        migrate(db,MIGRATIONS)
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url,wall_clock=lambda:100)
    try:
        assert store.db.execute('SELECT blocked_until FROM youtube_access WHERE id=1').fetchone()[0]==bad_deadline
    finally:store.db.close()


def test_null_next_at_preserves_startup_and_reports_unknown_time(tmp_path,postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:80])
        db.execute('UPDATE youtube_access SET blocked_until=200,next_at=NULL WHERE id=1')
        migrate(db,MIGRATIONS)
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url,wall_clock=lambda:100)
    try:
        row=current(store);body=json.loads(row['body'])
        assert row['youtube_admission_due']==200 and body['next_at'] is None
        assert 'Время следующей попытки недоступно' in body['consequence']
        assert store.db.execute('SELECT next_at FROM youtube_access WHERE id=1').fetchone()[0] is None
    finally:store.db.close()


def test_legacy_state_key_cannot_spoof_completed_admission_recovery(tmp_path,postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:80])
        db.execute("INSERT INTO state(key,value) VALUES ('youtube-admission-jobs-recovered-v1','foreign')")
        db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected','[]')")
        db.execute("INSERT INTO publications(id) VALUES ('p')")
        db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','p',true)")
        db.execute("INSERT INTO jobs(kind,item,status,problem_code) VALUES ('fetch','p','pending','')")
        db.execute('UPDATE youtube_access SET blocked_until=200 WHERE id=1')
        migrate(db,MIGRATIONS)
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url,wall_clock=lambda:100)
    try:
        assert store.db.execute("SELECT value FROM state WHERE key='youtube-admission-jobs-recovered-v1'").fetchone()[0]=='foreign'
        assert store.db.execute('SELECT youtube_admission_recovered FROM youtube_access WHERE id=1').fetchone()[0]
        assert json.loads(current(store)['body'])['objects']==[dict(kind='material',id='p')]
    finally:store.db.close()
