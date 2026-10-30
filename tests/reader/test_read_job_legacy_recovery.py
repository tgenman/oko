"""Independent synthetic reproductions for the frozen read-lifecycle patch."""
import json
import psycopg
import pytest
from oko.storage.postgres_migrations import migrate
from oko.storage.postgres_schema import MIGRATIONS
from tests.support.storage import Store


def feed(db, identity='feed'):
    db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES (%s,%s,%s,'default','rss','rss','connected','[]')", (identity,identity,identity))


def problem_body(code='source-connection',operation='producer:read',objects=None):
    return dict(code=code,operation=operation,section='object',title='x',consequence='x',next_step='x',objects=objects if objects is not None else [dict(kind='feed',id='feed')],action=None,state='current',next_at=None)


@pytest.mark.parametrize('job_id',[0,-1])
def test_recovery_handles_nonpositive_job_id(tmp_path,postgres_database_url,job_id):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:64]);feed(db)
        db.execute("INSERT INTO jobs(id,kind,item,status,error,problem_code) VALUES (%s,'read','feed','failed','synthetic','source-connection')",(job_id,))
        migrate(db,MIGRATIONS)
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url)
    try:
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='job:source-connection:feed:read' AND finished IS NULL").fetchone()
    finally:store.db.close()


def test_recovery_handles_empty_problem_id(tmp_path,postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:64]);feed(db)
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('','job:source-connection:feed:read','background',%s,1)",(json.dumps(problem_body()),))
        migrate(db,MIGRATIONS)
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url)
    try:
        assert store.db.execute("SELECT finished FROM reader_problems WHERE id=''").fetchone()[0] is not None
    finally:store.db.close()


def test_recovery_handles_null_read_item(tmp_path,postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:64])
        db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('read',NULL,'failed','synthetic','source-connection')")
        migrate(db,MIGRATIONS)
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url)
    try:
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='job:source-connection:demo:read' AND finished IS NULL").fetchone()
    finally:store.db.close()


def test_malformed_legacy_objects_does_not_prevent_open(tmp_path,postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:64])
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('bad','job:source-connection:feed:read','background',%s,1)",(json.dumps(problem_body(objects=[None])),))
        migrate(db,MIGRATIONS)
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url)
    try:
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE id='bad' AND finished IS NULL").fetchone()
    finally:store.db.close()


def test_legacy_fetch_with_read_in_item_is_not_closed(tmp_path,postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:64]);feed(db)
        db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('item:read:tail','rss','external',0)")
        db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('feed','item:read:tail',true,true)")
        db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','item:read:tail','failed','failure','fetch-error')")
        body=problem_body(code='fetch-error',operation='producer:status',objects=[dict(kind='material',id='item:read:tail')])
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:fetch-error:feed:fetch:item:read:tail','background',%s,1)",(json.dumps(body),))
        migrate(db,MIGRATIONS)
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url)
    try:
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE id='old' AND finished IS NULL").fetchone()
        store.problems.snapshot(3)
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE id='old' AND finished IS NULL").fetchone()
    finally:store.db.close()


def test_multi_object_legacy_group_keeps_other_feed(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            feed(store.db,'feed');feed(store.db,'other')
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,feed_id) VALUES ('read','feed','failed','failure','source-connection','other'),('read','other','failed','failure','source-connection','other')")
            old=dict(id='job:source-connection:other:read',created_at=1,finished_at=None,outcome=None,**problem_body(objects=[dict(kind='feed',id='feed'),dict(kind='feed',id='other')]))
            store.problems.sync_candidate(old['id'],old,1)
            original=store.db.execute('SELECT id FROM reader_problems WHERE identity=%s',(old['id'],)).fetchone()[0]
            store.problems.sync_read_feed('feed',2)
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='job:source-connection:other:read' AND id=%s AND finished IS NULL",(original,)).fetchone()
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='job:source-connection:feed:read' AND finished IS NULL").fetchone()
    finally:store.db.close()


def test_recovery_resumes_nonpositive_job_ids_after_batch_failure(tmp_path,postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:64])
        db.execute("INSERT INTO jobs(id,kind,item,status,error,problem_code) SELECT n,'read','feed-'||n,'failed','failure','source-connection' FROM generate_series(-1,101) n")
        migrate(db,MIGRATIONS)
    def fault(point):
        if point=='read_problem_recovery_batch':raise RuntimeError('interrupted')
    with pytest.raises(RuntimeError,match='interrupted'):
        Store(tmp_path,fault,database_url=postgres_database_url)
    with psycopg.connect(postgres_database_url) as db:
        assert db.execute("SELECT 1 FROM state WHERE key='read-problems-recovered-v1'").fetchone() is None
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url)
    try:
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'operation'='producer:read' AND finished IS NULL").fetchone()[0]==103
        for identity in ('job:source-connection:feed--1:read','job:source-connection:feed-0:read'):
            assert store.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
    finally:store.db.close()


def test_recovery_resumes_empty_problem_id_after_record_batch_failure(tmp_path,postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:64])
        body=problem_body(objects=[dict(kind='feed',id='missing-empty')])
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('','job:source-connection:missing-empty:read','background',%s,1)",(json.dumps(body),))
        db.execute("""INSERT INTO reader_problems(id,identity,origin,body,created)
            SELECT 'p-'||n,'job:source-connection:f-'||n||':read','background',
            jsonb_build_object('code','source-connection','operation','producer:read','objects',jsonb_build_array(jsonb_build_object('kind','feed','id','f-'||n)))::text,1
            FROM generate_series(1,101) n""")
        migrate(db,MIGRATIONS)
    def fault(point):
        if point=='read_problem_record_recovery_batch':raise RuntimeError('interrupted')
    with pytest.raises(RuntimeError,match='interrupted'):
        Store(tmp_path,fault,database_url=postgres_database_url)
    with psycopg.connect(postgres_database_url) as db:
        assert db.execute("SELECT 1 FROM state WHERE key='read-problems-recovered-v1'").fetchone() is None
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url)
    try:
        assert store.db.execute("SELECT finished FROM reader_problems WHERE id=''").fetchone()[0] is not None
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE finished IS NULL").fetchone()[0]==0
    finally:store.db.close()


@pytest.mark.parametrize('body',[None,[],{'objects':None,'code':'source-connection','operation':'producer:read'},{'objects':{},'code':'source-connection','operation':'producer:read'},{'objects':[None,{'kind':'feed','id':'feed'}],'code':'source-connection','operation':'producer:read'}])
def test_malformed_legacy_bodies_are_preserved_at_start(tmp_path,postgres_database_url,body):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:64])
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('bad','job:source-connection:feed:read','background',%s,1)",(json.dumps(body),))
        migrate(db,MIGRATIONS)
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url)
    try:
        row=store.db.execute("SELECT body,finished FROM reader_problems WHERE id='bad'").fetchone()
        assert json.loads(row['body'])==body and row['finished'] is None
    finally:store.db.close()


def test_malformed_legacy_body_not_overwritten_by_live_read_job(tmp_path,postgres_database_url):
    malformed=problem_body(objects=[None,dict(kind='feed',id='feed')])
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:64]);feed(db)
        db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('read','feed','failed','failure','source-connection')")
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('bad','job:source-connection:feed:read','background',%s,1)",(json.dumps(malformed),))
        migrate(db,MIGRATIONS)
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url)
    try:
        row=store.db.execute("SELECT body,finished FROM reader_problems WHERE id='bad'").fetchone()
        assert json.loads(row['body'])==malformed and row['finished'] is None
    finally:store.db.close()


def test_legacy_discover_with_read_in_code_is_not_closed(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            feed(store.db)
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('discover','feed','failed','failure','source-read:read:foo')")
            body=problem_body(code='source-read:read:foo',operation='producer:status',objects=[dict(kind='feed',id='feed')])
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:source-read:read:foo:feed:discover','background',%s,1)",(json.dumps(body),))
            store.problems.sync_read_feed('feed',2)
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE id='old' AND finished IS NULL").fetchone()
    finally:store.db.close()


def test_multi_object_legacy_splits_when_legacy_owner_is_third_feed(tmp_path,postgres_url):
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            for identity in ('a','b','c'):feed(store.db,identity)
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,feed_id) VALUES ('read','a','failed','failure','source-connection','c'),('read','b','failed','failure','source-connection','c')")
            old=dict(id='job:source-connection:c:read',created_at=1,finished_at=None,outcome=None,**problem_body(objects=[dict(kind='feed',id='a'),dict(kind='feed',id='b')]))
            store.problems.sync_candidate(old['id'],old,1)
            original=store.db.execute('SELECT id FROM reader_problems WHERE identity=%s',(old['id'],)).fetchone()[0]
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                store.problems.sync_read_feed('a',2)
                store.problems.sync_read_feed('b',2)
                raise ValueError('rollback')
        assert store.db.execute('SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL',(old['id'],)).fetchone()[0]==original
        with transaction(store.db):
            store.problems.sync_read_feed('a',2)
            store.problems.sync_read_feed('b',2)
        current=store.db.execute("SELECT identity,id FROM reader_problems WHERE identity LIKE 'job:source-connection:%%' AND finished IS NULL ORDER BY identity").fetchall()
        assert [row['identity'] for row in current]==['job:source-connection:a:read','job:source-connection:b:read']
        assert original in [row['id'] for row in current]
    finally:store.db.close()


def test_null_read_item_with_unshared_error_remains_visible(tmp_path,postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:64])
        db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('read',NULL,'failed','synthetic','read:failure')")
        migrate(db,MIGRATIONS)
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url)
    try:
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='job:read:failure:demo:read:' AND finished IS NULL").fetchone()
    finally:store.db.close()
