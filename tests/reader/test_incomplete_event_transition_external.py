from tests.support.collection_acceptance import apply_simulated_collection, running_collection_job
import json
import pytest
from tests.support.storage import Store
from oko.storage.transaction import transaction


def seed(store,status):
    with store.db:
        store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
        content=json.dumps({'blocks':[{'type':'text','role':'body','text':'partial'}],'extra':{'_reader':{'full':False,'reason':'partial'}}})
        store.db.execute("INSERT INTO publications(id,platform,external_id,url,published_at,content,status) VALUES ('p','other_web','https://p.test','https://p.test',1,%s,'pending_collection')",(content,))
        store.db.execute("UPDATE publications SET feed_id='f' WHERE id='p'")
        job=store.db.execute("INSERT INTO jobs(kind,item,status,reason_message,reason_code,generation) VALUES ('fetch','p',%s,NULL,NULL,1) RETURNING id",(status,)).fetchone()[0]
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


def test_content_becoming_full_finishes_incomplete_before_status_get(tmp_path,postgres_url):
    from tests.support.legacy_source import Publication
    from tests.support.synthetic_material import material as Material
    from tests.support.source import StructuredSource
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        seed(store,'done');store.problems.snapshot(1)
        old=current(store)['id']
        publication=Publication('p','https://p.test',1)
        material=StructuredSource(None).describe(publication,Material('Full','Complete',full=True))
        with transaction(store.db):
            job_for_collection=running_collection_job(store,'fetch','p')
            _,effects=store.collected_publication.accept_content('p',material.adapter_content);apply_simulated_collection(store,effects,job=job_for_collection)
        assert tuple(store.db.execute('SELECT outcome,finished IS NOT NULL FROM reader_problems WHERE id=%s',(old,)).fetchone())==('resolved',True)
    finally:store.db.close()


def test_legacy_full_flag_does_not_create_problem_for_ready_publication(tmp_path,postgres_url):
    from tests.support.worker_library import worker_library
    from oko.storage.incomplete_index import save_class
    store,item,_,_=worker_library(tmp_path,database_url=postgres_url)
    try:
        with transaction(store.db):
            raw=store.db.execute('SELECT content FROM publications WHERE id=%s',(item,)).fetchone()[0]
            content=raw if isinstance(raw,dict) else json.loads(raw)
            content['extra'].setdefault('_reader',{})['full']=False
            content['extra']['_reader']['reason']='stale legacy value'
            store.db.execute("UPDATE publications SET status='ready',content=%s WHERE id=%s",(json.dumps(content),item))
            save_class(store.db,item,content)
        problems=store.problems.snapshot(store.wall_clock())['current']
        assert not any(p['code']=='material-incomplete' and any(o['id']==item for o in p['objects']) for p in problems)
    finally:store.db.close()


def test_pending_collection_problem_does_not_depend_on_legacy_full_flag(tmp_path,postgres_url):
    from oko.storage.incomplete_index import save_class
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        seed(store,'done')
        assert current(store) is not None
        with transaction(store.db):
            raw=store.db.execute("SELECT content FROM publications WHERE id='p'").fetchone()[0]
            content=raw if isinstance(raw,dict) else json.loads(raw)
            content['extra']['_reader']['full']=True
            content['extra']['_reader']['reason']=''
            store.db.execute("UPDATE publications SET content=%s WHERE id='p'",(json.dumps(content),))
            save_class(store.db,'p',content)
        assert current(store) is not None
    finally:store.db.close()


def test_status_change_recomputes_incomplete_diagnostic_without_content_rewrite(tmp_path,postgres_url):
    from oko.storage.incomplete_index import save_class
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        seed(store,'done')
        with transaction(store.db):
            content=store.db.execute("SELECT content FROM publications WHERE id='p'").fetchone()[0]
            store.db.execute("UPDATE publications SET status='ready' WHERE id='p'")
            save_class(store.db,'p',content)
        assert current(store) is None
        with transaction(store.db):
            store.db.execute("UPDATE publications SET status='pending_collection' WHERE id='p'")
            store.incomplete_draft.mark_materials(['p'])
        assert current(store) is not None
    finally:store.db.close()


def test_restart_recovers_pending_problem_from_status_not_legacy_flags(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    seed(store,'failed')
    with store.db:
        raw=store.db.execute("SELECT content FROM publications WHERE id='p'").fetchone()[0]
        content=raw if isinstance(raw,dict) else json.loads(raw)
        content['extra']['_reader']['full']=True
        content['extra']['_reader']['reason']=''
        content['extra']['_reader']['source_manifest']={'preview_fragment':'partial','post':{},'resources':[]}
        store.db.execute("UPDATE publications SET content=%s WHERE id='p'",(json.dumps(content),))
        store.db.execute("DELETE FROM reader_problems WHERE body::jsonb->>'code'='material-incomplete'")
        store.db.execute("DELETE FROM state WHERE key IN ('incomplete-class-recovered-v1','experimental-incomplete-groups-v1','experimental-incomplete-problems-v1')")
    store.db.close()
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        assert current(store) is not None
    finally:store.db.close()


def test_fallback_diagnostics_use_pending_status_not_legacy_full(tmp_path,postgres_url):
    from oko.storage.incomplete_index import save_class
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        seed(store,'done')
        with transaction(store.db):
            raw=store.db.execute("SELECT content FROM publications WHERE id='p'").fetchone()[0]
            content=raw if isinstance(raw,dict) else json.loads(raw)
            content['extra']['_reader']['full']=True
            store.db.execute("UPDATE publications SET content=%s WHERE id='p'",(json.dumps(content),))
            save_class(store.db,'p',content)
            store.db.execute("DELETE FROM state WHERE key='experimental-incomplete-problems-v1'")
        assert any(p['code']=='material-incomplete' for p in store.problems.candidates(store.wall_clock()))
    finally:store.db.close()
