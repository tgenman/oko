import json
import pytest
from tests.support.storage import Store
from oko.storage.transaction import transaction


def seed(store,status):
    with store.db:
        store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
        content=json.dumps({'extra':{'_reader':{'full':False,'reason':'partial'}}})
        store.db.execute("INSERT INTO publications(id,platform,external_id,url,published,viewed,complete,content,incomplete_class) VALUES ('p','rss','p','https://p.test',1,0,1,%s,0)",(content,))
        store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
        job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,generation) VALUES ('fetch','p',%s,NULL,NULL,1) RETURNING id",(status,)).fetchone()[0]
        from oko.storage.incomplete_group_draft import save_keys
        save_keys(store.db,['p'])
        store.incomplete_draft.mark(['p'])
    return job


def current(store):
    return store.db.execute("SELECT id,body FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'code'='material-incomplete'").fetchone()


def test_fetch_finish_opens_incomplete_before_status_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        job=seed(store,'pending')
        assert not any(p['code']=='material-incomplete' for p in store.problems.snapshot(1)['current'])
        with transaction(store.db):
            claimed=store.jobs._claim(store.db.execute('SELECT * FROM jobs WHERE id=%s',(job,)).fetchone())
        assert claimed is not None
        with transaction(store.db):store.jobs.finish(claimed)
        row=current(store)
        assert row is not None and json.loads(row['body'])['objects']==[{'kind':'material','id':'p'}]
    finally:store.db.close()


def test_fetch_claim_keeps_sticky_occurrence_and_marks_retrying_before_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        job=seed(store,'failed')
        store.problems.snapshot(1)
        before=current(store)
        assert before and json.loads(before['body'])['state']=='current'
        with store.db:store.db.execute("UPDATE jobs SET status='pending' WHERE id=%s",(job,))
        with transaction(store.db):store.jobs._claim(store.db.execute('SELECT * FROM jobs WHERE id=%s',(job,)).fetchone())
        row=current(store)
        assert row and row['id']==before['id'] and json.loads(row['body'])['state']=='retrying'
    finally:store.db.close()


def test_source_error_finishes_incomplete_in_event_transaction(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        job=seed(store,'failed')
        store.problems.snapshot(1)
        old=current(store)['id']
        with store.db:store.db.execute("UPDATE jobs SET status='pending' WHERE id=%s",(job,))
        with transaction(store.db):claimed=store.jobs._claim(store.db.execute('SELECT * FROM jobs WHERE id=%s',(job,)).fetchone())
        with transaction(store.db):store.jobs.retry(claimed,store.clock(),'network',code='source-network')
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()[0] is not None
    finally:store.db.close()


def test_claim_incomplete_event_rolls_back_and_clears_ephemeral_state(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        job=seed(store,'failed');store.problems.snapshot(1)
        before=current(store)
        with store.db:store.db.execute("UPDATE jobs SET status='pending' WHERE id=%s",(job,))
        with pytest.raises(RuntimeError,match='rollback'):
            with transaction(store.db):
                store.jobs._claim(store.db.execute('SELECT * FROM jobs WHERE id=%s',(job,)).fetchone())
                raise RuntimeError('rollback')
        row=current(store)
        assert tuple(row)==tuple(before)
        assert store.db.execute("SELECT to_regclass('pg_temp.oko_incomplete_work')").fetchone()[0] is None
        assert store.db.execute('SELECT status FROM jobs WHERE id=%s',(job,)).fetchone()[0]=='pending'
    finally:store.db.close()


def test_source_removed_finishes_incomplete_before_status_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        seed(store,'done');store.problems.snapshot(1)
        old=current(store)['id']
        with transaction(store.db):store.mark_source_removed('p')
        assert tuple(store.db.execute('SELECT outcome,finished IS NOT NULL FROM reader_problems WHERE id=%s',(old,)).fetchone())==('irrelevant',True)
    finally:store.db.close()


def test_content_becoming_full_finishes_incomplete_before_status_get(tmp_path,postgres_url):
    from oko.collection.contracts import Material,Publication
    from tests.support.source import StructuredSource
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        seed(store,'done');store.problems.snapshot(1)
        old=current(store)['id']
        with store.db:store.db.execute("UPDATE publications SET source_id='rss:p' WHERE id='p'")
        publication=Publication('p','https://p.test',1)
        material=StructuredSource(None).describe(publication,Material('Full','Complete',full=True))
        with transaction(store.db):
            _,effects=store.collect_material('p',material);store.jobs.apply_collection(effects)
        assert tuple(store.db.execute('SELECT outcome,finished IS NOT NULL FROM reader_problems WHERE id=%s',(old,)).fetchone())==('resolved',True)
    finally:store.db.close()


def test_removing_primary_feed_moves_group_without_status_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        seed(store,'done')
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('g','g','G','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('g','p',false)")
        store.problems.snapshot(1)
        old=current(store)['id']
        store.remove_feed('f')
        oldrow=store.db.execute('SELECT finished,outcome FROM reader_problems WHERE id=%s',(old,)).fetchone()
        assert oldrow['finished'] is not None
        new=store.db.execute("SELECT id FROM reader_problems WHERE identity='incomplete:g:p' AND finished IS NULL").fetchone()
        assert new and new['id']!=old
    finally:store.db.close()


def test_linking_failed_child_closes_parent_incomplete_before_get(tmp_path,postgres_url):
    from oko.collection.adapters import ExternalKey,ExternalReference,Platform
    from oko.domain.collection import CollectionEffects
    from oko.storage import publication_links
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        seed(store,'done');store.problems.snapshot(1)
        old=current(store)['id']
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,url,viewed,complete) VALUES ('child','rss','child','https://child.test',0,0)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','child','failed','bad','source-network')")
        with transaction(store.db):
            effects=CollectionEffects()
            publication_links.sync(store,'p',(ExternalReference(ExternalKey(Platform.rss,'child'),'https://child.test'),),effects)
            store.jobs.apply_collection(effects)
        row=store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()
        assert row['finished'] is not None
    finally:store.db.close()


def test_profile_problem_resolving_rechecks_incomplete_before_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        job=seed(store,'failed')
        with store.db:
            store.db.execute("UPDATE jobs SET error='auth',problem_code='identity-unconfirmed',feed_id='f' WHERE id=%s",(job,))
            body=dict(code='profile_unavailable',section='object',title='profile',consequence='saved',next_step='retry',objects=[dict(kind='feed',id='f')],action=None,operation='producer:profile',state='current',next_at=None)
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('profile','profile:f','background',%s,1)",(json.dumps(body),))
        store.problems.snapshot(1)
        old=current(store)['id']
        with transaction(store.db):store.problems.sync_profiles(2,{'f'})
        assert store.db.execute('SELECT finished IS NOT NULL FROM reader_problems WHERE id=%s',(old,)).fetchone()[0]
    finally:store.db.close()


def test_status_get_does_not_reconcile_event_owned_incomplete(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        seed(store,'failed')
        before=current(store)
        assert before is not None
        queries=[];store.db.set_trace_callback(queries.append)
        try:result=store.problems.snapshot(1)
        finally:store.db.set_trace_callback(None)
        assert any(p['id']==before['id'] and p['code']=='material-incomplete' for p in result['current'])
        assert tuple(current(store))==tuple(before)
        assert not [q for q in queries if q.lstrip().upper().startswith(('UPDATE READER_PROBLEMS','INSERT INTO READER_PROBLEMS','DELETE FROM READER_PROBLEMS'))]
    finally:store.db.close()


def test_unknown_content_is_reported_after_real_writer_hook(tmp_path,postgres_url):
    from oko.storage.incomplete_index import save_class
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        seed(store,'done')
        unknown={'extra':{'_reader':{'full':{'unexpected':'value'}}}}
        with transaction(store.db):
            store.db.execute("UPDATE publications SET content=%s WHERE id='p'",(json.dumps(unknown),))
            save_class(store.db,'p',unknown)
        assert store.db.execute("SELECT incomplete_group_unknown FROM publications WHERE id='p'").fetchone()[0]
        with pytest.raises(ValueError,match='Unrecognized publication completeness'):store.problems.snapshot(1)
    finally:store.db.close()


def test_forgetting_root_rechecks_surviving_child_before_get(tmp_path,postgres_url):
    from oko.storage.incomplete_group_draft import save_keys
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[]),('g','g','G','default','rss','rss','connected',ARRAY[]::text[])")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('root','rss','root',0,1,'{}',1),('child','rss','child',0,1,%s,0)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','root',true),('g','child',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root','child')")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','child','failed','bad','source-network')")
            save_keys(store.db,['root','child'])
            store.incomplete_draft.mark(['child'])
        old=store.db.execute("SELECT id FROM reader_problems WHERE identity='incomplete:g:child' AND finished IS NULL").fetchone()
        assert old
        with transaction(store.db):store.publications.forget_many(['root'])
        assert store.db.execute('SELECT finished IS NOT NULL FROM reader_problems WHERE id=%s',(old['id'],)).fetchone()[0]
    finally:store.db.close()


def test_media_finish_opens_incomplete_before_status_get(tmp_path,postgres_url):
    from oko.storage.job_types import Job
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        seed(store,'done')
        with store.db:
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image:1','x','file','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,error,problem_code) VALUES ('download_media','m','running',1,NULL,NULL) RETURNING id").fetchone()[0]
            store.incomplete_draft.mark_materials(['p'])
        # Existing incomplete stays sticky during waiting; start from no occurrence.
        with store.db:store.db.execute("UPDATE reader_problems SET finished=1,outcome='resolved' WHERE body::jsonb->>'code'='material-incomplete' AND finished IS NULL")
        with transaction(store.db):store.jobs.finish(Job(job,'download_media','m',1))
        assert current(store) is not None
    finally:store.db.close()


def test_primary_feed_change_preserves_sticky_wait_across_group_rekey(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        job=seed(store,'failed')
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('g','g','G','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('g','p',false)")
            store.db.execute("UPDATE jobs SET status='pending' WHERE id=%s",(job,))
        assert current(store)
        store.remove_feed('f')
        new=store.db.execute("SELECT body FROM reader_problems WHERE identity='incomplete:g:p' AND finished IS NULL").fetchone()
        assert new and json.loads(new['body'])['state']=='retrying'
    finally:store.db.close()


def test_bulk_primary_feed_change_keeps_sticky_across_batch_boundary(tmp_path,postgres_url):
    from oko.storage.incomplete_group_draft import save_keys,sync_groups_batch
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[]),('g','g','G','default','rss','rss','connected',ARRAY[]::text[])")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) SELECT 'p-'||n,'rss','p-'||n,0,1,%s,0 FROM generate_series(1,101)n",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT feed,'p-'||n,feed='f' FROM generate_series(1,101)n CROSS JOIN (VALUES ('f'),('g')) v(feed)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'fetch','p-'||n,'failed',NULL,NULL FROM generate_series(1,101)n")
            items=['p-'+str(n) for n in range(1,102)]
            save_keys(store.db,items)
            for start in range(0,len(items),100):sync_groups_batch(store,['incomplete:f:'+item for item in items[start:start+100]],1)
            store.db.execute("UPDATE jobs SET status='pending' WHERE kind='fetch'")
        store.remove_feed('f')
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE identity LIKE 'incomplete:g:%%' AND finished IS NULL AND body::jsonb->>'state'='retrying'").fetchone()[0]==101
    finally:store.db.close()


def test_restart_profile_reconciliation_updates_group_before_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        job=seed(store,'failed')
        with store.db:
            store.db.execute("UPDATE jobs SET error='auth',problem_code='identity-unconfirmed',feed_id='f' WHERE id=%s",(job,))
            body=dict(code='profile_unavailable',section='object',title='profile',consequence='saved',next_step='retry',objects=[dict(kind='feed',id='f')],action=None,operation='producer:profile',state='current',next_at=None)
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('profile','profile:f','background',%s,1)",(json.dumps(body),))
            store.incomplete_draft.mark_materials(['p'])
        old=current(store)['id']
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url,defer_recovery=True)
        assert store.db.execute('SELECT finished IS NOT NULL FROM reader_problems WHERE id=%s',(old,)).fetchone()[0]
    finally:store.db.close()


def test_deleting_feed_rechecks_unrelated_publication_with_same_id(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[]),('g','g','G','default','rss','rss','connected',ARRAY[]::text[])")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('f','rss','same-id',0,1,%s,0)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('g','f',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('filter','f','running','bad','source-network')")
            from oko.storage.incomplete_group_draft import save_keys
            save_keys(store.db,['f'])
            store.incomplete_draft.mark(['f'])
        assert not current(store)
        with transaction(store.db):
            store.remove_feed('f')
            store.jobs.remove_feed('f',set())
        row=store.db.execute("SELECT id FROM reader_problems WHERE identity='incomplete:g:f' AND finished IS NULL").fetchone()
        assert row
    finally:store.db.close()


def test_orphan_media_cleanup_rechecks_colliding_publication_id(tmp_path,postgres_url):
    from oko.storage.incomplete_group_draft import save_keys
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('m','rss','m',0,1,%s,0)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','m',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m','failed','bad','source-network')")
            save_keys(store.db,['m']);store.incomplete_draft.mark(['m'])
        assert not current(store)
        with transaction(store.db):store.jobs.reconcile_cleanup()
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='incomplete:f:m' AND finished IS NULL").fetchone()
    finally:store.db.close()


def test_time_sensitive_fetch_flag_tracks_link_and_owner_changes(tmp_path,postgres_url):
    from oko.storage import memberships,publication_links
    from oko.storage.incomplete_group_draft import save_keys
    from oko.collection.adapters import ExternalKey,ExternalReference,Platform
    from oko.domain.collection import CollectionEffects
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','Y','default','youtube','rss','connected',ARRAY[]::text[]),('rss','rss','R','default','rss','rss','connected',ARRAY[]::text[])")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,url,viewed,complete,content,incomplete_class) VALUES ('root','youtube','root','https://root',0,1,%s,0),('child','rss','child','https://child',0,0,'{}',1)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','root',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root','child')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','child','failed','bad',NULL) RETURNING id").fetchone()[0]
            save_keys(store.db,['root']);store.incomplete_draft.mark_materials(['child'])
        assert store.db.execute('SELECT incomplete_youtube_sensitive FROM jobs WHERE id=%s',(job,)).fetchone()[0]
        store.youtube.start(100)
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='incomplete:yt:root' AND finished IS NULL").fetchone()
        with transaction(store.db):memberships.add(store.db,'rss','child')
        assert not store.db.execute('SELECT incomplete_youtube_sensitive FROM jobs WHERE id=%s',(job,)).fetchone()[0]
        assert not store.db.execute("SELECT 1 FROM reader_problems WHERE identity='incomplete:yt:root' AND finished IS NULL").fetchone()
    finally:store.db.close()


def test_media_schedule_rechecks_colliding_publication_parent(tmp_path,postgres_url):
    from oko.storage.incomplete_group_draft import save_keys
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('root','rss','root',0,1,%s,0),('m','rss','m',0,1,'{}',1),('owner','rss','owner',0,1,'{}',1)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','root',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root','m')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','owner','image:1','x','file','unavailable')")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m','pending','bad','source-network')")
            save_keys(store.db,['root']);store.incomplete_draft.mark(['root'])
        assert not current(store)
        with transaction(store.db):store.jobs.schedule_media('owner')
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='incomplete:f:root' AND finished IS NULL").fetchone()
    finally:store.db.close()


def test_media_retry_rechecks_colliding_publication_parent(tmp_path,postgres_url):
    from oko.storage.incomplete_group_draft import save_keys
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('root','rss','root',0,1,%s,0),('m','rss','m',0,1,'{}',1),('owner','rss','owner',0,1,'{}',1)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','root',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root','m')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','owner','image:1','x','file','unavailable')")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m','failed','bad','source-network')")
            save_keys(store.db,['root']);store.incomplete_draft.mark(['root'])
        assert not current(store)
        with transaction(store.db):store.jobs.retry_media('owner',revalidate_ready=False)
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='incomplete:f:root' AND finished IS NULL").fetchone()
    finally:store.db.close()


def test_time_sensitive_flag_clears_when_material_becomes_full(tmp_path,postgres_url):
    from oko.storage.incomplete_index import save_class
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        job=seed(store,'failed')
        with store.db:
            store.db.execute("UPDATE feeds SET kind='youtube' WHERE id='f'")
            store.db.execute("UPDATE jobs SET error='bad',problem_code=NULL WHERE id=%s",(job,))
            store.incomplete_draft.mark(['p'])
        assert store.db.execute('SELECT incomplete_youtube_sensitive FROM jobs WHERE id=%s',(job,)).fetchone()[0]
        full={'extra':{'_reader':{'full':True}}}
        with transaction(store.db):
            store.db.execute("UPDATE publications SET content=%s WHERE id='p'",(json.dumps(full),))
            save_class(store.db,'p',full)
        assert not store.db.execute('SELECT incomplete_youtube_sensitive FROM jobs WHERE id=%s',(job,)).fetchone()[0]
    finally:store.db.close()


def test_identify_platform_change_resynchronizes_caption_wait(tmp_path,postgres_url):
    from oko.collection.adapters import ExternalKey,ExternalReference,Platform
    from oko.storage.incomplete_group_draft import save_keys
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','rss','p',0,1,%s,0)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,status,source_locator,name) VALUES ('caption','p','youtube:subtitles','pending_download','https://example.test/caption','file')")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','caption','pending',NULL,NULL)")
            store.db.execute("UPDATE youtube_access SET next_at=200 WHERE id=1")
            save_keys(store.db,['p']);store.incomplete_draft.mark(['p'])
        # Existing waiting media suppresses initial creation: establish a sticky occurrence first.
        with store.db:
            store.db.execute("UPDATE jobs SET status='done' WHERE item='caption'")
            store.incomplete_draft.mark(['p'])
        with store.db:
            store.db.execute("UPDATE jobs SET status='pending' WHERE item='caption'")
            store.incomplete_draft.mark(['p'])
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='incomplete:f:p' AND finished IS NULL").fetchone()
        with transaction(store.db):
            store.publications.identify('p',ExternalReference(ExternalKey(Platform.youtube,'v'),'https://example.test/v'))
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='incomplete:f:p' AND finished IS NULL").fetchone() is None
    finally:store.db.close()
