import pytest
from oko.storage.transaction import transaction
from tests.support.storage import Store


@pytest.mark.parametrize('kind',['discover','read','feed-delete'])
def test_viewed_publication_does_not_cancel_feed_job_with_same_id(tmp_path,postgres_url,kind):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,kind,transport,connection,original_urls) VALUES ('same','same','same','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,viewed) VALUES ('same',1)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,problem_code,error) VALUES (%s,'same','failed','processor-config','failure') RETURNING id",(kind,)).fetchone()[0]
            store.problems.finish_processor_change([job],None,1)
        original=store.problems.processor_current()['id']
        with pytest.raises(RuntimeError,match='rollback'):
            with transaction(store.db):
                store.jobs.reconcile_cleanup()
                assert store.db.execute('SELECT status FROM jobs WHERE id=%s',(job,)).fetchone()[0]=='failed'
                raise RuntimeError('rollback')
        with transaction(store.db):store.jobs.reconcile_cleanup()
        assert store.db.execute('SELECT status FROM jobs WHERE id=%s',(job,)).fetchone()[0]=='failed'
        assert store.problems.processor_current()['id']==original
    finally:store.db.close()


def test_cleanup_still_cancels_viewed_fetch_job(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,viewed) VALUES ('item',1)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,problem_code,error) VALUES ('fetch','item','failed','processor-config','failure') RETURNING id").fetchone()[0]
            store.problems.finish_processor_change([job],None,1)
        with transaction(store.db):store.jobs.reconcile_cleanup()
        assert store.db.execute('SELECT status FROM jobs WHERE id=%s',(job,)).fetchone()[0]=='cancelled'
    finally:store.db.close()


def test_media_id_equal_unrelated_viewed_publication_uses_media_owner(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('same','rss','same',1),('owner','rss','owner',0)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('same','owner','image','synthetic','file','pending')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,problem_code,error) VALUES ('download_media','same','failed','processor-config','failure') RETURNING id").fetchone()[0]
            store.problems.finish_processor_change([job],None,1)
        assert store.db.execute('SELECT processor_visible FROM jobs WHERE id=%s',(job,)).fetchone()[0]
        assert store.problems.processor_configuration_candidate(2)['objects']==[{'kind':'material','id':'owner'}]
        with transaction(store.db):store.jobs.reconcile_cleanup()
        assert store.db.execute('SELECT status FROM jobs WHERE id=%s',(job,)).fetchone()[0]=='failed'
        assert store.problems.processor_current() is not None
    finally:store.db.close()


def test_media_owner_viewed_is_cancelled_even_when_id_matches_unviewed_publication(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('same','rss','same',0),('owner','rss','owner',1)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('same','owner','image','synthetic','file','pending')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,problem_code,error) VALUES ('download_media','same','failed','processor-config','failure') RETURNING id").fetchone()[0]
            store.problems.finish_processor_change([job],None,1)
        assert not store.db.execute('SELECT processor_visible FROM jobs WHERE id=%s',(job,)).fetchone()[0]
        with transaction(store.db):store.jobs.reconcile_cleanup()
        assert store.db.execute('SELECT status FROM jobs WHERE id=%s',(job,)).fetchone()[0]=='cancelled'
    finally:store.db.close()


def test_migration_preserves_media_owner_on_colliding_ids(postgres_database_url):
    import psycopg
    from oko.storage.postgres_migrations import migrate
    from oko.storage.postgres_schema import MIGRATIONS
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:70])
        db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('same','rss','same',1),('owner','rss','owner',0)")
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('same','owner','image','synthetic','file','pending')")
        db.execute("INSERT INTO jobs(kind,item,status,problem_code,error) VALUES ('download_media','same','failed','processor-config','failure')")
        migrate(db,MIGRATIONS)
        assert db.execute("SELECT processor_visible FROM jobs WHERE kind='download_media' AND item='same'").fetchone()[0]


def test_viewed_media_cleanup_lookup_ignores_10000_unviewed_owners(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) SELECT 'p-'||n,'rss','p-'||n,0 FROM generate_series(1,10000) n")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) SELECT 'm-'||n,'p-'||n,'image','synthetic','file','pending' FROM generate_series(1,10000) n")
            store.db.execute("INSERT INTO jobs(kind,item,status) SELECT 'download_media','m-'||n,'pending' FROM generate_series(1,10000) n")
            store.db._db.execute('ANALYZE publications');store.db._db.execute('ANALYZE media_files');store.db._db.execute('ANALYZE jobs')
        query="SELECT j.id FROM jobs j WHERE j.status!='cancelled' AND j.kind='download_media' AND j.item IN (SELECT m.id FROM media_files m JOIN publications p ON p.id=m.publication_id WHERE p.viewed=1)"
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000,plan
    finally:store.db.close()


def test_viewed_media_owner_cleanup_closes_media_problem_without_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('owner','rss','owner',0)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('media','owner','image','synthetic','file','pending')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,problem_code,error) VALUES ('download_media','media','failed','media-failed','failure') RETURNING id").fetchone()[0]
            store.problems.sync_media_problem(job,[],1)
            saved=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'operation'='producer:download_media' AND finished IS NULL").fetchone()[0]
            store.db.execute("UPDATE publications SET viewed=1 WHERE id='owner'")
        with transaction(store.db):store.jobs.reconcile_cleanup()
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(saved,)).fetchone()[0] is not None
    finally:store.db.close()
