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
        legacy=client.get('/api/v3/materials/'+items[0]).json()
        reply,cost=measure.request('open','GET','/api/v3/reading/materials/'+items[0])
        statements=list(app.state.last_sql)
        files=dict(measure.files)
        parts={r['kind']:r['data'] for r in reply.json()['resources']}
        assert set(parts)=={'material','filter','theses','media'}
        for k,v in parts['material'].items():
            if k!='content': assert v==legacy[k],k
        for k,v in parts['material']['content'].items():assert v==legacy['content'][k],k
        assert parts['filter']['filter']==legacy['filter']
        assert parts['theses']['theses']==legacy['theses']
        for k,v in parts['media'].items(): assert v==legacy['content'][k],k
        if processed:
            assert legacy['theses']['result']['points']
            assert legacy['filter']['result']['removed']==['Advertisement']
            assert any(media['kind']=='image' for media in legacy['content']['attachments']) and 'previous_versions' not in legacy['content']
        cost.update(selects=sum(q.lstrip().upper().startswith(('SELECT','WITH')) for q in statements),
                    writes=sum(q.lstrip().upper().startswith(('INSERT','UPDATE','DELETE','REPLACE')) for q in statements),
                    max_file_reads=max(files.values(),default=0))
        output=Path(os.environ.get('OKO_TEST_ARTIFACT_DIR',tmp_path));output.mkdir(parents=True,exist_ok=True)
        (output/f'open-{count*4}-{processed}.json').write_text(json.dumps(cost,indent=2))
        print('OPEN_COST',json.dumps(cost))
        assert cost['writes']==0,statements
        assert cost['selects']<=8,statements
        assert cost['max_file_reads']<=1,files


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


def assert_text_parity(actual, legacy):
    for k,v in actual.items():
        if k=='content':
            for name,value in v.items():assert value==legacy[k][name]
        else:assert v==legacy[k]


@pytest.mark.parametrize('kind,complete',[('youtube',False),('telegram-client',True)])
def test_split_text_http_parity(tmp_path,kind,complete):
    item=source_library(tmp_path,kind,complete)
    with TestClient(traced_app(tmp_path),headers=HEADERS) as client:
        legacy=client.get('/api/v3/materials/'+item).json()
        response=client.get('/api/v3/reading/materials/'+item)
        assert response.status_code==200,response.text
        resource=next(r for r in response.json()['resources'] if r['kind']=='material')
        assert resource['state']=='changed',resource
        assert_text_parity(resource['data'],legacy)


@pytest.mark.parametrize('kind',['demo','rss','youtube','telegram-web','telegram-client'])
@pytest.mark.parametrize('complete',[False,True])
def test_split_text_projection_parity_for_source_kinds(tmp_path,kind,complete):
    # Both real projection paths, with independent PostgreSQL schemas and files per case.
    # No scheduler, HTTP/lifespan or external adapters are needed for this matrix.
    from types import SimpleNamespace
    from oko.execution import Execution
    from oko.services.queries import ReadingQueries
    from fastapi import FastAPI
    from oko.reader.reading_v3 import register
    from oko.reader.versions_v3 import VersionedReading
    from oko.storage.reading import reading_snapshot
    item=source_library(tmp_path,kind,complete)
    store=Store(tmp_path,lambda _:None)
    try:
        runtime=SimpleNamespace(execution=Execution(lambda: True), queries=ReadingQueries(lambda: store),
            processing_wait=lambda item,kind:store.processing.waiting(item,kind,store.clock(),store.wall_clock()))
        legacy=register(FastAPI(),runtime)(item).model_dump(mode='json')
        reading=VersionedReading(runtime,None,None,None)
        with reading_snapshot():
            actual=reading.text(item)
        assert_text_parity(actual,legacy)
        assert actual['source']['kind']==('telegram' if kind.startswith('telegram') else kind)
        assert actual['availability']==('available' if complete else 'pending')
    finally:
        store.db.close()


@pytest.mark.parametrize('count',[26,100])
def test_idle_check_only_compares_versions(tmp_path,count):
    _,items=library(tmp_path,[count]*4)
    app=traced_app(tmp_path)
    with TestClient(app,headers=HEADERS) as client,ReadingMeasurements(client,app,tmp_path) as measure:
        options=dict(target=dict(scope='all'),sort='newest',show_hidden=False)
        initial=client.post('/api/v3/reading/check',json=dict(storage_id=None,resources=[dict(kind='status',known_version=None),dict(kind='navigation',known_version=None),dict(kind='list',options=options,known_version=None,known_cards={})])).json()
        cards={r['id']:r['version'] for r in initial['resources'] if r['kind']=='card'}
        resources=[]
        for r in initial['resources']:
            if r['kind']=='list':resources.append(dict(kind='list',options=options,known_version=r['version'],known_cards=cards))
            elif r['kind']!='card':resources.append(dict(kind=r['kind'],**({'id':r['id']} if r['kind']=='feed' else {}),known_version=r['version']))
        opened=client.get('/api/v3/reading/materials/'+items[0]).json()
        resources.extend(dict(kind=r['kind'],id=r['id'],known_version=r['version']) for r in opened['resources'])
        reply,cost=measure.request('idle','POST','/api/v3/reading/check',json=dict(storage_id=initial['storage_id'],resources=resources))
        queries=app.state.last_sql
        print('IDLE_COST',json.dumps(cost))
        assert all(r['state']=='unchanged' for r in reply.json()['resources'])
        assert cost['sql']<=2,queries
        assert cost['content_opens']==cost['content_stats']==0
        assert not any(q.lstrip().upper().startswith(('UPDATE','DELETE','INSERT')) for q in queries)
