import pytest
from oko.storage.transaction import transaction
from tests.support.storage import Store
from tests.support.postgres_work import scan_rows


def test_refresh_targets_only_changed_jobs_and_rolls_back(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'read','other-'||n,'failed','failure','other' FROM generate_series(1,10000) n")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('read','target','failed','failure','processor-config') RETURNING id").fetchone()[0]
            store.db._db.execute('ANALYZE jobs')
        assert not store.db.execute('SELECT processor_visible FROM jobs WHERE id=%s',(job,)).fetchone()[0]
        with pytest.raises(RuntimeError):
            with transaction(store.db):
                store.problems.refresh_processor_visibility([job])
                assert store.db.execute('SELECT processor_visible FROM jobs WHERE id=%s',(job,)).fetchone()[0]
                raise RuntimeError('rollback')
        assert not store.db.execute('SELECT processor_visible FROM jobs WHERE id=%s',(job,)).fetchone()[0]
        with transaction(store.db):assert store.problems.refresh_processor_visibility([job])
        before=store.db.execute('SELECT xmin::text FROM jobs WHERE id=%s',(job,)).fetchone()[0]
        with transaction(store.db):assert not store.problems.refresh_processor_visibility([job])
        assert store.db.execute('SELECT xmin::text FROM jobs WHERE id=%s',(job,)).fetchone()[0]==before
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) SELECT 1 FROM jobs WHERE processor_visible AND NOT processor_caption LIMIT 1').fetchone()[0][0]['Plan']
        print('derived-visible one target + 10000 foreign: rows=%s buffers=%s'%(scan_rows(plan),plan.get('Shared Hit Blocks',0)))
        assert scan_rows(plan)<100
    finally:store.db.close()


def test_viewed_and_missing_publications_follow_existing_filter(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('seen','rss','seen',1)")
            rows=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('filter','seen','failed','x','processor-config'),('read','missing','pending','x','processor-config'),('fetch','gone','done','x','processor-config'),('theses','done','done','x','processor-config') RETURNING id").fetchall()
            store.problems.refresh_processor_visibility([r[0] for r in rows])
        assert [r[0] for r in store.db.execute('SELECT processor_visible FROM jobs ORDER BY id')]==[False,True,True,False]
    finally:store.db.close()


def test_visible_migration_backfills_and_rolls_back(postgres_database_url):
    import psycopg
    from oko.storage.postgres_migrations import Migration,migrate,verify
    from oko.storage.postgres_schema import MIGRATIONS
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:70])
        db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('seen','rss','seen',1)")
        db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('filter','seen','failed','x','processor-config'),('read','live','failed','x','processor-config'),('fetch','foreign','failed','x','other')")
        before=db.execute('SELECT id,kind,item,status,error,problem_code FROM jobs ORDER BY id').fetchall()
        failing=Migration(71,MIGRATIONS[70].name,MIGRATIONS[70].sql+'; SELECT missing_column FROM jobs')
        with pytest.raises(psycopg.errors.UndefinedColumn):migrate(db,(*MIGRATIONS[:70],failing))
        assert db.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='jobs' AND column_name='processor_visible'").fetchone()[0]==0
        assert db.execute('SELECT id,kind,item,status,error,problem_code FROM jobs ORDER BY id').fetchall()==before
        assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==70
        migrate(db,MIGRATIONS)
        verify(db,MIGRATIONS)
        assert db.execute('SELECT id,kind,item,status,error,problem_code FROM jobs ORDER BY id').fetchall()==before
        assert db.execute('SELECT processor_visible FROM jobs ORDER BY id').fetchall()==[(False,),(True,),(False,)]


def test_visible_index_does_not_scan_10000_hidden_processor_jobs(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) SELECT 'p-'||n,'rss','p-'||n,1 FROM generate_series(1,10000) n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'filter','p-'||n,'failed','failure','processor-config' FROM generate_series(1,10000) n")
            row=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,processor_visible) VALUES ('read','visible','failed','x','processor-config',true) RETURNING id").fetchone()
            store.db._db.execute('ANALYZE jobs')
        query='SELECT 1 FROM jobs WHERE processor_visible AND NOT processor_caption AND NOT (id=ANY(%s)) LIMIT 1'
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,([row[0]],)).fetchone()[0][0]['Plan']
        print('visible-index hidden=10000 scanned=%s buffers=%s'%(scan_rows(plan),plan.get('Shared Hit Blocks',0)))
        assert scan_rows(plan)<100
    finally:store.db.close()


def test_full_projection_ignores_10000_invisible_jobs(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'filter','p-'||n,'failed','failure','processor-config' FROM generate_series(1,10000) n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,processor_visible) VALUES ('read','visible','failed','x','processor-config',true)")
            store.db._db.execute('ANALYZE jobs')
        queries=[];store.db.set_trace_callback(queries.append)
        try:result=store.problems.processor_configuration_candidate(1)
        finally:store.db.set_trace_callback(None)
        query=next(q for q in queries if 'WHERE processor_visible ORDER BY' in q)
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query).fetchone()[0][0]['Plan']
        assert result and len(result['objects'])==1
        assert scan_rows(plan)<100,plan
    finally:store.db.close()


def test_refresh_unrelated_caption_job_is_noop(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('p','youtube','p',0)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','synthetic','captions','pending')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m','failed','x','other') RETURNING id").fetchone()[0]
        before=store.db.execute('SELECT xmin::text FROM jobs WHERE id=%s',(job,)).fetchone()[0]
        with transaction(store.db):assert not store.problems.refresh_processor_visibility([job])
        assert store.db.execute('SELECT xmin::text FROM jobs WHERE id=%s',(job,)).fetchone()[0]==before
    finally:store.db.close()


def test_cleanup_processor_lookup_ignores_unrelated_jobs(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'fetch','other-'||n,'failed','x','other' FROM generate_series(1,10000) n")
            store.db._db.execute('ANALYZE jobs')
        sql=[];store.db.set_trace_callback(sql.append)
        try:
            with transaction(store.db):store.jobs.reconcile_cleanup()
        finally:store.db.set_trace_callback(None)
        query=next(q for q in sql if 'SELECT j.id FROM jobs j WHERE (j.processor_visible' in q)
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000,plan
    finally:store.db.close()


def test_finishing_one_visible_job_ignores_10000_hidden_jobs(tmp_path,postgres_url):
    from oko.storage.job_types import Job
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) SELECT 'p-'||n,'rss','p-'||n,1 FROM generate_series(1,10000) n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'filter','p-'||n,'failed','failure','processor-config' FROM generate_series(1,10000) n")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,error,problem_code,processor_visible) VALUES ('read','visible','running',1,'x','processor-config',true) RETURNING id").fetchone()[0]
            store.problems.finish_processor_change([],None,1)
            store.db._db.execute('ANALYZE jobs')
        statements=[];store.db.set_trace_callback(statements.append)
        try:
            with transaction(store.db):store.jobs.finish(Job(job,'read','visible',1))
        finally:store.db.set_trace_callback(None)
        selects=[q for q in statements if 'WHERE processor_visible ORDER BY' in q]
        assert len(selects)==1
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+selects[0]).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<100,plan
    finally:store.db.close()


def test_valid_processor_lookup_ignores_10000_invalid_legacy(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign','processor:configuration','background','{}',1)")
            malformed=json.dumps(dict(code='processor-config',processor_group_identity='processor:configuration',objects=[None]))
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'bad-'||n,'processor:configuration-isolated:bad-'||n,'background',%s,1 FROM generate_series(1,10000) n",(malformed,))
            valid=dict(id='processor:configuration-isolated:valid',code='processor-config',processor_group_identity='processor:configuration',section='action',title='valid',consequence='c',next_step='n',objects=[],action=None,operation='producer:read',state='current',next_at=None)
            store.problems.sync_candidate(valid['id'],valid,2)
            store.db._db.execute('ANALYZE reader_problems')
        sql=[];store.db.set_trace_callback(sql.append)
        try:assert store.problems.processor_current()['identity']==valid['id']
        finally:store.db.set_trace_callback(None)
        query=next(q for q in sql if "body::jsonb->>'processor_group_identity'='processor:configuration'" in q)
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000,plan
    finally:store.db.close()


def test_processor_material_lookup_ignores_10000_foreign_jobs(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'fetch','other-'||n,'failed','failure','other' FROM generate_series(1,10000) n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,processor_visible) VALUES ('filter','target','failed','x','processor-config',true)")
            store.db._db.execute('ANALYZE jobs')
        sql=[];store.db.set_trace_callback(sql.append)
        try:assert len(store.problems.processor_jobs_for_materials(['target']))==1
        finally:store.db.set_trace_callback(None)
        query=next(q for q in sql if 'SELECT j.id FROM jobs j WHERE j.item=ANY' in q)
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,(['target'],['target'])).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000,plan
    finally:store.db.close()
