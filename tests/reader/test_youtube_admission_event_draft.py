import json
import pytest
from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.storage.youtube_admission_jobs import recover_flags
from oko.storage.youtube_admission import current


def test_full_material_job_events_update_admission_without_get(tmp_path,postgres_url):
    clock=[100.]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','youtube','p',0,1,'{}',1)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,problem_code,generation) VALUES ('fetch','p','pending','',1) RETURNING id").fetchone()[0]
            store.incomplete_draft.mark_job('fetch','p')
        recover_flags(store)
        store.youtube_admission_ready=True
        store.youtube.blocked(100,900)
        row=current(store)
        assert row and json.loads(row['body'])['objects']==[dict(kind='material',id='p')]
        old=row['id']
        with transaction(store.db):claim=store.jobs._claim(store.db.execute('SELECT * FROM jobs WHERE id=%s',(job,)).fetchone())
        with pytest.raises(RuntimeError):
            with transaction(store.db):
                store.jobs.finish(claim)
                raise RuntimeError('abort')
        assert current(store)['id']==old and json.loads(current(store)['body'])['objects']==[dict(kind='material',id='p')]
        with transaction(store.db):store.jobs.finish(claim)
        row=current(store)
        assert row['id']==old and json.loads(row['body'])['objects']==[]
    finally:store.db.close()


def test_unowned_malformed_marker_survives_status_read(tmp_path,postgres_url):
    from oko.storage.youtube_admission import sync
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            body=json.dumps(dict(code='youtube-admission',operation='producer:youtube-admission',youtube_admission_owner='youtube:admission',objects=[None]))
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('bad','youtube:admission:isolated:foreign','background',%s,1)",(body,))
            sync(store,[],200,100)
        store.youtube_admission_ready=True
        snap=store.problems.snapshot(100)
        assert any(p['id']=='bad' and p['objects']==[] for p in snap['current'])
        assert tuple(store.db.execute("SELECT body,finished FROM reader_problems WHERE id='bad'").fetchone())==(body,None)
    finally:store.db.close()


def test_actual_job_writers_refresh_admission_without_status_read(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','youtube','p',0,1,'{}',1)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','p',true)")
        store.youtube.blocked(100,900)
        row=current(store);old=row['id'];assert json.loads(row['body'])['objects']==[]
        with transaction(store.db):store.jobs.enqueue_fetch('p')
        with transaction(store.db):claimed=store.jobs._claim(store.db.execute("SELECT * FROM jobs WHERE kind='fetch' AND item='p'").fetchone())
        with transaction(store.db):store.jobs.retry(claimed,0,'pause',code='youtube-admission')
        row=current(store)
        assert row['id']==old and json.loads(row['body'])['objects']==[dict(kind='material',id='p')]
        with transaction(store.db):claimed=store.jobs._claim(store.db.execute("SELECT * FROM jobs WHERE kind='fetch' AND item='p'").fetchone())
        with transaction(store.db):store.jobs.finish(claimed)
        row=current(store)
        assert row['id']==old and json.loads(row['body'])['objects']==[]
    finally:store.db.close()


def test_detach_last_link_updates_admission_before_get(tmp_path,postgres_url):
    from oko.domain.collection import CollectionEffects
    from oko.storage import publication_links
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('root','youtube','root',0,1,'{}',1),('child','rss','child',0,1,'{}',1)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','root',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root','child')")
            store.db.execute("INSERT INTO jobs(kind,item,status,problem_code) VALUES ('fetch','child','pending','')")
            store.incomplete_draft.mark_materials(['root','child'])
        store.youtube.blocked(100,900)
        row=current(store);old=row['id']
        assert json.loads(row['body'])['objects']==[dict(kind='material',id='root')]
        with pytest.raises(RuntimeError):
            with transaction(store.db):
                publication_links.sync(store,'root',(),CollectionEffects())
                raise RuntimeError('abort')
        assert json.loads(current(store)['body'])['objects']==[dict(kind='material',id='root')]
        with transaction(store.db):publication_links.sync(store,'root',(),CollectionEffects())
        row=current(store)
        assert row['id']==old and json.loads(row['body'])['objects']==[]
    finally:store.db.close()


def test_restart_recovers_legacy_occurrence_and_active_job(tmp_path,postgres_url):
    clock=[100.]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        legacy=dict(code='youtube-admission',section='wait',title='YouTube ограничил запросы',consequence='Ожидают субтитров: 1.',next_step='Око повторит получение после паузы.',objects=[dict(kind='material',id='p')],action=None,operation='producer:status',state='retrying',next_at=200.)
        with transaction(store.db):
            store.db.execute("UPDATE youtube_access SET youtube_admission_recovered=false WHERE id=1")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','p',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,problem_code) VALUES ('fetch','p','pending','')")
            store.db.execute("UPDATE youtube_access SET blocked_until=200 WHERE id=1")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('legacy','youtube:admission','background',%s,50)",(json.dumps(legacy),))
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
        row=current(store)
        assert row['id']=='legacy' and row['youtube_admission_due']==200
        assert json.loads(row['body'])['objects']==[dict(kind='material',id='p')]
        assert store.db.execute("SELECT youtube_admission_candidate FROM jobs WHERE item='p'").fetchone()[0]
    finally:store.db.close()


def test_source_ownership_change_updates_admission_without_get(tmp_path,postgres_url):
    from oko.storage import source_groups
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','youtube','p',0,1,'{}',1)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','p','pending','failure','source-network') RETURNING id").fetchone()[0]
            store.incomplete_draft.mark_job('fetch','p')
        store.youtube.blocked(100,900)
        row=current(store);old=row['id']
        assert json.loads(row['body'])['objects']==[dict(kind='material',id='p')]
        with transaction(store.db):source_groups.sync_jobs(store,[job],100)
        assert store.db.execute('SELECT source_event_owned FROM jobs WHERE id=%s',(job,)).fetchone()[0]
        row=current(store)
        assert row['id']==old and json.loads(row['body'])['objects']==[]
    finally:store.db.close()


def test_viewed_after_virtual_expiry_does_not_restore_removed_material(tmp_path,postgres_url):
    clock=[100.]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','youtube','p',0,1,'{}',1)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','p',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,problem_code) VALUES ('fetch','p','pending','')")
            store.incomplete_draft.mark_job('fetch','p')
        store.youtube.blocked(100,900)
        old=current(store)['id']
        clock[0]=1001
        assert any(p['id']==old for p in store.problems.snapshot(clock[0])['recent'])
        assert store.viewed_set(['p'])
        row=store.db.execute('SELECT body,finished FROM reader_problems WHERE id=%s',(old,)).fetchone()
        assert row and row['finished'] is not None
        assert all(obj.get('id')!='p' for obj in json.loads(row['body'])['objects'])
    finally:store.db.close()


@pytest.mark.parametrize('body',[None,{'code':'youtube-admission','objects':[None]},{'code':'youtube-admission','objects':None}])
def test_malformed_legacy_survives_restart_and_projects_safely(tmp_path,postgres_url,body):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    raw=json.dumps(body)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('bad','youtube:admission','background',%s,1)",(raw,))
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
        snapshot=store.problems.snapshot(100)
        assert any(p['id']=='bad' and p['objects']==[] for p in snapshot['current'])
        assert tuple(store.db.execute("SELECT body,finished FROM reader_problems WHERE id='bad'").fetchone())==(raw,None)
    finally:store.db.close()


def test_single_job_change_updates_only_delta_without_group_scan(tmp_path,postgres_url):
    from oko.storage.youtube_admission_jobs import sync_ids
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,complete,incomplete_class) SELECT 'p-'||n,1,1 FROM generate_series(1,1000)n")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'yt','p-'||n,true FROM generate_series(1,1000)n")
            ids=[row['id'] for row in store.db.execute("INSERT INTO jobs(kind,item,status,problem_code) SELECT 'fetch','p-'||n,'pending','' FROM generate_series(1,1000)n RETURNING id")]
            sync_ids(store,ids)
        store.youtube.blocked(100,900)
        assert len(json.loads(current(store)['body'])['objects'])==1000
        statements=[];store.db.set_trace_callback(statements.append)
        try:
            with transaction(store.db):
                store.db.execute("UPDATE jobs SET status='done' WHERE kind='fetch' AND item='p-1'")
                store.incomplete_draft.mark_job('fetch','p-1')
        finally:store.db.set_trace_callback(None)
        pages=[q for q in statements if q.startswith('SELECT id FROM jobs WHERE youtube_admission_candidate')]
        print('candidate-group pages after one changed job:',len(pages))
        assert len(pages)==0 and len(json.loads(current(store)['body'])['objects'])==999
    finally:store.db.close()


def test_delta_preserves_object_until_last_contributor_finishes(tmp_path,postgres_url):
    from oko.storage.youtube_admission_jobs import sync_ids
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','p',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','file','pending_download')")
            ids=[r['id'] for r in store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','p','pending','bad',''),('download_media','m','pending','bad','ordinary') RETURNING id")]
            sync_ids(store,ids)
        store.youtube.blocked(100,900)
        old=current(store)['id'];assert json.loads(current(store)['body'])['objects']==[dict(kind='material',id='p')]
        with transaction(store.db):
            store.db.execute("UPDATE jobs SET status='done' WHERE kind='fetch' AND item='p'")
            store.incomplete_draft.mark_job('fetch','p')
        assert current(store)['id']==old and json.loads(current(store)['body'])['objects']==[dict(kind='material',id='p')]
        with transaction(store.db):
            store.db.execute("UPDATE jobs SET status='done' WHERE kind='download_media' AND item='m'")
            store.incomplete_draft.mark_job('download_media','m')
        assert current(store)['id']==old and json.loads(current(store)['body'])['objects']==[]
    finally:store.db.close()


def test_legacy_null_target_survives_recovery_and_stays_visible(tmp_path,postgres_url):
    clock=[100.]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with transaction(store.db):
            store.db.execute("UPDATE youtube_access SET youtube_admission_recovered=false WHERE id=1")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('fetch',NULL,'yt','pending','paused','youtube-admission')")
            store.db.execute('UPDATE youtube_access SET blocked_until=200 WHERE id=1')
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
        row=current(store);body=json.loads(row['body'])
        assert body['objects']==[] and 'материал не определён' in body['consequence']
        snapshot=store.problems.snapshot(clock[0])
        assert any(p['id']==row['id'] for p in snapshot['current'])
        assert store.db.execute("SELECT item FROM jobs WHERE kind='fetch' AND feed_id='yt'").fetchone()[0] is None
    finally:store.db.close()


def test_early_unblock_closes_same_occurrence_and_rollback_restores(tmp_path,postgres_url):
    clock=[100.]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        store.youtube.blocked(100,900)
        old=current(store)['id']
        clock[0]=110
        with pytest.raises(RuntimeError):
            with transaction(store.db):
                store.youtube.completed()
                raise RuntimeError('abort')
        assert current(store)['id']==old and store.youtube.status(clock[0])['paused']
        store.youtube.completed()
        row=store.db.execute('SELECT finished,outcome FROM reader_problems WHERE id=%s',(old,)).fetchone()
        assert tuple(row)==(110,'resolved') and current(store) is None
    finally:store.db.close()


def test_viewed_during_pause_never_restores_forgotten_object(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','youtube','p',0,1,'{}',1)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','p',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,problem_code) VALUES ('fetch','p','pending','')")
            store.incomplete_draft.mark_job('fetch','p')
        store.youtube.blocked(100,900)
        assert json.loads(current(store)['body'])['objects']==[dict(kind='material',id='p')]
        assert store.viewed_set(['p'])
        for row in store.db.execute("SELECT body FROM reader_problems WHERE body::jsonb->>'code'='youtube-admission'"):
            assert all(o.get('id')!='p' for o in json.loads(row['body'])['objects'])
    finally:store.db.close()


def test_null_target_feed_change_clears_unknown_without_waiting_for_get(tmp_path,postgres_url):
    from oko.storage.job_types import Job
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[]),('rss','rss','RSS','default','rss','rss','connected',ARRAY[]::text[])")
            job=store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('fetch',NULL,'yt','pending','paused','youtube-admission') RETURNING id").fetchone()[0]
            store.incomplete_draft.mark_job('fetch',None,job)
        store.youtube.blocked(100,900)
        assert 'материал не определён' in json.loads(current(store)['body'])['consequence']
        store.collection_status.start(Job(job,'fetch',None,0),'rss')
        assert not store.db.execute('SELECT youtube_admission_unknown FROM jobs WHERE id=%s',(job,)).fetchone()[0]
        assert 'материал не определён' not in json.loads(current(store)['body'])['consequence']
    finally:store.db.close()


def test_fetch_null_ownership_transition_refreshes_admission(tmp_path,postgres_url):
    from oko.storage.youtube_admission_jobs import sync_ids
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,fetch_null_owned) VALUES ('fetch','p','pending','failure',NULL,true) RETURNING id").fetchone()[0]
            sync_ids(store,[job])
        store.youtube.blocked(100,900)
        assert json.loads(current(store)['body'])['objects']==[]
        with transaction(store.db):store.problems.sync_fetch(job,[],100)
        flags=store.db.execute('SELECT fetch_null_owned,youtube_admission_candidate,fetch_youtube_sensitive FROM jobs WHERE id=%s',(job,)).fetchone()
        assert tuple(flags)==(True,True,True)
        assert json.loads(current(store)['body'])['objects']==[dict(kind='material',id='p')]
    finally:store.db.close()


def test_delta_moves_null_target_to_valid_material(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('fetch',NULL,'yt','pending','paused','youtube-admission') RETURNING id").fetchone()[0]
            store.incomplete_draft.mark_job('fetch',None,job)
        store.youtube.blocked(100,900)
        old=current(store)['id'];assert 'материал не определён' in json.loads(current(store)['body'])['consequence']
        with transaction(store.db):
            store.db.execute("UPDATE jobs SET item='p' WHERE id=%s",(job,))
            store.incomplete_draft.mark_job('fetch','p',job)
        row=current(store);body=json.loads(row['body'])
        assert row['id']==old and body['objects']==[dict(kind='material',id='p')]
        assert 'материал не определён' not in body['consequence']
    finally:store.db.close()


def test_feed_deletion_updates_orphan_job_owner_without_get(tmp_path,postgres_url):
    from oko.storage.job_types import Job
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','deleting',ARRAY[]::text[])")
            job=store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('fetch','orphan','yt','pending','failure','youtube-admission') RETURNING id").fetchone()[0]
            delete=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('feed-delete','yt','running',1) RETURNING id").fetchone()[0]
            store.incomplete_draft.mark_job('fetch','orphan',job)
        store.youtube.blocked(100,900)
        assert json.loads(current(store)['body'])['objects']==[dict(kind='material',id='orphan')]
        store.jobs.finish_feed_deletion(Job(delete,'feed-delete','yt',1))
        assert not store.db.execute("SELECT 1 FROM feeds WHERE id='yt'").fetchone()
        assert json.loads(current(store)['body'])['objects']==[]
    finally:store.db.close()


def test_new_feed_reclaims_matching_orphan_job_without_get(tmp_path,postgres_url,monkeypatch):
    from oko.subscriptions.subscriptions import AddFeed
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            job=store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('fetch','orphan','fixed-feed','pending','failure','youtube-admission') RETURNING id").fetchone()[0]
            store.incomplete_draft.mark_job('fetch','orphan',job)
        store.youtube.blocked(100,900)
        assert json.loads(current(store)['body'])['objects']==[]
        monkeypatch.setattr(store.subscriptions,'new_feed_id',lambda:'fixed-feed')
        store.subscriptions.add_feed(AddFeed(url='https://www.youtube.com/feeds/videos.xml?channel_id=UC'+'a'*22,title='YouTube',folder='Inbox',verification='synthetic'))
        assert json.loads(current(store)['body'])['objects']==[dict(kind='material',id='orphan')]
    finally:store.db.close()


def test_deleted_viewed_publication_is_not_restored_by_orphan_job(tmp_path,postgres_url):
    from oko.storage.job_types import Job
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','youtube','p',0,1,'{}',1)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,problem_code) VALUES ('fetch','p','yt','pending','') RETURNING id").fetchone()[0]
            store.incomplete_draft.mark_job('fetch','p',job)
        store.youtube.blocked(100,900)
        assert json.loads(current(store)['body'])['objects']==[dict(kind='material',id='p')]
        assert store.viewed_set(['p'])
        with transaction(store.db):
            delete=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('delete','p','running',1) ON CONFLICT(kind,item) DO UPDATE SET status='running',generation=1 RETURNING id").fetchone()[0]
        store.jobs.delete(Job(delete,'delete','p',1))
        assert not store.db.execute("SELECT 1 FROM publications WHERE id='p'").fetchone()
        with transaction(store.db):store.incomplete_draft.mark_job('fetch','p',job)
        assert all(o.get('id')!='p' for row in store.db.execute("SELECT body FROM reader_problems WHERE body::jsonb->>'code'='youtube-admission'") for o in json.loads(row['body'])['objects'])
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','youtube','p',0,1,'{}',1)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','p',true)")
            store.incomplete_draft.mark_materials(['p'])
        assert json.loads(current(store)['body'])['objects']==[dict(kind='material',id='p')]
        assert store.db.execute('SELECT youtube_admission_forgotten_item FROM jobs WHERE id=%s',(job,)).fetchone()[0] is None
    finally:store.db.close()


def test_legacy_null_viewed_is_reported_as_unknown_without_disclosure(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,viewed) VALUES ('p',NULL)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,problem_code) VALUES ('fetch','p','pending','') RETURNING id").fetchone()[0]
            store.incomplete_draft.mark_job('fetch','p',job)
        store.youtube.blocked(100,900)
        body=json.loads(current(store)['body'])
        assert body['objects']==[] and 'материал не определён' in body['consequence']
        assert store.db.execute("SELECT viewed FROM publications WHERE id='p'").fetchone()[0] is None
    finally:store.db.close()


def test_foreign_canonical_occurrence_survives_paused_status_read(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        body=json.dumps(dict(code='foreign',section='object',title='Foreign',consequence='Saved',next_step='Inspect',objects=[],action=None,operation='producer:status',state='current',next_at=None))
        with transaction(store.db):store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign','youtube:admission','background',%s,1)",(body,))
        store.youtube.blocked(100,900)
        own=current(store);assert own and own['id']!='foreign'
        result=store.problems.snapshot(100)
        assert {p['id'] for p in result['current']} >= {'foreign',own['id']}
        assert tuple(store.db.execute("SELECT body,finished FROM reader_problems WHERE id='foreign'").fetchone())==(body,None)
    finally:store.db.close()


def test_unowned_legacy_with_new_marker_is_not_adopted(tmp_path,postgres_url):
    from oko.storage.youtube_admission import candidate
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        body=candidate([],None,unknown_time=True)
        body['operation']='producer:status'
        raw=json.dumps(body)
        with transaction(store.db):store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign','youtube:admission','background',%s,1)",(raw,))
        store.youtube.blocked(100,900)
        assert current(store)['id']!='foreign'
        assert tuple(store.db.execute("SELECT body,finished FROM reader_problems WHERE id='foreign'").fetchone())==(raw,None)
    finally:store.db.close()


def test_canonical_action_origin_is_not_adopted(tmp_path,postgres_url):
    from oko.storage.youtube_admission import candidate
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        body=candidate([],200);body['operation']='producer:status';raw=json.dumps(body)
        with transaction(store.db):store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign','youtube:admission','action',%s,1)",(raw,))
        store.youtube.blocked(100,900)
        assert current(store)['id']!='foreign'
        store.problems.snapshot(100)
        assert tuple(store.db.execute("SELECT body,finished FROM reader_problems WHERE id='foreign'").fetchone())==(raw,None)
    finally:store.db.close()


def test_unowned_valid_marker_is_preserved_and_shown_as_unknown(tmp_path,postgres_url):
    from oko.storage.youtube_admission import candidate,sync
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        fake=candidate([],200);fake['youtube_admission_owner']='youtube:admission';raw=json.dumps(fake)
        with transaction(store.db):
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('fake','youtube:admission:isolated:foreign','background',%s,1)",(raw,))
            actual=sync(store,[],200,100)
        snap=store.problems.snapshot(100)
        old=next(p for p in snap['current'] if p['id']=='fake')
        assert old['title']=='Сведения об ограничении YouTube недоступны'
        assert any(p['id']==actual for p in snap['current'])
        assert tuple(store.db.execute("SELECT body,finished FROM reader_problems WHERE id='fake'").fetchone())==(raw,None)
    finally:store.db.close()


def test_legacy_inconsistent_outcome_is_not_adopted_or_rewritten(tmp_path,postgres_url):
    from oko.storage.youtube_admission import candidate
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        body=candidate([],200);body['operation']='producer:status';raw=json.dumps(body)
        with transaction(store.db):store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created,outcome) VALUES ('old','youtube:admission','background',%s,1,'irrelevant')",(raw,))
        store.youtube.blocked(100,900)
        assert current(store)['id']!='old'
        snap=store.problems.snapshot(100)
        assert any(p['id']=='old' and p['objects']==[] for p in snap['current'])
        assert tuple(store.db.execute("SELECT body,finished,outcome FROM reader_problems WHERE id='old'").fetchone())==(raw,None,'irrelevant')
    finally:store.db.close()


def test_legacy_duplicate_objects_are_not_rewritten(tmp_path,postgres_url):
    from oko.storage.youtube_admission import candidate
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        body=candidate(['p'],200);body['operation']='producer:status';body['objects'].append(dict(kind='material',id='p'));raw=json.dumps(body)
        with transaction(store.db):store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','youtube:admission','background',%s,1)",(raw,))
        store.youtube.blocked(100,900)
        assert current(store)['id']!='old'
        store.problems.snapshot(100)
        assert tuple(store.db.execute("SELECT body,finished FROM reader_problems WHERE id='old'").fetchone())==(raw,None)
    finally:store.db.close()


def test_restart_preserves_current_occurrence_and_does_not_rewrite_it(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,problem_code) VALUES ('fetch','p','pending','') RETURNING id").fetchone()[0]
            store.incomplete_draft.mark_job('fetch','p',job)
        store.youtube.blocked(100,900)
        before=store.db.execute("SELECT id,body,xmin::text FROM reader_problems WHERE identity='youtube:admission' AND finished IS NULL").fetchone()
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
        after=store.db.execute("SELECT id,body,xmin::text FROM reader_problems WHERE identity='youtube:admission' AND finished IS NULL").fetchone()
        assert tuple(after)==tuple(before)
    finally:store.db.close()


def test_viewing_linked_roots_preserves_other_then_forgets_orphan(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('a','youtube','a',0,1,'{}',1),('b','youtube','b',0,1,'{}',1),('child','rss','c',0,1,'{}',1)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','a',true),('yt','b',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('a','child'),('b','child')")
            job=store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,problem_code) VALUES ('fetch','child','yt','pending','') RETURNING id").fetchone()[0]
            store.incomplete_draft.mark_job('fetch','child',job)
        store.youtube.blocked(100,900)
        assert {x['id'] for x in json.loads(current(store)['body'])['objects']}=={'a','b'}
        assert store.viewed_set(['a'])
        assert {x['id'] for x in json.loads(current(store)['body'])['objects']}=={'b'}
        assert store.viewed_set(['b'])
        for row in store.db.execute("SELECT body FROM reader_problems WHERE body::jsonb->>'code'='youtube-admission'"):
            assert not {o['id'] for o in json.loads(row['body'])['objects']} & {'a','b','child'}
    finally:store.db.close()
