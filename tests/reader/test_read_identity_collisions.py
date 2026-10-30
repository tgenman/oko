from tests.support.storage import Store


def test_read_failure_not_silenced_by_existing_discover_identity(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),('a:discover:b','x','X','default','rss','rss','connected','[]'),('b:read:a:discover:b','y','Y','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,feed_id) VALUES ('discover','b:read:a:discover:b','failed','failure','custom','a')")
        store.problems.snapshot(1)
        old=store.db.execute("SELECT id,identity FROM reader_problems WHERE body::jsonb->>'operation'='producer:discover' AND finished IS NULL").fetchone()
        assert old['identity']=='job:custom:a:discover:b:read:a:discover:b'
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('read','a:discover:b','failed','read failure','custom')")
            store.problems.sync_read_feed('a:discover:b',2)
        problems=store.problems.snapshot(3)['current']
        assert any(p['operation']=='producer:read' for p in problems),problems
    finally:store.db.close()


def test_collision_fallback_survives_restart_and_clears(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),('a:discover:b','x','X','default','rss','rss','connected','[]'),('b:read:a:discover:b','y','Y','default','rss','rss','connected','[]')")
            discover=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,feed_id) VALUES ('discover','b:read:a:discover:b','failed','failure','custom','a') RETURNING id").fetchone()[0]
        store.problems.snapshot(1)
        with store.db:
            read=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('read','a:discover:b','failed','read failure','custom') RETURNING id").fetchone()[0]
            store.problems.sync_read_feed('a:discover:b',2)
        original=store.db.execute("SELECT id,identity FROM reader_problems WHERE body::jsonb->>'operation'='producer:read' AND finished IS NULL").fetchone()
        store.db.close()
        store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        assert store.db.execute("SELECT id,identity FROM reader_problems WHERE body::jsonb->>'operation'='producer:read' AND finished IS NULL").fetchone()==tuple(original)
        with store.db:
            store.db.execute("UPDATE jobs SET status='done',error=NULL,problem_code=NULL WHERE id=%s",(discover,))
            store.problems.sync_read_feed('a:discover:b',3)
        assert store.db.execute("SELECT id,identity FROM reader_problems WHERE body::jsonb->>'operation'='producer:read' AND finished IS NULL").fetchone()==tuple(original)
        with store.db:
            store.db.execute("UPDATE jobs SET status='done',error=NULL,problem_code=NULL WHERE id=%s",(read,))
            store.problems.sync_read_feed('a:discover:b',4)
        assert not store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'operation'='producer:read' AND finished IS NULL").fetchone()
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(original['id'],)).fetchone()[0]=='resolved'
    finally:store.db.close()


def test_recovery_merges_all_legacy_aliases_without_false_resolution(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            for feed in ('a','b','demo'):
                store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES (%s,%s,%s,'default','rss','rss','connected','[]')",(feed,feed,feed))
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,feed_id) VALUES ('read','','failed','failure','source-connection','a'),('read','demo','failed','failure','source-connection','b')")
            for feed in ('a','b'):
                old=dict(id='job:source-connection:'+feed+':read',code='source-connection',section='object',title='Ошибка',consequence='Доступно',next_step='Повторите',objects=[dict(kind='feed',id=feed)],action=None,operation='producer:read',created_at=1,state='current',finished_at=None,outcome=None,next_at=None)
                store.problems.sync_candidate(old['id'],old,1)
            store.db.execute("DELETE FROM state WHERE key='read-problems-recovered-v1'")
        store.db.close()
        store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        rows=store.db.execute("SELECT identity,finished,outcome FROM reader_problems WHERE body::jsonb->>'code'='source-connection' ORDER BY identity").fetchall()
        assert sum(row['finished'] is None for row in rows)==1
        assert all(row['outcome']!='resolved' for row in rows)
    finally:store.db.close()


def test_collision_retries_are_bounded_and_transaction_rolls_back(tmp_path,postgres_url,monkeypatch):
    import hashlib
    import pytest
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),('a:discover:b','x','X','default','rss','rss','connected','[]'),('b:read:a:discover:b','y','Y','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,feed_id) VALUES ('discover','b:read:a:discover:b','failed','failure','custom','a')")
        store.problems.snapshot(1)
        canonical='job:custom:a:discover:b:read:a:discover:b'
        digest=hashlib.sha256(canonical.encode()).hexdigest()
        with store.db:
            for token in ('one','two','three'):
                body=dict(id='job:read-isolated:'+digest+':'+token,code='custom',objects=[],operation='producer:discover')
                store.problems.sync_candidate(body['id'],body,1)
        tokens=iter(('one','two','three'))
        monkeypatch.setattr('oko.storage.problems.new_read_problem_token',lambda:next(tokens))
        with pytest.raises(RuntimeError,match='Unable to allocate'):
            with store.db:
                store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('read','a:discover:b','failed','read failure','custom')")
                store.problems.sync_read_feed('a:discover:b',2)
        assert not store.db.execute("SELECT 1 FROM jobs WHERE kind='read' AND item='a:discover:b'").fetchone()
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE identity LIKE 'job:read-isolated:%' AND finished IS NULL").fetchone()[0]==3
    finally:
        store.db.close()
