from tests.support.storage import Store


def seed_legacy_discover_collision(store):
    # Legacy discover records could use feed_id as owner; preserve the exact
    # old identity while current event ownership uses the discover item.
    identity = 'job:custom:a:discover:b:read:a:discover:b'
    body = dict(id=identity, code='custom', section='object', title='Legacy discovery',
                consequence='Saved', next_step='Retry', objects=[dict(kind='feed', id='a')],
                action=None, operation='producer:discover', state='current', next_at=None)
    with store.db:
        store.problems.sync_candidate(identity, body, 1)



def test_read_failure_not_silenced_by_existing_discover_identity(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),('a:discover:b','x','X','default','rss','rss','connected','[]'),('b:read:a:discover:b','y','Y','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,feed_id) VALUES ('discover','b:read:a:discover:b','failed','failure','custom','a')")
        seed_legacy_discover_collision(store)
        old=store.db.execute("SELECT id,identity FROM reader_problems WHERE body::jsonb->>'operation'='producer:discover' AND finished IS NULL").fetchone()
        assert old['identity']=='job:custom:a:discover:b:read:a:discover:b'
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('read','a:discover:b','failed','read failure','custom')")
            store.problems.sync_read_feed('a:discover:b',2)
        problems=store.problems.snapshot(3)['current']
        assert any(p['operation']=='producer:read' for p in problems),problems
    finally:store.db.close()


def test_read_collision_identity_stays_stable_after_foreign_problem_closes(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),('a:discover:b','x','X','default','rss','rss','connected','[]'),('b:read:a:discover:b','y','Y','default','rss','rss','connected','[]')")
            discover=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,feed_id) VALUES ('discover','b:read:a:discover:b','failed','failure','custom','a') RETURNING id").fetchone()[0]
        seed_legacy_discover_collision(store)
        with store.db:
            read=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('read','a:discover:b','failed','read failure','custom') RETURNING id").fetchone()[0]
            store.problems.sync_read_feed('a:discover:b',2)
        first=next(p for p in store.problems.snapshot(3)['current'] if p['operation']=='producer:read')
        with store.db:
            store.db.execute("UPDATE jobs SET status='done',error=NULL,problem_code=NULL WHERE id=%s",(discover,))
            store.problems.sync_read_feed('a:discover:b',4)
        second=next(p for p in store.problems.snapshot(5)['current'] if p['operation']=='producer:read')
        assert second['id']==first['id']
        with store.db:
            store.db.execute("UPDATE jobs SET status='done',error=NULL,problem_code=NULL WHERE id=%s",(read,))
            store.problems.sync_read_feed('a:discover:b',6)
        assert not [p for p in store.problems.snapshot(7)['current'] if p['operation']=='producer:read']
    finally:store.db.close()


def test_distinct_read_jobs_with_ambiguous_colon_keys_remain_visible(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a:b','one','One','default','rss','rss','connected','[]'),('b','two','Two','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('read','a:b','failed','one','source-x'),('read','b','failed','two','source-x:a')")
            store.problems.sync_read_feed('a:b',1)
            store.problems.sync_read_feed('b',1)
        problems=[p for p in store.problems.snapshot(2)['current'] if p['operation']=='producer:read']
        assert len(problems)==2
        assert {p['code'] for p in problems}=={'source-x','source-x:a'}
        assert {p['objects'][0]['id'] for p in problems}=={'a:b','b'}
        ids={p['id'] for p in problems}
        with store.db:
            store.problems.sync_read_feed('a:b',3)
            store.problems.sync_read_feed('b',3)
        assert {p['id'] for p in store.problems.snapshot(4)['current'] if p['operation']=='producer:read'}==ids
    finally:store.db.close()


def test_read_fallback_avoids_preexisting_foreign_reserved_identity(tmp_path,postgres_url,monkeypatch):
    import hashlib
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        feed='a:discover:b';code='custom'
        canonical='job:'+code+':'+feed+':read:'+feed
        fallback='job:read-isolated:'+hashlib.sha256(canonical.encode()).hexdigest()+':0'
        tokens=iter(('0','1'))
        monkeypatch.setattr('oko.storage.problems.new_read_problem_token',lambda:next(tokens))
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),(%s,'x','X','default','rss','rss','connected','[]'),('b:read:a:discover:b','y','Y','default','rss','rss','connected','[]')",(feed,))
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,feed_id) VALUES ('discover','b:read:a:discover:b','failed','failure','custom','a')")
        seed_legacy_discover_collision(store)
        with store.db:
            body=dict(code='other',section='object',title='Other',consequence='Saved',next_step='Retry',objects=[dict(kind='feed',id=feed)],action=None,operation='producer:discover',state='current',next_at=None)
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('reserved',%s,'background',%s,1)",(fallback,json.dumps(body)))
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('read',%s,'failed','failure',%s)",(feed,code))
            store.problems.sync_read_feed(feed,2)
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE id='reserved'").fetchone()
        current=store.db.execute("SELECT identity FROM reader_problems WHERE body::jsonb->>'operation'='producer:read' AND finished IS NULL").fetchone()
        assert current and current['identity'].endswith(':1')
    finally:store.db.close()
