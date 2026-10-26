"""Dispatch one source without decoding unrelated publication/media JSON."""
import json
from uuid import uuid4

from tests.support.storage import Store
from tests.support.postgres_work import measure_reads
from tests.support.reading_cost import build_library


def _pending_files(root, foreign):
    feeds,_=build_library(root,[10,foreign])
    store=Store(root,lambda _:None)
    with store.db:
        for row in store.db.execute('SELECT id,content FROM items').fetchall():
            content=json.loads(row['content'])
            identities=[]
            for number in range(3):
                identity=str(uuid4())
                identities.append(identity)
                content['blocks'].append(dict(type='media',role='image',media_id=identity))
                store.db.execute("INSERT INTO media_files ( id , publication_id , resource_key , source_locator , name , status ) VALUES ( %s , %s , %s , %s , %s , 'pending_download' )",(identity,row['id'],str(number),'https://cdn.test/file','file'))
                store.db.execute("INSERT INTO jobs ( kind , item ) VALUES ( 'download_media' , %s )",(identity,))
            content['extra']['_reader'].update(full=False,reason='Media is pending')
            store.db.execute('UPDATE items SET content = %s , media_ids = %s WHERE id = %s',(json.dumps(content),json.dumps(identities),row['id']))
    return store,feeds


def _steps(store, **options):
    with measure_reads(store.db) as work:
        result=store.jobs.next_job(store.clock(),**options)
    return work.rows,result


def test_selected_media_dispatch_does_not_decode_foreign_publications(tmp_path):
    samples=[]
    for foreign in (26,1500):
        store,feeds=_pending_files(tmp_path/str(foreign),foreign)
        try:
            media,job=_steps(store,feeds=[feeds[0]],media_only=True)
            assert job is not None and store.item_feed(job.item)==feeds[0]
            captions,job=_steps(store,feeds=[feeds[0]],media_only=True,captions_only=True)
            assert job is None
            common,job=_steps(store,feeds=[feeds[0]])
            assert job is not None and store.item_feed(job.item)==feeds[0]
            samples.append((media,captions,common))
        finally:
            store.db.close()
    assert samples[1][0]<=samples[0][0]+2000,samples
    assert samples[1][1]<=samples[0][1]+2000,samples
    assert samples[1][2]<=samples[0][2]+2000,samples


def test_scheduling_one_publication_does_not_scan_foreign_media_jobs(tmp_path):
    samples=[]
    for foreign in (26,400):
        store,feeds=_pending_files(tmp_path/str(foreign),foreign)
        try:
            item=store.db.execute('SELECT id FROM items WHERE feed_id = %s LIMIT 1',(feeds[0],)).fetchone()[0]
            with measure_reads(store.db) as work:
                with store.db:
                    store.jobs.schedule_media(item)
            assert work.writes < 20
            samples.append(work.rows)
            assert store.db.execute("SELECT count(*) FROM jobs WHERE kind='download_media' AND status='cancelled'").fetchone()[0]==0
        finally:
            store.db.close()
    assert samples[1]<=samples[0]+2000,samples


def test_non_youtube_subtitle_file_uses_the_common_media_worker(tmp_path):
    store,feeds=_pending_files(tmp_path,1)
    try:
        row=store.db.execute("SELECT m . id , m . publication_id FROM jobs j JOIN media_files m ON m . id = j . item JOIN items i ON i . id = m . publication_id WHERE j . kind = 'download_media' AND i . feed_id = %s ORDER BY j . id LIMIT 1",(feeds[0],)).fetchone()
        with store.db:
            item=store.db.execute('SELECT content FROM items WHERE id = %s',(row['publication_id'],)).fetchone()
            content=json.loads(item['content'])
            for block in content['blocks']:
                if block.get('media_id')==row['id']:
                    block['role']='subtitles'
            store.db.execute('UPDATE items SET content = %s WHERE id = %s',(json.dumps(content),row['publication_id']))
        job=store.jobs.next_job(store.clock(),feeds=[feeds[0]],media_only=True)
        assert job is not None and job.item==row['id']
    finally:
        store.db.close()


def test_status_of_pending_files_does_not_query_each_publication(tmp_path):
    from fastapi.testclient import TestClient
    from tests.support.reading_cost import traced_app
    from tests.support.reading_measurements import ReadingMeasurements
    from tests.support.reader import HEADERS
    costs=[]
    for foreign in (26,400):
        root=tmp_path/str(foreign)
        store,_=_pending_files(root,foreign)
        store.db.close()
        app=traced_app(root)
        with TestClient(app,headers=HEADERS) as client,ReadingMeasurements(client,app,root) as measure:
            response,cost=measure.request('status','GET','/api/v3/status')
            assert response.json()['problems']['count']==0
            costs.append(cost)
    assert costs[1]['sql']<=costs[0]['sql']+10,costs
    # The library grows 11.4x; waiting-media lookup must grow linearly, not quadratically.
    assert costs[1]['rows_scanned']<=costs[0]['rows_scanned']*20,costs


def test_source_readiness_and_idle_dispatch_ignore_foreign_pending_media(tmp_path):
    costs=[]
    for foreign in (26,400):
        store,feeds=_pending_files(tmp_path/str(foreign),foreign)
        try:
            with store.db:
                store.db.execute("UPDATE jobs SET status='done' WHERE kind='fetch'")
            with measure_reads(store.db) as work:
                assert store.ready_sources([feeds[0]])==1
            dispatch,job=_steps(store,feeds=[feeds[0]])
            assert job is None
            costs.append((work.rows,dispatch))
        finally:
            store.db.close()
    assert costs[1][0]<=costs[0][0]+2000,costs
    assert costs[1][1]<=costs[0][1]+2000,costs


def test_readiness_waits_for_viewed_publication_cleanup(tmp_path):
    store,feeds=_pending_files(tmp_path,1)
    try:
        with store.db:
            store.db.execute("UPDATE jobs SET status='done' WHERE kind='fetch'")
        item=store.db.execute('SELECT id FROM items WHERE feed_id = %s LIMIT 1',(feeds[0],)).fetchone()[0]
        assert store.ready_sources([feeds[0]])==1
        store.viewed(item)
        with store.db:store.jobs.reconcile_cleanup()
        assert store.ready_sources([feeds[0]])==0
        job=store.jobs.next_job(store.clock(),deleting_only=True)
        assert job is not None and job.kind=='delete'
        store.jobs.delete(job)
        assert store.ready_sources([feeds[0]])==1
    finally:
        store.db.close()


def test_empty_dispatch_with_linked_articles_grows_linearly(tmp_path):
    costs=[]
    for count in (26,400):
        feeds,_=build_library(tmp_path/str(count),[count])
        store=Store(tmp_path/str(count),lambda _:None)
        try:
            with store.db:
                for root in store.db.execute('SELECT id FROM items').fetchall():
                    article=str(uuid4())
                    store.db.execute('INSERT INTO items ( id , source_id , url , published , feed_id ) VALUES ( %s , %s , %s , 0 , NULL )',
                        (article,article,'https://article.test/'+article))
                    store.db.execute('INSERT INTO publication_links VALUES ( %s , %s )',(root['id'],article))
                store.db.execute("UPDATE jobs SET status='done' WHERE kind='fetch'")
            steps,job=_steps(store,feeds=feeds)
            assert job is None
            costs.append(steps)
        finally:
            store.db.close()
    assert costs[1]<=costs[0]*25,costs
