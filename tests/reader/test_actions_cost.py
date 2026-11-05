"""Scoped actions and status keep bounded work through the reader HTTP interface."""
from fastapi.testclient import TestClient
import pytest

from tests.support.reading_cost import traced_app, tracing
from tests.support.reader import HEADERS


def test_failed_command_problem_is_saved_before_status_read(tmp_path):
    app=traced_app(tmp_path)
    with TestClient(app,headers=HEADERS) as client:
        tracing(client,app)
        response=client.post('/api/v3/command-observations',json={'id':'synthetic-failed-setting','context':{'operation':'settings_patch','target':{'scope':'oko','owner':'global'},'keys':['theses']},'result':'failed'})
        assert response.status_code==200,response.text
        status=client.get('/api/v3/status/details')
        assert status.status_code==200
        problem=next(p for p in status.json()['problems']['current'] if p['code']=='command_failed')
        assert not any(q.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) for q in app.state.last_sql)
        assert client.patch('/api/v3/settings/oko/global',json={'theses':False}).status_code==200
        status=client.get('/api/v3/status/details')
        assert not any(p['code']=='command_failed' for p in status.json()['problems']['current'])
        assert any(p['id']==problem['id'] and p['outcome']=='resolved' for p in status.json()['problems']['recent'])
        assert not any(q.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) for q in app.state.last_sql)
    restarted=traced_app(tmp_path)
    with TestClient(restarted,headers=HEADERS) as client:
        tracing(client,restarted)
        status=client.get('/api/v3/status/details')
        assert any(p['id']==problem['id'] and p['outcome']=='resolved' for p in status.json()['problems']['recent'])
        assert not any(q.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) for q in restarted.state.last_sql)


def test_command_problem_and_outcome_roll_back_together(tmp_path):
    from oko.storage.transaction import transaction
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None)
    context=dict(identity='synthetic-operation',title='Synthetic failure',objects=[],action=None,discardable=True,
                 command_context={'operation':'collect','target':{'scope':'all'}})
    try:
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                assert store.problems.report_action('synthetic-failed',context,'failed',store.wall_clock())=='failed'
                assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='action:synthetic-operation' AND finished IS NULL").fetchone()
                raise ValueError('rollback')
        assert store.db.execute("SELECT 1 FROM reader_action_results WHERE id='synthetic-failed'").fetchone() is None
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='action:synthetic-operation'").fetchone() is None
        store.problems.action_started('synthetic-pending',context,store.wall_clock())
        store.db.close()
        store=Store(tmp_path,lambda _:None)
        assert 'synthetic-pending' in store.problems._startup_pending
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                store.problems.action_started('synthetic-pending',context,store.wall_clock(),restart=True)
                assert 'synthetic-pending' not in store.problems._startup_pending
                raise ValueError('rollback')
        assert 'synthetic-pending' in store.problems._startup_pending
        assert store.problems.command_record('synthetic-pending').result=='unknown'
    finally:
        store.db.close()


def test_stop_checks_pending_subtitles_in_batches(tmp_path,postgres_url):
    import json
    from tests.support.storage import Store
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            for number in range(40):
                item='p'+str(number);media='m'+str(number)
                content={'blocks':[{'type':'media','role':'subtitles','media_id':media,'source_url':None,'publication_url':None}],'extra':{'youtube':{}}}
                store.db.execute("INSERT INTO publications(id,platform,external_id,content,media_ids) VALUES (%s,'youtube',%s,%s::jsonb,%s)",(item,item,json.dumps(content),json.dumps([media])))
                store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES (%s,%s,'youtube:subtitles','opaque','captions','pending_download')",(media,item))
                store.db.execute("INSERT INTO jobs(kind,item,status,payload) VALUES ('theses',%s,'pending','{\"input_revision\":\"stale\"}')",(item,))
        queries=[]
        store.db._trace=queries.append
        with transaction(store.db):
            store.jobs.invalidate_processing()
        store.db._trace=None
        assert not any('SELECT publication_id FROM media_files WHERE id=' in query for query in queries)
    finally:
        store.db._trace=None
        store.db.close()


def test_scoped_stop_checks_only_affected_publications(tmp_path,postgres_url,monkeypatch):
    from oko.storage import processing_input
    from oko.storage.transaction import transaction
    from tests.support.storage import Store

    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    checked=[]
    actual_revision=processing_input.revision
    def record_revision(store,item):
        checked.append(item)
        return actual_revision(store,item)
    monkeypatch.setattr(processing_input,'revision',record_revision)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,platform,external_id) VALUES ('target','rss','target'),('other','rss','other')")
            for number in range(40):
                item='p'+str(number)
                feed='target' if number==0 else 'other'
                store.db.execute("INSERT INTO publications(id,platform,external_id,feed_id,content) VALUES (%s,'rss',%s,%s,%s::jsonb)",(item,item,feed,'{"blocks":[],"extra":{}}'))
                store.db.execute("INSERT INTO jobs(kind,item,status,payload) VALUES ('theses',%s,'pending','{\"input_revision\":\"stale\"}')",(item,))
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('p1','p0')")
        with transaction(store.db):
            store.jobs.invalidate_processing(set())
        assert checked==[]
        with transaction(store.db):
            store.jobs.invalidate_processing({'target'})
        assert set(checked)=={'p0','p1'}
        rows=store.db.execute("SELECT item,generation FROM jobs WHERE kind='theses' ORDER BY item").fetchall()
        assert dict((row['item'],row['generation']) for row in rows)=={f'p{n}':int(n in (0,1)) for n in range(40)}
    finally:
        store.db.close()
