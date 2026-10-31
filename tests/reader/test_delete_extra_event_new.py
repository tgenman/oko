from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.storage.job_types import Job


def test_retry_unexpected_delete_failure_persists_before_get(tmp_path,postgres_url):
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('delete','p','running',1) RETURNING id").fetchone()[0]
            s.jobs.retry(Job(job,'delete','p',1),100,'unexpected',code='delete:RuntimeError')
        row=s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'deletion_job_id'=%s",(str(job),)).fetchone()
        assert row
        assert any(p['code']=='delete:RuntimeError' for p in s.problems.snapshot(100)['current'])
    finally:s.db.close()


def test_finish_closes_extra_delete_occurrence_and_rollback(tmp_path,postgres_url):
    import pytest
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,generation,error,problem_code) VALUES ('delete','p','running',1,'bad','delete:RuntimeError') RETURNING id").fetchone()[0]
            s.problems.sync_deletion(job,[],100)
            old=s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'deletion_job_id'=%s",(str(job),)).fetchone()[0]
        with pytest.raises(RuntimeError,match='rollback'):
            with transaction(s.db):
                s.jobs.finish(Job(job,'delete','p',1))
                assert s.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()[0] is not None
                raise RuntimeError('rollback')
        assert s.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()[0] is None
        with transaction(s.db):s.jobs.finish(Job(job,'delete','p',1))
        assert s.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()[0] is not None
        assert not s.db.execute('SELECT deletion_extra_owned FROM jobs WHERE id=%s',(job,)).fetchone()[0]
    finally:s.db.close()


def test_ambiguous_extra_delete_legacy_is_not_rewritten(tmp_path,postgres_url):
    import json
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('delete','p','failed','failure','delete:RuntimeError') RETURNING id").fetchone()[0]
            canonical='job:delete:RuntimeError:p:delete:p'
            body=json.dumps(dict(code='delete:RuntimeError',section='object',title='old',consequence='saved',next_step='retry',objects=[],action=None,operation='producer:delete',state='current',next_at=None,deletion_job_id=str(job),deletion_job_identity=canonical))
            for n in (1,2):s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES (%s,%s,'background',%s,%s)",(f'old-{n}',f'job:delete-isolated:{n}',body,n))
            s.problems.sync_deletion(job,[],100)
        rows=s.db.execute("SELECT id,body,finished FROM reader_problems WHERE id LIKE 'old-%%' ORDER BY id").fetchall()
        assert len(rows)==2 and all(r['body']==body and r['finished'] is None for r in rows)
        assert not s.db.execute('SELECT deletion_extra_owned FROM jobs WHERE id=%s',(job,)).fetchone()[0]
    finally:s.db.close()


def test_extra_delete_preserves_foreign_canonical_record(tmp_path,postgres_url):
    import json
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        foreign=json.dumps(dict(code='other',operation='producer:status',objects=[dict(kind='material',id='x')],state='current'))
        with transaction(s.db):
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('delete','p','failed','failure','delete:RuntimeError') RETURNING id").fetchone()[0]
            s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign','job:delete:RuntimeError:p:delete:p','background',%s,1)",(foreign,))
            s.problems.sync_deletion(job,[],100)
        s.problems.snapshot(100)
        assert tuple(s.db.execute("SELECT body,finished FROM reader_problems WHERE id='foreign'").fetchone())==(foreign,None)
        assert s.db.execute("SELECT 1 FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'deletion_job_id'=%s",(str(job),)).fetchone()
    finally:s.db.close()


def test_recovery_preserves_active_legacy_id(tmp_path,postgres_url):
    import json
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("DELETE FROM state WHERE key='deletion-extra-recovered-v1'")
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('delete','p','failed','failure','delete:RuntimeError') RETURNING id").fetchone()[0]
            body=json.dumps(dict(code='delete:RuntimeError',section='object',title='old',consequence='saved',next_step='retry',objects=[],action=None,operation='producer:status',state='current',next_at=None))
            s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:delete:RuntimeError:p:delete:p','background',%s,1)",(body,))
        s.problems.recover_deletion_extra_problems()
        row=s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'deletion_job_id'=%s",(str(job),)).fetchone()
        assert row and row[0]=='old'
    finally:s.db.close()


def test_shared_roots_removing_one_feed_closes_only_old_owner(tmp_path,postgres_url):
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('x','x','X','default','rss','rss','connected',ARRAY[]::text[]),('y','y','Y','default','rss','rss','connected',ARRAY[]::text[])")
            s.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('a','rss','a'),('b','rss','b'),('p','other_web','p')")
            s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('x','a',true),('y','b',true)")
            s.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('a','p'),('b','p')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('delete','p','failed','failure','delete:RuntimeError') RETURNING id").fetchone()[0]
            s.problems.sync_deletion(job,[],100)
        before={r['identity']:r['id'] for r in s.db.execute("SELECT identity,id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'deletion_job_id'=%s",(str(job),))}
        assert len(before)==2
        s.remove_feed('x')
        after={r['identity']:r['id'] for r in s.db.execute("SELECT identity,id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'deletion_job_id'=%s",(str(job),))}
        assert len(after)==1 and next(iter(after.values()))==before['job:delete:RuntimeError:y:delete:p']
    finally:s.db.close()


def test_restart_updates_running_extra_delete_without_get(tmp_path,postgres_url):
    import json
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,generation,error,problem_code) VALUES ('delete','p','running',1,'failure','delete:RuntimeError') RETURNING id").fetchone()[0]
            s.problems.sync_deletion(job,[],100)
            row=s.db.execute("SELECT id,body FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'deletion_job_id'=%s",(str(job),)).fetchone()
            assert json.loads(row['body'])['state']=='retrying';old=row['id']
        s.db.close();s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:101)
        row=s.db.execute('SELECT id,body FROM reader_problems WHERE id=%s AND finished IS NULL',(old,)).fetchone()
        assert row and json.loads(row['body'])['state']=='current'
    finally:s.db.close()


def test_unproven_finished_legacy_remains_unchanged_after_recovery_and_get(tmp_path,postgres_url):
    import json
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        body=json.dumps(dict(code='delete:RuntimeError',section='object',title='legacy',consequence='saved',next_step='retry',objects=[],action=None,operation='producer:status',state='current',next_at=None))
        with transaction(s.db):
            s.db.execute("DELETE FROM state WHERE key='deletion-extra-recovered-v1'")
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('delete','p','done',NULL,NULL)")
            s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:delete:RuntimeError:p:delete:p','background',%s,1)",(body,))
        s.problems.recover_deletion_extra_problems();s.problems.snapshot(100)
        assert tuple(s.db.execute("SELECT body,finished FROM reader_problems WHERE id='old'").fetchone())==(body,None)
    finally:s.db.close()


def test_real_collection_outer_catch_persists_extra_delete_error(tmp_path,postgres_url,monkeypatch):
    import asyncio
    from tests.support.worker_library import collection_worker
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            job_id=s.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('delete','p','running',1) RETURNING id").fetchone()[0]
        worker=collection_worker(tmp_path,s,object())
        def fail_delete(_):raise OSError('synthetic delete failure')
        monkeypatch.setattr(s.jobs,'delete',fail_delete)
        retry=s.jobs.retry;calls=[]
        def fail_first(*args,**kwargs):
            calls.append(kwargs.get('code'))
            if len(calls)==1:raise RuntimeError('synthetic retry failure')
            return retry(*args,**kwargs)
        monkeypatch.setattr(s.jobs,'retry',fail_first)
        asyncio.run(worker._execute(Job(job_id,'delete','p',1)))
        assert calls==['delete','delete:RuntimeError']
        row=s.db.execute("SELECT body FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'deletion_job_id'=%s",(str(job_id),)).fetchone()
        assert row and 'delete:RuntimeError' in row['body']
    finally:s.db.close()


def test_feed_delete_extra_code_keeps_feed_owner_and_id(tmp_path,postgres_url):
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('feed-delete','f','running',1) RETURNING id").fetchone()[0]
            s.jobs.retry(Job(job,'feed-delete','f',1),100,'unexpected',code='feed-delete:RuntimeError')
        row=s.db.execute("SELECT id,body::jsonb->'objects' AS objects FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'deletion_job_id'=%s",(str(job),)).fetchone()
        assert row and row['objects']==[{'kind':'feed','id':'f'}]
        before=s.problems.snapshot(100)
        assert any(p['id']==row['id'] for p in before['current'])
        with transaction(s.db):
            claimed=s.jobs._claim(s.db.execute('SELECT * FROM jobs WHERE id=%s',(job,)).fetchone())
            assert claimed;s.jobs.finish_feed_deletion(claimed)
        assert s.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(row['id'],)).fetchone()[0] is not None
    finally:s.db.close()


def test_different_explicit_delete_code_starts_new_occurrence(tmp_path,postgres_url):
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,generation,error,problem_code) VALUES ('delete','p','running',1,'first','delete') RETURNING id").fetchone()[0]
            s.problems.sync_deletion(job,[],100)
            old=s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'deletion_job_id'=%s",(str(job),)).fetchone()[0]
        with transaction(s.db):s.jobs.retry(Job(job,'delete','p',1),101,'second',code='delete:RuntimeError')
        current=s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'deletion_job_id'=%s",(str(job),)).fetchone()
        assert current and current[0]!=old
        assert s.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()[0] is not None
    finally:s.db.close()


def test_recovery_resumes_after_page_without_changing_occurrence(tmp_path,postgres_url,monkeypatch):
    import pytest
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("DELETE FROM state WHERE key='deletion-extra-recovered-v1'")
            s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'delete','item-'||n,'failed','failure','delete:RuntimeError' FROM generate_series(1,101)n")
        old=s.fault
        def crash(point):
            if point=='deletion_extra_recovery_batch':raise RuntimeError('interrupted')
            return old(point)
        monkeypatch.setattr(s,'fault',crash)
        with pytest.raises(RuntimeError,match='interrupted'):s.problems.recover_deletion_extra_problems()
        before={r['id'] for r in s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'code'='delete:RuntimeError'")}
        assert len(before)==100
        monkeypatch.setattr(s,'fault',old);s.problems.recover_deletion_extra_problems()
        after={r['id'] for r in s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'code'='delete:RuntimeError'")}
        assert len(after)==101 and before.issubset(after)
    finally:s.db.close()


def test_special_and_source_codes_stay_outside_extra_owner(tmp_path,postgres_url):
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        codes=('source-network','processor-config','ai-permission','identity-unconfirmed','profile_unavailable','account_identity_mismatch','youtube-admission','codex-choice')
        with transaction(s.db):
            for n,code in enumerate(codes):
                job=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('delete',%s,'failed','failure',%s) RETURNING id",(f'p-{n}',code)).fetchone()[0]
                s.problems.sync_deletion(job,[],100)
                assert not s.db.execute('SELECT deletion_extra_owned FROM jobs WHERE id=%s',(job,)).fetchone()[0]
                assert not s.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'deletion_job_id'=%s",(str(job),)).fetchone()
    finally:s.db.close()


def test_087_migration_rolls_back_and_startup_recovers_extra_legacy(tmp_path,postgres_database_url):
    import json,psycopg,pytest
    from oko.storage.postgres_migrations import Migration,migrate,verify
    from oko.storage.postgres_schema import MIGRATIONS
    legacy=json.dumps(dict(code='delete:RuntimeError',section='object',title='legacy',consequence='saved',next_step='retry',objects=[],action=None,operation='producer:status',state='current',next_at=None))
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:86])
        db.execute("INSERT INTO publications(id) VALUES ('p')")
        db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('delete','p','failed','failure','delete:RuntimeError')")
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:delete:RuntimeError:p:delete:p','background',%s,1)",(legacy,))
        mig=MIGRATIONS[86];bad=Migration(87,mig.name,mig.sql+'; SELECT nonexistent FROM jobs')
        with pytest.raises(psycopg.errors.UndefinedColumn):migrate(db,(*MIGRATIONS[:86],bad))
        assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==86
        assert db.execute("SELECT body FROM reader_problems WHERE id='old'").fetchone()[0]==legacy
        migrate(db,MIGRATIONS);verify(db,MIGRATIONS)
    s=Store(tmp_path,lambda _:None,database_url=postgres_database_url,wall_clock=lambda:100)
    try:
        row=s.db.execute("SELECT id,body FROM reader_problems WHERE identity='job:delete:RuntimeError:p:delete:p' AND finished IS NULL").fetchone()
        assert row and row['id']=='old' and json.loads(row['body'])['operation']=='producer:delete'
    finally:s.db.close()


def test_protected_legacy_is_not_adopted_on_active_recovery(tmp_path,postgres_url):
    import hashlib,json
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        ident='job:delete:RuntimeError:p:delete:p'
        body=json.dumps(dict(code='delete:RuntimeError',section='object',title='legacy',consequence='saved',next_step='retry',objects=[],action=None,operation='producer:status',state='current',next_at=None))
        with transaction(s.db):
            s.db.execute("DELETE FROM state WHERE key='deletion-extra-recovered-v1'")
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('delete','p','failed','failure','delete:RuntimeError') RETURNING id").fetchone()[0]
            s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old',%s,'background',%s,1)",(ident,body))
            key='fetch-null-protected:'+hashlib.sha256(b'old').hexdigest()
            s.db.execute('INSERT INTO state(key,value) VALUES (%s,%s)',(key,json.dumps(['oko-fetch-null-v2','old',ident])))
        s.problems.recover_deletion_extra_problems()
        assert tuple(s.db.execute("SELECT body,finished FROM reader_problems WHERE id='old'").fetchone())==(body,None)
        assert s.db.execute("SELECT 1 FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'deletion_job_id'=%s",(str(job),)).fetchone()
    finally:s.db.close()


def test_corrupt_marker_prevents_foreign_overwrite_and_rolls_back(tmp_path,postgres_url):
    import hashlib,json,pytest
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        body=json.dumps(dict(code='foreign',operation='producer:status',objects=[],state='current'))
        key='fetch-null-protected:'+hashlib.sha256(b'old').hexdigest()
        with transaction(s.db):
            s.db.execute("DELETE FROM state WHERE key='deletion-extra-recovered-v1'")
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('delete','p','failed','failure','delete:RuntimeError') RETURNING id").fetchone()[0]
            s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:delete:RuntimeError:p:delete:p','background',%s,1)",(body,))
            s.db.execute('INSERT INTO state(key,value) VALUES (%s,%s)',(key,'foreign'))
        with pytest.raises(RuntimeError,match='Conflicting'):s.problems.recover_deletion_extra_problems()
        assert tuple(s.db.execute("SELECT body,finished FROM reader_problems WHERE id='old'").fetchone())==(body,None)
        assert not s.db.execute('SELECT deletion_extra_owned FROM jobs WHERE id=%s',(job,)).fetchone()[0]
    finally:s.db.close()


def test_recovery_preserves_isolated_status_legacy_id(tmp_path,postgres_url):
    import json
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        canonical='job:delete:RuntimeError:p:delete:p'
        body=json.dumps(dict(code='delete:RuntimeError',section='object',title='legacy',consequence='saved',next_step='retry',objects=[],action=None,operation='producer:delete',state='current',next_at=None,status_job_identity=canonical,status_job_target='p'))
        with transaction(s.db):
            s.db.execute("DELETE FROM state WHERE key='deletion-extra-recovered-v1'")
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('delete','p','failed','failure','delete:RuntimeError') RETURNING id").fetchone()[0]
            s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:status-isolated:old','background',%s,1)",(body,))
        s.problems.recover_deletion_extra_problems()
        row=s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'deletion_job_id'=%s",(str(job),)).fetchone()
        assert row and row[0]=='old'
    finally:s.db.close()


def test_graph_removal_closes_isolated_owned_occurrence(tmp_path,postgres_url):
    import json
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('x','x','X','default','rss','rss','connected',ARRAY[]::text[]),('y','y','Y','default','rss','rss','connected',ARRAY[]::text[])")
            s.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('a','rss','a'),('b','rss','b'),('p','other_web','p')")
            s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('x','a',true),('y','b',true)")
            s.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('a','p'),('b','p')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('delete','p','failed','failure','delete:RuntimeError') RETURNING id").fetchone()[0]
            foreign=json.dumps(dict(code='other',operation='producer:status',objects=[],state='current'))
            s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign','job:delete:RuntimeError:x:delete:p','background',%s,1)",(foreign,))
            s.problems.sync_deletion(job,[],100)
        isolated=s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'deletion_job_identity'='job:delete:RuntimeError:x:delete:p'").fetchone()
        assert isolated
        s.remove_feed('x')
        assert s.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(isolated[0],)).fetchone()[0] is not None
        assert tuple(s.db.execute("SELECT body,finished FROM reader_problems WHERE id='foreign'").fetchone())==(foreign,None)
    finally:s.db.close()
