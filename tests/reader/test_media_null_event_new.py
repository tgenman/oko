from tests.support.storage import Store
from oko.storage.transaction import transaction


def test_null_media_error_saved_before_get(tmp_path,postgres_url):
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('download_media','m','failed','failure') RETURNING id").fetchone()[0]
            s.problems.sync_media_problem(job,[],100)
        assert s.db.execute("SELECT 1 FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()
    finally:s.db.close()


def test_recovery_reuses_legacy_occurrence_and_get_does_not_change_it(tmp_path,postgres_url):
    import json
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("DELETE FROM state WHERE key='media-null-problems-recovered-v1'")
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('download_media','m','failed','failure') RETURNING id").fetchone()[0]
            body=dict(code='work-download_media',operation='producer:status',objects=[dict(kind='material',id='p')])
            s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:work-download_media:m:download_media:m','background',%s,1)",(json.dumps(body),))
        s.problems.recover_media_null_problems()
        row=s.db.execute("SELECT id,body,xmin::text FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()
        assert row and row['id']=='old'
        s.problems.snapshot(100)
        assert tuple(row)==tuple(s.db.execute('SELECT id,body,xmin::text FROM reader_problems WHERE id=%s',(row['id'],)).fetchone())
    finally:s.db.close()


def test_recovery_does_not_take_subtitles(tmp_path,postgres_url):
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("DELETE FROM state WHERE key='media-null-problems-recovered-v1'")
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('download_media','m','failed','failure') RETURNING id").fetchone()[0]
        s.problems.recover_media_null_problems()
        assert not s.db.execute('SELECT media_null_owned FROM jobs WHERE id=%s',(job,)).fetchone()[0]
        assert not s.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()
    finally:s.db.close()


def test_null_to_explicit_work_code_keeps_occurrence(tmp_path,postgres_url):
    from oko.storage.media_job_problems import previous_candidates
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('download_media','m','failed','failure') RETURNING id").fetchone()[0]
            s.problems.sync_media_problem(job,[],100)
            old=s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()[0]
        with transaction(s.db):
            previous=previous_candidates(s,job,101)
            s.db.execute("UPDATE jobs SET problem_code='work-download_media' WHERE id=%s",(job,))
            s.problems.sync_media_problem(job,previous,101)
        row=s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()
        assert row and row[0]==old
        assert not s.db.execute('SELECT media_null_owned FROM jobs WHERE id=%s',(job,)).fetchone()[0]
    finally:s.db.close()


def test_finish_closes_null_media_error_and_rollback_restores_it(tmp_path,postgres_url):
    import pytest
    from oko.storage.job_types import Job
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,generation,error) VALUES ('download_media','m','running',1,'failure') RETURNING id").fetchone()[0]
            s.problems.sync_media_problem(job,[],100)
            old=s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()[0]
        with pytest.raises(RuntimeError,match='rollback'):
            with transaction(s.db):
                s.jobs.finish(Job(job,'download_media','m',1))
                assert s.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()[0] is not None
                raise RuntimeError('rollback')
        assert s.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()[0] is None
        with transaction(s.db):s.jobs.finish(Job(job,'download_media','m',1))
        assert s.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()[0] is not None
    finally:s.db.close()


def test_recovery_preserves_foreign_canonical_body(tmp_path,postgres_url):
    import json
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        foreign=json.dumps(dict(code='other',operation='producer:delete',objects=[dict(kind='material',id='x')],state='current'))
        with transaction(s.db):
            s.db.execute("DELETE FROM state WHERE key='media-null-problems-recovered-v1'")
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('download_media','m','failed','failure') RETURNING id").fetchone()[0]
            s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign','job:work-download_media:m:download_media:m','background',%s,1)",(foreign,))
        s.problems.recover_media_null_problems();s.problems.snapshot(100)
        saved=s.db.execute("SELECT body,finished FROM reader_problems WHERE id='foreign'").fetchone()
        assert saved['body']==foreign and saved['finished'] is None
        assert s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()
    finally:s.db.close()


def test_null_media_preserves_legacy_item_collision_projection(tmp_path,postgres_url):
    from oko.storage.media_job_problems import managed_candidates
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
            s.db.execute("INSERT INTO publications(id) VALUES ('root'),('m'),('owner')")
            s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','root',true)")
            s.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root','m')")
            s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','owner','image','x','f','pending_download')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('download_media','m','failed','failure') RETURNING id").fetchone()[0]
        old,_,_=s.problems.incomplete_generic_groups(100)
        expected=[(g['id'],g['objects']) for g in old.values() if g['code']=='work-download_media']
        projected=managed_candidates(s,job,100)
        assert [(p['id'],p['objects']) for p in projected]==expected
    finally:s.db.close()


def test_null_to_explicit_code_preserves_unknown_cause_occurrence(tmp_path,postgres_url):
    from oko.storage.media_job_problems import previous_candidates
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('download_media','m','failed','failure') RETURNING id").fetchone()[0]
            s.problems.sync_media_problem(job,[],100)
            old=s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()[0]
        with transaction(s.db):
            previous=previous_candidates(s,job,101)
            s.db.execute("UPDATE jobs SET problem_code='media-unavailable' WHERE id=%s",(job,))
            s.problems.sync_media_problem(job,previous,101)
        row=s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()
        assert row and row[0]==old
        assert s.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()[0] is None
    finally:s.db.close()


def test_owned_media_null_status_get_is_read_only(tmp_path,postgres_url):
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('download_media','m','failed','failure') RETURNING id").fetchone()[0]
            s.problems.sync_media_problem(job,[],100)
        before=[tuple(row) for row in s.db.execute('SELECT id,identity,body,finished,xmin::text FROM reader_problems ORDER BY id')]
        trace=[];s.db.set_trace_callback(trace.append)
        try:
            first=s.problems.snapshot(101);second=s.problems.snapshot(101)
        finally:s.db.set_trace_callback(None)
        after=[tuple(row) for row in s.db.execute('SELECT id,identity,body,finished,xmin::text FROM reader_problems ORDER BY id')]
        assert before==after
        assert [p['id'] for p in first['current'] if p['code']=='work-download_media']==[p['id'] for p in second['current'] if p['code']=='work-download_media']
        assert not any(q.lstrip().upper().startswith(('UPDATE ','INSERT ','DELETE ')) for q in trace)
    finally:s.db.close()


def test_reclassification_to_subtitle_keeps_unambiguous_occurrence(tmp_path,postgres_url):
    from oko.storage.media_job_problems import previous_candidates
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('download_media','m','failed','failure') RETURNING id").fetchone()[0]
            s.problems.sync_media_problem(job,[],100)
            old=s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()[0]
        with transaction(s.db):
            previous=previous_candidates(s,job,101)
            s.db.execute("UPDATE media_files SET resource_key='youtube:subtitles' WHERE id='m'")
            s.problems.sync_media_problem(job,previous,101)
        row=s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()
        assert row and row[0]==old
        flags=s.db.execute('SELECT media_null_owned,subtitle_event_owned FROM jobs WHERE id=%s',(job,)).fetchone()
        assert not flags['media_null_owned'] and flags['subtitle_event_owned']
    finally:s.db.close()


def test_recovery_resumes_after_committed_page(tmp_path,postgres_url,monkeypatch):
    import pytest
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("DELETE FROM state WHERE key='media-null-problems-recovered-v1'")
            s.db.execute("INSERT INTO jobs(kind,item,status,error) SELECT 'download_media','orphan-'||n,'failed','failure' FROM generate_series(1,101)n")
        old=s.fault
        def crash(point):
            if point=='media_null_problems_recovery_batch':raise RuntimeError('interrupted')
            return old(point)
        monkeypatch.setattr(s,'fault',crash)
        with pytest.raises(RuntimeError,match='interrupted'):s.problems.recover_media_null_problems()
        before={r['id'] for r in s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_null_event'='true'")}
        assert len(before)==100
        monkeypatch.setattr(s,'fault',old);s.problems.recover_media_null_problems()
        after={r['id'] for r in s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_null_event'='true'")}
        assert len(after)==101 and before.issubset(after)
    finally:s.db.close()


def test_corrupt_foreign_protection_marker_rolls_back_recovery(tmp_path,postgres_url):
    import hashlib,json,pytest
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        foreign=json.dumps(dict(code='other',operation='producer:delete',objects=[dict(kind='material',id='x')],state='current'))
        key='fetch-null-protected:'+hashlib.sha256(b'foreign').hexdigest()
        with transaction(s.db):
            s.db.execute("DELETE FROM state WHERE key='media-null-problems-recovered-v1'")
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('download_media','m','failed','failure') RETURNING id").fetchone()[0]
            s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign','job:work-download_media:m:download_media:m','background',%s,1)",(foreign,))
            s.db.execute('INSERT INTO state(key,value) VALUES (%s,%s)',(key,'foreign'))
        with pytest.raises(RuntimeError,match='Conflicting'):
            s.problems.recover_media_null_problems()
        assert not s.db.execute('SELECT media_null_owned FROM jobs WHERE id=%s',(job,)).fetchone()[0]
        assert tuple(s.db.execute("SELECT body,finished FROM reader_problems WHERE id='foreign'").fetchone())==(foreign,None)
        assert s.db.execute('SELECT value FROM state WHERE key=%s',(key,)).fetchone()[0]=='foreign'
    finally:s.db.close()


def test_ambiguous_legacy_occurrences_are_not_rewritten(tmp_path,postgres_url):
    import json
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("DELETE FROM state WHERE key='media-null-problems-recovered-v1'")
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('download_media','m','failed','failure') RETURNING id").fetchone()[0]
            canonical='job:work-download_media:m:download_media:m'
            body=json.dumps(dict(code='work-download_media',operation='producer:download_media',media_file_id='m',media_job_id=str(job),media_job_identity=canonical,objects=[dict(kind='material',id='p')]))
            for n in (1,2):s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES (%s,%s,'background',%s,%s)",(f'legacy-{n}',f'job:media-isolated:{n}',body,n))
        s.problems.recover_media_null_problems()
        rows=s.db.execute("SELECT id,body,finished FROM reader_problems WHERE id LIKE 'legacy-%%' ORDER BY id").fetchall()
        assert len(rows)==2 and all(r['body']==body and r['finished'] is None for r in rows)
        assert not s.db.execute('SELECT media_null_owned FROM jobs WHERE id=%s',(job,)).fetchone()[0]
    finally:s.db.close()


def test_086_migration_rolls_back_and_startup_recovers_null(tmp_path,postgres_database_url):
    import json,psycopg,pytest
    from oko.storage.postgres_migrations import Migration,migrate,verify
    from oko.storage.postgres_schema import MIGRATIONS
    legacy=json.dumps(dict(code='work-download_media',operation='producer:status',objects=[dict(kind='material',id='p')]))
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:85])
        db.execute("INSERT INTO publications(id) VALUES ('p')")
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
        db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('download_media','m','failed','failure')")
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:work-download_media:m:download_media:m','background',%s,1)",(legacy,))
        mig=MIGRATIONS[85];bad=Migration(86,mig.name,mig.sql+'; SELECT nonexistent FROM jobs')
        with pytest.raises(psycopg.errors.UndefinedColumn):migrate(db,(*MIGRATIONS[:85],bad))
        assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==85
        assert db.execute("SELECT body FROM reader_problems WHERE id='old'").fetchone()[0]==legacy
        migrate(db,MIGRATIONS);verify(db,MIGRATIONS)
    s=Store(tmp_path,lambda _:None,database_url=postgres_database_url,wall_clock=lambda:100)
    try:
        row=s.db.execute("SELECT id,body FROM reader_problems WHERE identity='job:work-download_media:m:download_media:m' AND finished IS NULL").fetchone()
        assert row and row['id']=='old' and json.loads(row['body'])['operation']=='producer:download_media'
    finally:s.db.close()


def test_reclassification_from_subtitle_clears_subtitle_owner(tmp_path,postgres_url):
    from oko.storage.media_job_problems import previous_candidates
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('download_media','m','failed','failure') RETURNING id").fetchone()[0]
            s.problems.sync_media_problem(job,[],100)
            old=s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()[0]
        with transaction(s.db):
            previous=previous_candidates(s,job,101)
            s.db.execute("UPDATE media_files SET resource_key='image' WHERE id='m'")
            s.problems.sync_media_problem(job,previous,101)
        row=s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()
        assert row and row[0]==old
        flags=s.db.execute('SELECT media_null_owned,subtitle_event_owned FROM jobs WHERE id=%s',(job,)).fetchone()
        assert flags['media_null_owned'] and not flags['subtitle_event_owned']
    finally:s.db.close()


def test_finished_legacy_media_error_does_not_require_get_to_close(tmp_path,postgres_url):
    import json
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("DELETE FROM state WHERE key='media-null-problems-recovered-v1'")
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','ready')")
            s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m','done',NULL,NULL)")
            body=json.dumps(dict(code='work-download_media',operation='producer:status',objects=[dict(kind='material',id='p')]))
            s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:work-download_media:m:download_media:m','background',%s,1)",(body,))
        s.problems.recover_media_null_problems()
        assert s.db.execute("SELECT finished FROM reader_problems WHERE id='old'").fetchone()[0] is not None
    finally:s.db.close()


def test_finished_legacy_protected_or_foreign_body_remains_untouched(tmp_path,postgres_url):
    import hashlib,json
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("DELETE FROM state WHERE key='media-null-problems-recovered-v1'")
            s.db.execute("INSERT INTO publications(id) VALUES ('p'),('q')")
            s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','ready'),('n','q','image','x','f','ready')")
            s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m','done',NULL,NULL),('download_media','n','done',NULL,NULL)")
            protected=json.dumps(dict(code='work-download_media',operation='producer:status',objects=[dict(kind='material',id='p')]))
            foreign=json.dumps(dict(code='work-download_media',operation='producer:status',objects=[dict(kind='material',id='q')],foreign_evidence='keep'))
            s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('protected','job:work-download_media:m:download_media:m','background',%s,1),('foreign','job:work-download_media:n:download_media:n','background',%s,1)",(protected,foreign))
            key='fetch-null-protected:'+hashlib.sha256(b'protected').hexdigest();s.db.execute('INSERT INTO state(key,value) VALUES (%s,%s)',(key,'corrupt'))
        s.problems.recover_media_null_problems()
        rows=s.db.execute("SELECT id,body,finished FROM reader_problems WHERE id IN ('protected','foreign') ORDER BY id").fetchall()
        assert [(r['id'],r['body'],r['finished']) for r in rows]==[('foreign',foreign,None),('protected',protected,None)]
    finally:s.db.close()


def test_legacy_with_unknown_fields_is_not_rewritten(tmp_path,postgres_url):
    import json
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("DELETE FROM state WHERE key='media-null-problems-recovered-v1'")
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('download_media','m','failed','failure') RETURNING id").fetchone()[0]
            foreign=json.dumps(dict(code='work-download_media',operation='producer:status',objects=[dict(kind='material',id='p')],foreign_evidence='keep'))
            s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign','job:work-download_media:m:download_media:m','background',%s,1)",(foreign,))
        s.problems.recover_media_null_problems()
        assert tuple(s.db.execute("SELECT body,finished FROM reader_problems WHERE id='foreign'").fetchone())==(foreign,None)
        assert s.db.execute("SELECT 1 FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()
    finally:s.db.close()


def test_terminal_legacy_compare_and_swap_preserves_concurrent_body(tmp_path,postgres_url,monkeypatch):
    import json,psycopg
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    other=psycopg.connect(postgres_url,autocommit=True)
    try:
        with transaction(s.db):
            s.db.execute("DELETE FROM state WHERE key='media-null-problems-recovered-v1'")
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','ready')")
            s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m','done',NULL,NULL)")
            body=json.dumps(dict(code='work-download_media',operation='producer:status',objects=[dict(kind='material',id='p')]))
            s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:work-download_media:m:download_media:m','background',%s,1)",(body,))
        original=s.problems.media_record_matches
        foreign=json.dumps(dict(code='work-download_media',operation='producer:delete',objects=[dict(kind='material',id='p')]))
        changed=[]
        def race(record,candidate):
            result=original(record,candidate)
            if result and not changed:
                other.execute("UPDATE reader_problems SET body=%s WHERE id='old'",(foreign,));changed.append(True)
            return result
        monkeypatch.setattr(s.problems,'media_record_matches',race)
        s.problems.recover_media_null_problems()
        row=s.db.execute("SELECT body,finished FROM reader_problems WHERE id='old'").fetchone()
        assert changed and row['body']==foreign and row['finished'] is None
    finally:other.close();s.db.close()
