import json
from oko.collection.adapters import AdapterIssue,AdapterErrorKind
from oko.storage.job_types import Job
from oko.storage.transaction import transaction
from tests.support.storage import Store


def test_media_failure_and_finish_without_status_read(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('file','article','image:1','https://example.test/file','file','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','file','running',1) RETURNING id").fetchone()[0]
            store.jobs.fail_media(Job(job,'download_media','file',1),AdapterIssue('media-too-large','failure',AdapterErrorKind.permanent))
            row=store.db.execute("SELECT id,body FROM reader_problems WHERE identity='job:media-too-large:feed:download_media:file' AND finished IS NULL").fetchone()
            assert row and json.loads(row['body'])['objects']==[dict(kind='material',id='article')]
            store.db.execute("UPDATE jobs SET status='running',generation=2 WHERE id=%s",(job,))
            store.jobs.finish(Job(job,'download_media','file',2))
            assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(row['id'],)).fetchone()[0]=='resolved'
    finally:store.db.close()


def test_two_files_on_one_publication_keep_separate_errors(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('one','article','image:1','https://example.test/1','one','pending_download'),('two','article','image:2','https://example.test/2','two','pending_download')")
            jobs=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','one','running',1),('download_media','two','running',1) RETURNING id,item").fetchall()
            for row in jobs:store.jobs.fail_media(Job(row['id'],'download_media',row['item'],1),AdapterIssue('media-too-large','failure',AdapterErrorKind.permanent))
            rows=store.db.execute("SELECT identity FROM reader_problems WHERE body::jsonb->>'operation'='producer:download_media' AND finished IS NULL ORDER BY identity").fetchall()
            assert [row[0] for row in rows]==['job:media-too-large:feed:download_media:one','job:media-too-large:feed:download_media:two']
            first=jobs[0];store.db.execute("UPDATE jobs SET status='running',generation=2 WHERE id=%s",(first['id'],));store.jobs.finish(Job(first['id'],'download_media',first['item'],2))
            assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'operation'='producer:download_media' AND finished IS NULL").fetchone()[0]==1
    finally:store.db.close()


def test_youtube_subtitle_keeps_existing_pause_dependent_status(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','youtube','youtube','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('video')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','video',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('caption','video','youtube:subtitles','https://example.test/sub','sub','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','caption','running',1) RETURNING id").fetchone()[0]
            store.jobs.fail_media(Job(job,'download_media','caption',1),AdapterIssue('media-unavailable','failure',AdapterErrorKind.temporary))
            assert not store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()
        visible=store.problems.candidates(2)
        assert any(p['code']=='media-unavailable' for p in visible)
        store.youtube.blocked(2,900)
        assert not any(p['code']=='media-unavailable' for p in store.problems.candidates(3))
    finally:store.db.close()


def test_removed_file_makes_its_problem_irrelevant(tmp_path,postgres_url):
    from oko.domain.collection import CollectionEffects
    from oko.storage.publication_links import capture_media_problems
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('file','article','image:1','https://example.test/file','file','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','file','failed','failure','media-too-large') RETURNING id").fetchone()[0]
            store.problems.sync_media_problem(job,[],1)
            effects=CollectionEffects();capture_media_problems(store,effects,{'article'})
            store.db.execute("DELETE FROM media_files WHERE id='file'")
            store.jobs.reconcile_cleanup()
            store.jobs.sync_captured_deletions(effects)
            assert store.db.execute("SELECT outcome FROM reader_problems WHERE identity='job:media-too-large:feed:download_media:file'").fetchone()[0]=='irrelevant'
    finally:store.db.close()


def test_removing_primary_feed_reassigns_media_occurrence(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),('b','b','B','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('article','rss','x')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','article',true),('b','article',false)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('file','article','image:1','https://example.test/file','file','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','file','failed','failure','media-too-large') RETURNING id").fetchone()[0]
            store.problems.sync_media_problem(job,[],1)
            before=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:media-too-large:a:download_media:file' AND finished IS NULL").fetchone()[0]
        store.remove_feed('a')
        after=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:media-too-large:b:download_media:file' AND finished IS NULL").fetchone()
        assert after and after[0]==before
    finally:store.db.close()


def test_status_does_not_rewrite_managed_media_occurrence(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('file','article','image:1','https://example.test/file','file','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','file','failed','failure','media-too-large') RETURNING id").fetchone()[0]
            store.problems.sync_media_problem(job,[],1)
            before=store.db.execute("SELECT id,xmin::text,body FROM reader_problems WHERE identity='job:media-too-large:feed:download_media:file' AND finished IS NULL").fetchone()
        assert not [c for c in store.problems.candidates(2) if c.get('code')=='media-too-large']
        store.problems.snapshot(2)
        after=store.db.execute('SELECT id,xmin::text,body FROM reader_problems WHERE id=%s',(before['id'],)).fetchone()
        assert tuple(after)==tuple(before)
    finally:store.db.close()


def test_manual_retry_clears_error_without_status_read(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('file','article','image:1','https://example.test/file','file','unavailable')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','file','failed','failure','media-too-large') RETURNING id").fetchone()[0]
            store.problems.sync_media_problem(job,[],1)
            store.jobs.retry_media('article')
        assert store.db.execute("SELECT outcome FROM reader_problems WHERE identity='job:media-too-large:feed:download_media:file'").fetchone()[0]=='resolved'
    finally:store.db.close()


def test_status_media_probe_cost_on_many_managed_failures(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) SELECT 'm-'||n,'article','key-'||n,'locator','name','unavailable' FROM generate_series(1,10000) n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'download_media','m-'||n,'failed','failure','media-too-large' FROM generate_series(1,10000) n")
            store.db._db.execute('ANALYZE jobs');store.db._db.execute('ANALYZE media_files')
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.problems.candidates(1)
        finally:store.db.set_trace_callback(None)
        query=next(q for q in sql if 'SELECT jobs . *' in q)
        plan=store.db._db.execute('EXPLAIN (ANALYZE,FORMAT JSON) '+query).fetchone()[0][0]
        print('status main SQL rows:',scan_rows(plan['Plan']))
        assert scan_rows(plan['Plan'])<1000,plan
    finally:store.db.close()


def test_orphan_media_job_retains_previous_identity_and_material_reference(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','orphan','running',1) RETURNING id").fetchone()[0]
            store.jobs.fail_media(Job(job,'download_media','orphan',1),AdapterIssue('media-too-large','failure',AdapterErrorKind.permanent))
            row=store.db.execute("SELECT id,body FROM reader_problems WHERE identity='job:media-too-large:orphan:download_media:orphan' AND finished IS NULL").fetchone()
            assert row and json.loads(row['body'])['objects']==[dict(kind='material',id='orphan')]
            store.db.execute("UPDATE jobs SET status='running',generation=2 WHERE id=%s",(job,))
            store.jobs.finish(Job(job,'download_media','orphan',2))
            assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(row['id'],)).fetchone()[0]=='resolved'
    finally:store.db.close()


def test_restart_recovers_orphan_media_with_original_wire_identity(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='media-problems-recovered-v2'")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','orphan','failed','failure','media-too-large')")
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        row=store.db.execute("SELECT body FROM reader_problems WHERE identity='job:media-too-large:orphan:download_media:orphan' AND finished IS NULL").fetchone()
        assert row and json.loads(row[0])['objects']==[dict(kind='material',id='orphan')]
    finally:store.db.close()


def test_linked_media_failure_recovers_all_root_owners(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='media-problems-recovered-v2'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('x','x','X','default','rss','rss','connected','[]'),('y','y','Y','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('root-x'),('root-y'),('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('x','root-x',true),('y','root-y',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root-x','article'),('root-y','article')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('file','article','image:1','https://example.test/file','file','unavailable')")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','file','failed','failure','media-too-large')")
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        rows=store.db.execute("SELECT identity,body FROM reader_problems WHERE body::jsonb->>'operation'='producer:download_media' AND finished IS NULL ORDER BY identity").fetchall()
        assert [r['identity'] for r in rows]==['job:media-too-large:x:download_media:file','job:media-too-large:y:download_media:file']
        assert {json.loads(r['body'])['objects'][0]['id'] for r in rows}=={'root-x','root-y'}
    finally:store.db.close()


def test_orphan_media_status_matches_previous_problem_projection(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','orphan','failed','failure','media-too-large') RETURNING id").fetchone()[0]
            store.problems.sync_media_problem(job,[],1)
        state=store.problems.snapshot(2)
        row=next(p for p in state['current'] if p['code']=='media-too-large')
        assert row['objects']==[dict(kind='material',id='orphan')]
    finally:store.db.close()


def test_orphan_legacy_history_is_preserved_and_reused(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    body=dict(code='media-too-large',section='object',title='legacy',consequence='saved',next_step='retry',objects=[dict(kind='material',id='orphan')],action=None,operation='producer:status',state='current',next_at=None)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='media-problems-recovered-v2'")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','orphan','failed','failure','media-too-large')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:media-too-large:orphan:download_media:orphan','background',%s,1)",(json.dumps(body),))
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        row=store.db.execute("SELECT id,body FROM reader_problems WHERE identity='job:media-too-large:orphan:download_media:orphan' AND finished IS NULL").fetchone()
        assert row and row['id']=='old' and json.loads(row['body'])['objects']==body['objects']
    finally:store.db.close()


def test_orphan_ambiguous_legacy_without_file_survives_recovery_and_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    body=dict(code='media-too-large',section='object',title='old',consequence='c',next_step='n',state='current',operation='producer:status',objects=[dict(kind='material',id='unrelated')])
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='media-problems-recovered-v2'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('unrelated')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:media-too-large:feed:download_media:ghost','background',%s,1)",(json.dumps(body),))
        store.problems.recover_media_problems()
        store.problems.snapshot(2)
        row=store.db.execute("SELECT finished,body FROM reader_problems WHERE id='old'").fetchone()
        assert row['finished'] is None and json.loads(row['body'])==body
    finally:store.db.close()


def test_legacy_media_with_unrelated_object_remains_unchanged(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    body=dict(code='media-too-large',section='object',title='old',consequence='c',next_step='n',state='current',operation='producer:status',objects=[dict(kind='material',id='article'),dict(kind='material',id='unrelated')])
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='media-problems-recovered-v2'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('article'),('unrelated')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('file','article','image:1','https://example.test/file','file','unavailable')")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','file','failed','failure','media-too-large')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:media-too-large:feed:download_media:file','background',%s,1)",(json.dumps(body),))
        store.problems.recover_media_problems()
        store.problems.snapshot(2)
        old=store.db.execute("SELECT finished,body FROM reader_problems WHERE id='old'").fetchone()
        assert old['finished'] is None and json.loads(old['body'])==body
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'media_job_id' IS NOT NULL AND finished IS NULL").fetchone()
    finally:store.db.close()


def test_orphan_gains_file_owner_without_losing_occurrence(tmp_path,postgres_url):
    from oko.storage.media_job_problems import managed_candidates
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','file','failed','failure','media-too-large') RETURNING id").fetchone()[0]
            store.problems.sync_media_problem(job,[],1)
            old=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:media-too-large:file:download_media:file' AND finished IS NULL").fetchone()[0]
            previous=managed_candidates(store,job,2)
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('file','article','image:1','https://example.test/file','file','unavailable')")
            store.problems.sync_media_problem(job,previous,2)
            row=store.db.execute("SELECT id,body FROM reader_problems WHERE identity='job:media-too-large:feed:download_media:file' AND finished IS NULL").fetchone()
            assert row and row['id']==old and json.loads(row['body'])['objects']==[dict(kind='material',id='article')]
    finally:store.db.close()


def test_recovery_thousand_orphan_media_jobs_is_batched(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='media-problems-recovered-v2'")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'download_media','orphan-'||n,'failed','failure','media-too-large' FROM generate_series(1,1000) n")
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.problems.recover_media_problems()
        finally:store.db.set_trace_callback(None)
        print(f'orphan recovery statements {len(sql)}')
        assert len(sql)<500,len(sql)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'operation'='producer:download_media' AND finished IS NULL").fetchone()[0]==1000
    finally:store.db.close()


def test_recovery_mixed_orphan_media_preserves_old_ids_in_batches(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='media-problems-recovered-v2'")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'download_media','orphan-'||n,'failed','failure','media-too-large' FROM generate_series(1,1000) n")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'old-'||n,'job:media-too-large:orphan-'||n||':download_media:orphan-'||n,'background',jsonb_build_object('code','media-too-large','section','object','title','x','consequence','c','next_step','n','operation','producer:download_media','state','current','objects',jsonb_build_array(jsonb_build_object('kind','material','id','orphan-'||n)))::text,1 FROM generate_series(1,1000,100) n")
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.problems.recover_media_problems()
        finally:store.db.set_trace_callback(None)
        print(f'mixed orphan recovery statements {len(sql)}')
        assert len(sql)<500,len(sql)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'operation'='producer:download_media' AND finished IS NULL").fetchone()[0]==1000
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE id LIKE 'old-%%' AND finished IS NULL").fetchone()[0]==10
    finally:store.db.close()


def test_unverified_legacy_media_without_job_is_preserved(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    body=dict(code='media-too-large',section='object',title='old',consequence='c',next_step='n',state='current',operation='producer:status',objects=[dict(kind='material',id='unrelated')])
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='media-problems-recovered-v2'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('unrelated')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:media-too-large:feed:download_media:ghost','background',%s,1)",(json.dumps(body),))
        store.problems.recover_media_problems();store.problems.snapshot(2)
        row=store.db.execute("SELECT finished,body FROM reader_problems WHERE id='old'").fetchone()
        assert row['finished'] is None and json.loads(row['body'])==body
    finally:store.db.close()


def test_schedule_cancels_unavailable_media_error_without_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('file','article','image:1','https://example.test/file','file','unavailable')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','file','pending','failure','media-too-large') RETURNING id").fetchone()[0]
            store.problems.sync_media_problem(job,[],1)
            store.jobs.schedule_media('article')
        assert store.db.execute("SELECT outcome FROM reader_problems WHERE identity='job:media-too-large:feed:download_media:file'").fetchone()[0]=='resolved'
    finally:store.db.close()


def test_unidentifiable_legacy_media_is_preserved_after_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    body=dict(code='media-too-large',section='object',title='old',consequence='c',next_step='n',state='current',operation='producer:download_media',objects=[dict(kind='material',id='unknown')])
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='media-problems-recovered-v2'")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:legacy-unparseable','background',%s,1)",(json.dumps(body),))
        store.problems.recover_media_problems();store.problems.snapshot(2)
        row=store.db.execute("SELECT finished,body FROM reader_problems WHERE id='old'").fetchone()
        assert row['finished'] is None and json.loads(row['body'])==body
    finally:store.db.close()


def test_real_material_replacement_captures_file_problem_before_deletion(tmp_path,postgres_url):
    from oko.collection.contracts import Material,Publication
    from tests.support.source import StructuredSource,publication_packet
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        store.ensure_demo()
        publication=Publication('p','https://example.test/p',1)
        packet=publication_packet(publication)
        with transaction(store.db):
            effects=store.collect_discovery([packet],'{}')
            store.jobs.apply_collection(effects)
        item=store.db.execute("SELECT id FROM publications WHERE url='https://example.test/p'").fetchone()[0]
        file='00000000-0000-4000-8000-000000000001'
        with transaction(store.db):
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES (%s,%s,'image:1','https://example.test/file','file','unavailable')",(file,item))
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media',%s,'failed','failure','media-too-large') RETURNING id",(file,)).fetchone()[0]
            store.problems.sync_media_problem(job,[],1)
            before=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()[0]
            described=StructuredSource(None).describe(publication,Material('New','New text'))
            _,effects=store.collect_material(item,described)
            store.jobs.apply_collection(effects)
            assert store.db.execute('SELECT 1 FROM media_files WHERE id=%s',(file,)).fetchone() is None
            assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(before,)).fetchone()[0]=='irrelevant'
    finally:store.db.close()


def test_real_material_replacement_rolls_back_file_job_and_problem(tmp_path,postgres_url):
    import pytest
    from oko.collection.contracts import Material,Publication
    from tests.support.source import StructuredSource,publication_packet
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        store.ensure_demo();publication=Publication('p','https://example.test/p',1)
        with transaction(store.db):
            effects=store.collect_discovery([publication_packet(publication)],'{}');store.jobs.apply_collection(effects)
        item=store.db.execute("SELECT id FROM publications WHERE url='https://example.test/p'").fetchone()[0]
        file='00000000-0000-4000-8000-000000000001'
        with store.db:
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES (%s,%s,'image:1','https://example.test/file','file','unavailable')",(file,item))
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media',%s,'failed','failure','media-too-large') RETURNING id",(file,)).fetchone()[0]
            store.problems.sync_media_problem(job,[],1)
        before=store.db.execute("SELECT id,body,finished FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                _,effects=store.collect_material(item,StructuredSource(None).describe(publication,Material('New','New text')))
                store.jobs.apply_collection(effects)
                assert store.db.execute('SELECT 1 FROM media_files WHERE id=%s',(file,)).fetchone() is None
                raise ValueError('rollback')
        assert store.db.execute('SELECT 1 FROM media_files WHERE id=%s',(file,)).fetchone()
        assert store.db.execute('SELECT status FROM jobs WHERE id=%s',(job,)).fetchone()[0]=='failed'
        after=store.db.execute('SELECT id,body,finished FROM reader_problems WHERE id=%s',(before['id'],)).fetchone()
        assert tuple(after)==tuple(before)
    finally:store.db.close()


def test_recovery_hundred_moved_media_roots_is_bounded(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='media-problems-recovered-v2'")
            store.db.execute("INSERT INTO feeds(id) SELECT 'f-'||n FROM generate_series(1,100) n")
            store.db.execute("INSERT INTO publications(id,viewed) SELECT 'r-'||n,0 FROM generate_series(1,100) n")
            store.db.execute("INSERT INTO publications(id,viewed) VALUES ('target',0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'f-'||n,'r-'||n,true FROM generate_series(1,100) n")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) SELECT 'r-'||n,'target' FROM generate_series(1,100) n")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('file','target','image:1','https://example.test/file','file','unavailable')")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','file','failed','failure','media-too-large')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'old-'||n,'job:media-too-large:old-'||n||':download_media:file','background',jsonb_build_object('code','media-too-large','section','object','title','t','consequence','c','next_step','n','state','current','operation','producer:download_media','objects',jsonb_build_array(jsonb_build_object('kind','material','id','r-'||n)))::text,1 FROM generate_series(1,100) n")
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.problems.recover_media_problems()
        finally:store.db.set_trace_callback(None)
        print(f'100 moved media roots: {len(sql)} SQL')
        assert len(sql)<100,len(sql)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'operation'='producer:download_media' AND finished IS NULL").fetchone()[0]==100
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE id LIKE 'old-%%' AND finished IS NULL").fetchone()[0]==100
    finally:store.db.close()


def test_media_change_to_subtitle_preserves_occurrence_when_not_paused(tmp_path,postgres_url):
    from oko.storage.media_job_problems import managed_candidates
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','youtube','youtube','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('video')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','video',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('file','video','image:1','https://example.test/file','file','unavailable')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','file','failed','failure','media-unavailable') RETURNING id").fetchone()[0]
            store.problems.sync_media_problem(job,[],1)
            old=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()[0]
            previous=managed_candidates(store,job,2)
            store.db.execute("UPDATE media_files SET resource_key='youtube:subtitles' WHERE id='file'")
            store.problems.sync_media_problem(job,previous,2)
        state=store.problems.snapshot(2)
        current=[p for p in state['current'] if p['code']=='media-unavailable']
        assert len(current)==1 and current[0]['id']==old
    finally:store.db.close()


def test_media_change_to_subtitle_during_pause_closes_file_error(tmp_path,postgres_url):
    from oko.storage.media_job_problems import managed_candidates
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','youtube','youtube','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('video')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','video',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('file','video','image:1','https://example.test/file','file','unavailable')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','file','pending','failure','media-unavailable') RETURNING id").fetchone()[0]
            store.problems.sync_media_problem(job,[],1)
            old=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()[0]
            previous=managed_candidates(store,job,2)
            store.youtube.blocked(2,900)
            store.db.execute("UPDATE media_files SET resource_key='youtube:subtitles' WHERE id='file'")
            store.problems.sync_media_problem(job,previous,3)
        state=store.problems.snapshot(3)
        assert not any(p['code']=='media-unavailable' for p in state['current'])
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(old,)).fetchone()[0]=='resolved'
    finally:store.db.close()


def test_restart_reassigns_old_orphan_problem_when_file_now_exists(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','file','failed','failure','media-too-large') RETURNING id").fetchone()[0]
            store.problems.sync_media_problem(job,[],1)
            old=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()[0]
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('file','article','image:1','https://example.test/file','file','unavailable')")
            store.db.execute("DELETE FROM state WHERE key='media-problems-recovered-v2'")
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        row=store.db.execute("SELECT id,body FROM reader_problems WHERE identity='job:media-too-large:feed:download_media:file' AND finished IS NULL").fetchone()
        assert row and row['id']==old and json.loads(row['body'])['objects']==[dict(kind='material',id='article')]
    finally:store.db.close()


def test_media_collision_with_other_producer_preserves_both_records(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    foreign=dict(code='media-too-large',section='object',title='foreign',consequence='saved',next_step='retry',objects=[dict(kind='material',id='article')],action=None,operation='producer:fetch',state='current',next_at=None)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('file','article','image:1','https://example.test/file','file','unavailable')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','file','failed','failure','media-too-large') RETURNING id").fetchone()[0]
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign','job:media-too-large:feed:download_media:file','background',%s,1)",(json.dumps(foreign),))
            store.problems.sync_media_problem(job,[],1)
            assert json.loads(store.db.execute("SELECT body FROM reader_problems WHERE id='foreign'").fetchone()[0])==foreign
            row=store.db.execute("SELECT id,identity FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
            assert row and row['identity'].startswith('job:media-isolated:')
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        with store.db:
            store.db.execute("UPDATE reader_problems SET finished=2,outcome='resolved' WHERE id='foreign'")
            store.problems.sync_media_problem(job,[],3)
        after=store.db.execute("SELECT id,identity FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
        assert tuple(after)==tuple(row)
    finally:store.db.close()


def test_ghost_media_legacy_with_active_publication_is_not_finished(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    body=dict(code='media-too-large',section='object',title='old',consequence='c',next_step='n',state='current',operation='producer:status',objects=[dict(kind='material',id='article')])
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='media-problems-recovered-v2'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('file','article','image:1','https://example.test/file','file','unavailable')")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','file','failed','failure','media-too-large')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:media-too-large:feed:download_media:ghost','background',%s,1)",(json.dumps(body),))
        store.problems.recover_media_problems();store.problems.snapshot(2)
        row=store.db.execute("SELECT finished,body FROM reader_problems WHERE id='old'").fetchone()
        assert row['finished'] is None and json.loads(row['body'])==body
    finally:store.db.close()


def test_marked_media_record_with_unrelated_object_is_preserved(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='media-problems-recovered-v2'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('article'),('unrelated')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('file','article','image:1','https://example.test/file','file','unavailable')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','file','failed','failure','media-too-large') RETURNING id").fetchone()[0]
            body=dict(code='media-too-large',section='object',title='old',consequence='c',next_step='n',state='current',operation='producer:download_media',media_job_id=str(job),media_file_id='file',objects=[dict(kind='material',id='article'),dict(kind='material',id='unrelated')])
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:media-too-large:feed:download_media:file','background',%s,1)",(json.dumps(body),))
        store.problems.recover_media_problems();store.problems.snapshot(2)
        old=store.db.execute("SELECT finished,body FROM reader_problems WHERE id='old'").fetchone()
        assert old['finished'] is None and json.loads(old['body'])==body
    finally:store.db.close()


def test_http_status_exposes_orphan_media_as_before(tmp_path,postgres_url):
    import psycopg
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    with psycopg.connect(postgres_url,autocommit=True) as db:
        db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','orphan','failed','failure','media-too-large')")
    with TestClient(create_app(tmp_path,token='test',database_url=postgres_url,api_only=True),headers=HEADERS) as client:
        response=client.get('/api/v3/status/details')
        assert response.status_code==200,response.text
        matches=[p for p in response.json()['problems']['current'] if p['code']=='source_unavailable' or p['code']=='work_failed']
        assert matches,response.text
        print('orphan wire',json.dumps({k:v for k,v in matches[0].items() if k not in ('id','created_at')},sort_keys=True))
        assert any(any(o.get('material_id')=='orphan' for o in p['affected']) for p in matches),response.text


def test_adapter_change_closes_old_media_error_without_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('file','article','image:1','https://example.test/file','file','unavailable')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','file','failed','failure','media-too-large') RETURNING id").fetchone()[0]
            store.problems.sync_media_problem(job,[],1)
            store.jobs.change_adapter('feed')
        assert store.db.execute("SELECT outcome FROM reader_problems WHERE identity='job:media-too-large:feed:download_media:file'").fetchone()[0]=='resolved'
    finally:store.db.close()


def test_orphan_cleanup_resolves_existing_problem_without_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','orphan','failed','failure','media-too-large') RETURNING id").fetchone()[0]
            store.problems.sync_media_problem(job,[],1)
            store.jobs.reconcile_cleanup()
        assert store.db.execute("SELECT outcome FROM reader_problems WHERE identity='job:media-too-large:orphan:download_media:orphan'").fetchone()[0]=='resolved'
    finally:store.db.close()


def test_source_grouped_media_is_owned_by_source_event(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('file','article','image:1','https://example.test/file','file','unavailable')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','file','running',1) RETURNING id").fetchone()[0]
            store.jobs.fail_media(Job(job,'download_media','file',1),AdapterIssue('source-connection','failure',AdapterErrorKind.permanent))
            assert not store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-connection:feed:download_media' AND finished IS NULL").fetchone()
        assert any(p['code']=='source-connection' for p in store.problems.snapshot(2)['current'])
        assert not any(p['id']=='job:source-connection:feed:download_media' for p in store.problems.candidates(2))
    finally:store.db.close()
