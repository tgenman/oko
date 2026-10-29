from tests.support.processing_jobs import save_derivative
from tests.support.processing_jobs import reconcile
from tests.support.source import save_material
"""Cost and response parity of the actual browser opening endpoint."""
import json
import os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from tests.support.source import SyntheticMaterial,Image
from oko.processing.llamacpp import ProcessorConfig
from tests.support.storage import Store
from oko.storage.job_types import Job
from oko.subscriptions.subscriptions import Overrides
from tests.support.reader import HEADERS
from tests.support.reading_cost import library, traced_app
from tests.support.reading_measurements import ReadingMeasurements


def prepared(root, count, processed):
    feeds,items=library(root,[count]*4)
    if processed:
        store=Store(root,lambda _:None)
        store.configure_processor(ProcessorConfig(endpoint='http://localhost:9931',model='local:latest',local_execution=True))
        store.subscriptions.update_settings('oko','global',Overrides(theses=True,prompt_filters='Remove ads',processor='llamacpp'))
        store.settings_changed()
        def claim(kind):
            row=store.db.execute('SELECT id , generation FROM jobs WHERE kind = %s AND item = %s',(kind,items[0])).fetchone()
            assert row is not None
            with store.db:store.db.execute("UPDATE jobs SET status = 'running' WHERE id = %s",(row['id'],))
            return Job(row['id'],kind,items[0],row['generation'])
        for index in range(2):
            if index: assert store.jobs.retry_material(items[0])
            job=store.jobs.next_job(store.clock(),feeds=[feeds[0]])
            assert job is not None and job.kind=='fetch'
            # Select the fixture's own first fetch, without relying on generated IDs.
            items[0]=job.item
            save_material(store,job,SyntheticMaterial('Saved title',f'Saved text {index}\nAdvertisement',captions='available',images=(Image('image-1.png',b'fixture'),)))
            reconcile(store,'filter')
            filter_job=claim('filter')
            assert filter_job is not None and filter_job.kind=='filter'
            signature=store.filters.signature(filter_job.item)
            save_derivative(store,filter_job,signature,dict(hidden=False,reason='ad',removed=['Advertisement']),'filtered',provenance=None)
            store.processing.sync()
            reconcile(store,'theses')
            thesis_job=claim('theses')
            assert thesis_job is not None and thesis_job.kind=='theses'
            signature=store.processing.theses.signature(thesis_job.item)
            save_derivative(store,thesis_job,signature,dict(points=[dict(text='Saved summary',sources=[dict(id=thesis_job.item,url='https://example.org/0/0')])]),'summary',provenance=None)
        store.db.close()
    return feeds,items


@pytest.mark.parametrize('count',[26,100])
@pytest.mark.parametrize('processed',[False,True])
def test_open_read_budget_and_parity(tmp_path,count,processed):
    _,items=prepared(tmp_path,count,processed)
    app=traced_app(tmp_path)
    with TestClient(app,headers=HEADERS) as client,ReadingMeasurements(client,app,tmp_path) as measure:
        reply,cost=measure.request('open','GET','/api/v3/publication/'+items[0])
        statements=list(app.state.last_sql)
        files=dict(measure.files)
        publication=reply.json()
        assert publication['id']==items[0]
        if processed:
            assert publication['theses']['result']['points']
            assert publication['filter']['result']['removed']==['Advertisement']
            assert any(media['kind']=='image' for media in [media for media in publication['media'] if media['role'] == 'attachment']) and 'previous_versions' not in publication
        cost.update(selects=sum(q.lstrip().upper().startswith(('SELECT','WITH')) for q in statements),
                    writes=sum(q.lstrip().upper().startswith(('INSERT','UPDATE','DELETE','REPLACE')) for q in statements),
                    max_file_reads=max(files.values(),default=0))
        output=Path(os.environ.get('OKO_TEST_ARTIFACT_DIR',tmp_path));output.mkdir(parents=True,exist_ok=True)
        (output/f'open-{count*4}-{processed}.json').write_text(json.dumps(cost,indent=2))
        print('OPEN_COST',json.dumps(cost))
        assert cost['writes']==0,statements
        assert cost['selects']<=8,statements
        assert cost['max_file_reads']<=1,files
        if processed:
            attachment=[media for media in publication['media'] if media['role'] == 'attachment'][0]
            media,media_cost=measure.request('media','GET',attachment['url'])
            assert media.content==b'fixture'
            assert media_cost['content_opens']==1
            assert media_cost['content_stats']>=1
            assert media_cost['read_bytes']==len(media.content)
            assert sum(measure.files.values())==1
            assert all(Path(path).is_relative_to(tmp_path/'media') for path in measure.files)


def source_library(root, kind, complete):
    feeds,items=library(root,[1])
    store=Store(root,lambda _:None)
    telegram=kind.startswith('telegram')
    with store.db:
        store.db.execute('UPDATE feeds SET kind = %s , transport = %s , telegram_account = %s , telegram_peer = %s WHERE id = %s',
            ('telegram' if telegram else kind,'telegram' if telegram else kind,
             'synthetic-account' if kind=='telegram-legacy' else None,123 if kind=='telegram-legacy' else None,feeds[0]))
        store.db.execute("UPDATE feeds SET connection = 'connected' WHERE id = %s",(feeds[0],))
        store.db.execute('UPDATE items SET complete = %s WHERE id = %s',(int(complete),items[0]))
        binding={'kind':'web','username':'synthetic_channel','preview_url':'https://t.me/s/synthetic_channel'} if kind=='telegram-web' else {'kind':'client','role':'personal','account_user_id':'123','peer_id':'456'} if kind=='telegram-client' else None
        if binding:store.db.execute('INSERT INTO telegram_bindings VALUES ( %s , %s )',(feeds[0],json.dumps(binding)))
    store.db.close()
    return items[0]



@pytest.mark.parametrize('kind',['demo','rss','youtube','telegram-web','telegram-client'])
@pytest.mark.parametrize('complete',[False,True])
def test_publication_http_parity_for_source_kinds(tmp_path,kind,complete):
    item=source_library(tmp_path,kind,complete)
    with TestClient(traced_app(tmp_path),headers=HEADERS) as client:
        response=client.get('/api/v3/publication/'+item)
        assert response.status_code==200,response.text
        actual=response.json()
        assert actual['id']==item
        assert actual['source']['kind']==('telegram' if kind.startswith('telegram') else kind)
        assert actual['availability']==('available' if complete else 'pending')
        unchanged=client.get('/api/v3/publication/'+item,headers={'If-None-Match':response.headers['ETag']})
        assert unchanged.status_code==304 and not unchanged.content


@pytest.mark.parametrize('count',[26,100])
def test_idle_conditional_reads_return_no_body_or_writes(tmp_path,count):
    _,items=library(tmp_path,[count]*4)
    app=traced_app(tmp_path)
    with TestClient(app,headers=HEADERS) as client,ReadingMeasurements(client,app,tmp_path) as measure:
        for url in ('/api/v3/status','/api/v3/navigation','/api/v3/publication/'+items[0]):
            initial=client.get(url)
            assert initial.status_code==200
            reply,cost=measure.request('idle','GET',url,expected_status=304,headers={'If-None-Match':initial.headers['ETag']})
            queries=app.state.last_sql
            print('IDLE_COST',url,json.dumps(cost))
            assert reply.status_code==304 and not reply.content
            if '/publication/' not in url:
                assert cost['content_opens']==cost['content_stats']==0
            writes=[q for q in queries if q.lstrip().upper().startswith(('UPDATE','DELETE','INSERT'))]
            assert not writes, writes
