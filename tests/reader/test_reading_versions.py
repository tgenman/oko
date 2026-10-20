"""Persisted resource versions and selective delivery at the real HTTP seam."""
import time
from fastapi.testclient import TestClient
from tests.support.reader import HEADERS
from tests.support.reading_cost import library, traced_app, tracing


def check(client, resources, storage=None):
    response = client.post('/api/v3/reading/check', json={'storage_id': storage, 'resources': resources})
    assert response.status_code == 200, response.text
    return response


def changed_within_background_interval(client, request, storage):
    deadline=time.monotonic()+2
    while time.monotonic()<deadline:
        resource=check(client,[request],storage).json()['resources'][0]
        if resource['state']=='changed':return resource
        time.sleep(.02)
    raise AssertionError('Runtime did not publish the due transition within two seconds')


def test_unchanged_versions_return_no_data_or_full_projection(tmp_path):
    feeds, items = library(tmp_path, [26] * 4)
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        tracing(client, app)
        request = [{'kind':'navigation','known_version':None}, {'kind':'list','options':{'target':{'scope':'feed','feed_id':feeds[0]},'sort':'newest','show_hidden':False},'known_version':None,'known_cards':{}}]
        first = check(client, request).json()
        assert first['state'] == 'current'
        changed = {(r['kind'], r['id']):r for r in first['resources'] if r['state']=='changed'}
        cards = [r for r in changed.values() if r['kind']=='card']
        assert len(cards) == 26
        assert all('theses' not in r['data'] and 'filter' not in r['data'] for r in cards)
        list_resource = next(r for r in changed.values() if r['kind']=='list')
        request[0]['known_version'] = next(r['version'] for r in changed.values() if r['kind']=='navigation')
        request[1]['known_version'] = list_resource['version']
        request[1]['known_cards'] = {r['id']:r['version'] for r in cards}
        response = check(client, request, first['storage_id'])
        frame = response.json()
        assert all(r['state']=='unchanged' and 'data' not in r for r in frame['resources'])
        assert int(response.headers['X-Test-SQL']) < 35
        assert not any('FROM settings' in q or 'SELECT * FROM items' in q for q in app.state.last_sql)


def test_material_version_survives_restart_and_viewed_is_explicit(tmp_path):
    _, items = library(tmp_path, [1])
    previous = None
    for _ in range(2):
        app = traced_app(tmp_path)
        with TestClient(app, headers=HEADERS) as client:
            response = client.get('/api/v3/reading/materials/' + items[0])
            assert response.status_code == 200
            frame = response.json()
            current = frame['resources'][0]
            assert current['state']=='changed' and current['kind']=='material'
            if previous:
                assert frame['storage_id'] == previous['storage_id']
                assert current['version'] == previous['resources'][0]['version']
            previous = frame
    with TestClient(traced_app(tmp_path), headers=HEADERS) as client:
        assert client.post('/api/v3/materials/'+items[0]+'/viewed').status_code == 200
        frame = check(client,[{'kind':'material','id':items[0],'known_version':current['version']}],previous['storage_id']).json()
        assert frame['resources'][0]['state']=='unavailable'
        assert frame['resources'][0]['reason']=='viewed'


def test_noop_write_and_other_folder_leave_entity_versions_unchanged(tmp_path):
    feeds, items = library(tmp_path,[1,1])
    app=traced_app(tmp_path)
    with TestClient(app,headers=HEADERS) as client:
        req=[{'kind':'material','id':items[0],'known_version':None},{'kind':'list','options':{'target':{'scope':'feed','feed_id':feeds[0]},'sort':'newest','show_hidden':False},'known_version':None,'known_cards':{}}]
        first=check(client,req).json()
        versions={(r['kind'],r['id']):r['version'] for r in first['resources']}
        material=next(r for r in first['resources'] if r['kind']=='material')
        listing=next(r for r in first['resources'] if r['kind']=='list')
        card=next(r for r in first['resources'] if r['kind']=='card')
        req[0]['known_version']=material['version']
        req[1]['known_version']=listing['version']
        req[1]['known_cards']={card['id']:card['version']}
        assert client.patch('/api/v3/feeds/'+feeds[0],json={'title':'Synthetic 0'}).status_code==200
        assert client.post('/api/v3/materials/'+items[1]+'/viewed').status_code==200
        current=check(client,req,first['storage_id']).json()
        assert all(r['state']=='unchanged' for r in current['resources'])
        assert versions[material['kind'],material['id']]==next(r['version'] for r in current['resources'] if r['kind']=='material')


def test_feed_version_increments_once_and_sql_rollback_keeps_it(tmp_path):
    import pytest
    feeds,_=library(tmp_path,[1])
    app=traced_app(tmp_path)
    with TestClient(app,headers=HEADERS) as client:
        resource={'kind':'feed','id':feeds[0],'known_version':None}
        first=check(client,[resource]).json()
        before=first['resources'][0]
        assert client.patch('/api/v3/feeds/'+feeds[0],json={'title':'Renamed'}).status_code==200
        renamed=check(client,[resource],first['storage_id']).json()['resources'][0]
        assert int(renamed['version'])==int(before['version'])+1
        assert renamed['data']['title']=='Renamed'
        def rollback():
            store=app.state.runtime.require_store()
            with store.db:
                store.db.execute('BEGIN IMMEDIATE')
                store.db.execute("UPDATE feeds SET title='Uncommitted' WHERE id=?",(feeds[0],))
                raise ValueError('rollback fixture')
        with pytest.raises(ValueError,match='rollback fixture'):
            client.portal.call(rollback)
        resource['known_version']=renamed['version']
        response=check(client,[resource],first['storage_id']).json()
        assert response['resources'][0]['state']=='unchanged'
        assert response['resources'][0]['version']==renamed['version']


def test_due_fetch_publishes_a_new_material_version_without_dispatch(tmp_path):
    _,items=library(tmp_path,[1])
    clock=[0.0]
    app=traced_app(tmp_path,clock=lambda:clock[0],wall_clock=lambda:1800000000+clock[0])
    with TestClient(app,headers=HEADERS) as client:
        def schedule():
            store=app.state.runtime.require_store()
            with store.db:
                store.db.execute("UPDATE jobs SET next=10,error='Synthetic wait',problem_code='source-connection' WHERE kind='fetch' AND item=?",(items[0],))
        client.portal.call(schedule)
        resource={'kind':'material','id':items[0],'known_version':None}
        first=check(client,[resource]).json()
        before=first['resources'][0]
        assert before['data']['content']['loading'] is False
        resource['known_version']=before['version']
        assert check(client,[resource],first['storage_id']).json()['resources'][0]['state']=='unchanged'
        clock[0]=11.0
        after=changed_within_background_interval(client,resource,first['storage_id'])
        assert after['state']=='changed'
        assert int(after['version'])>int(before['version'])
        assert after['data']['content']['loading'] is True


def test_list_member_changes_are_selective_and_reset_is_explicit(tmp_path):
    feeds,items=library(tmp_path,[2])
    app=traced_app(tmp_path)
    with TestClient(app,headers=HEADERS) as client:
        req={'kind':'list','options':{'target':{'scope':'all'},'sort':'newest','show_hidden':False},'known_version':None,'known_cards':{}}
        first=check(client,[req]).json()
        listing=next(r for r in first['resources'] if r['kind']=='list')
        req['known_version']=listing['version']
        req['known_cards']={r['id']:r['version'] for r in first['resources'] if r['kind']=='card'}
        assert client.post('/api/v3/materials/'+items[0]+'/viewed').status_code==200
        changed=check(client,[req],first['storage_id']).json()
        listing=next(r for r in changed['resources'] if r['kind']=='list')
        assert listing['state']=='changed' and len(listing['data']['ids'])==1
        assert not any(r['kind']=='card' and r['state']=='changed' for r in changed['resources'])
        reset=check(client,[req],'00000000-0000-4000-8000-000000000000').json()
        assert reset=={'state':'reset','storage_id':first['storage_id']}


def test_theses_completion_delivers_no_text_or_media(tmp_path):
    from oko.collection.contracts import Material
    _,items=library(tmp_path,[1])
    app=traced_app(tmp_path)
    with TestClient(app,headers=HEADERS) as client:
        assert client.patch('/api/v3/settings/oko/global',json={'theses':True}).status_code==200
        def complete():
            store=app.state.runtime.require_store()
            job=store.next_job(store.clock(),feeds=[store.item_feed(items[0])])
            assert job is not None and job.kind=='fetch'
            store.save(job,Material('Synthetic','Long original text '*1000,captions='available'))
        client.portal.call(complete)
        first=client.get('/api/v3/reading/materials/'+items[0]).json()
        assert all(r['state']=='changed' for r in first['resources'])
        requests=[{'kind':r['kind'],'id':r['id'],'known_version':r['version']} for r in first['resources']]
        def summarize():
            store=app.state.runtime.require_store()
            job=store.next_job(store.clock(),ai_only=True)
            assert job is not None and job.kind=='theses'
            signature=store.processing.theses.signature(job.item)
            assert signature is not None
            store.save_derivative(job,signature,{'points':[{'text':'Synthetic summary','sources':[{'id':job.item,'url':'https://example.org/0/0'}]}]},'Synthetic summary',provenance=None)
        client.portal.call(summarize)
        current=check(client,requests,first['storage_id']).json()
        changed=[r for r in current['resources'] if r['state']=='changed']
        assert [r['kind'] for r in changed]==['theses']
        assert changed[0]['data']['theses']['result']['points'][0]['text']=='Synthetic summary'
        assert 'Long original text' not in str(current)


def test_stopped_check_returns_final_status_and_explicit_unavailability(tmp_path):
    _,items=library(tmp_path,[1])
    app=traced_app(tmp_path)
    with TestClient(app,headers=HEADERS) as client:
        first=check(client,[{'kind':'status','known_version':None}]).json()
        assert client.post('/api/v3/stop').status_code==200
        import time
        for _ in range(100):
            if app.state.runtime._store is None:break
            time.sleep(.01)
        assert app.state.runtime._store is None
        request=[{'kind':'status','known_version':None},{'kind':'material','id':items[0],'known_version':None}]
        stopped=check(client,request,first['storage_id']).json()
        assert stopped['resources'][0]['data']['runtime_state']=='stopped'
        assert stopped['resources'][1]['reason']=='runtime_stopped'
        request[0]['known_version']=stopped['resources'][0]['version']
        assert check(client,request,first['storage_id']).json()['resources'][0]['state']=='unchanged'


def test_problem_expiry_advances_status_without_an_external_write(tmp_path):
    from tests.support.codex import CodexService,configure_codex
    wall=[1800000000.0]
    app=traced_app(tmp_path,codex_client=CodexService(),wall_clock=lambda:wall[0])
    with TestClient(app,headers=HEADERS) as client:
        assert client.patch('/api/v3/settings/oko/global',json={'theses':True}).status_code==200
        request={'kind':'status','known_version':None}
        first=check(client,[request]).json()
        assert first['resources'][0]['data']['problems']['current']
        configure_codex(client)
        finished=check(client,[request],first['storage_id']).json()['resources'][0]
        assert finished['data']['problems']['recent']
        request['known_version']=finished['version']
        wall[0]+=86399
        assert check(client,[request],first['storage_id']).json()['resources'][0]['state']=='unchanged'
        wall[0]+=1
        expired=changed_within_background_interval(client,request,first['storage_id'])
        assert expired['state']=='changed' and expired['data']['problems']['recent']==[]


def test_linked_feed_move_updates_lists_without_retransmitting_cards(tmp_path):
    feeds,items=library(tmp_path,[1,1])
    app=traced_app(tmp_path)
    with TestClient(app,headers=HEADERS) as client:
        folder=client.get('/api/v3/subscriptions').json()['feeds'][0]['folder_id']
        options={'target':{'scope':'folder','folder_id':folder},'sort':'newest','show_hidden':False}
        request={'kind':'list','options':options,'known_version':None,'known_cards':{}}
        first=check(client,[request,{'kind':'feed','id':feeds[0],'known_version':None}]).json()
        listing=next(r for r in first['resources'] if r['kind']=='list')
        feed=next(r for r in first['resources'] if r['kind']=='feed')
        request.update(known_version=listing['version'],known_cards={r['id']:r['version'] for r in first['resources'] if r['kind']=='card'})
        assert client.patch('/api/v3/feeds/'+feeds[0],json={'folder_id':client.get('/api/v3/subscriptions').json()['feeds'][1]['folder_id'],'title':'Moved'}).status_code==200
        current=check(client,[request,{'kind':'feed','id':feeds[0],'known_version':feed['version']}],first['storage_id']).json()
        assert next(r for r in current['resources'] if r['kind']=='list')['data']['ids']==[]
        assert next(r for r in current['resources'] if r['kind']=='feed')['data']['title']=='Moved'
        assert not any(r['kind']=='card' and r['state']=='changed' for r in current['resources'])


def test_registered_list_batches_work_and_versions_in_the_write_transaction(tmp_path):
    from oko.storage.store import Store
    import pytest
    for count in [100,200,400]:
        root=tmp_path/str(count)
        store=Store(root,lambda _:None)
        store.subscriptions.ensure_demo()
        options={'target':{'scope':'all'},'sort':'newest','show_hidden':False}
        before=store.read_versions.list_version(options)['version']
        steps=[]
        store.db.set_progress_handler(lambda:steps.append(1) or 0,100)
        with store.db:
            store.db.executemany('INSERT INTO items(id,feed_id,published) VALUES (?,?,?)',[(f'item-{n}','demo',n) for n in range(count)])
        store.db.set_progress_handler(None,0)
        assert len(steps)*100<count*1000,(count,len(steps)*100)
        assert store.read_versions.list_version(options)['version']==before+1
        assert len(store.read_versions.ids(options))==count
        # Net-zero changes do not advance a list, and failed writes roll back
        # both membership and the transaction's pending list maintenance.
        with store.db:
            store.db.execute("UPDATE items SET viewed=1 WHERE id='item-0'")
            store.db.execute("UPDATE items SET viewed=0 WHERE id='item-0'")
        assert store.read_versions.list_version(options)['version']==before+1
        with pytest.raises(ValueError):
            with store.db:
                store.db.execute("UPDATE items SET viewed=1 WHERE id='item-0'")
                raise ValueError('rollback')
        store.db.close()
        reopened=Store(root,lambda _:None)
        assert reopened.read_versions.list_version(options)['version']==before+1
        assert len(reopened.read_versions.ids(options))==count
        reopened.db.close()


def test_filter_resource_keeps_saved_provenance_across_restart(tmp_path):
    from tests.support.app import create_app
    from tests.support.filters import FilterSource,transport,setup_filters
    from tests.support.reader import wait_for
    options=dict(source=FilterSource(),token='test',api_only=True,ai_transport=transport([]))
    identity=None
    for _ in range(2):
        with TestClient(create_app(tmp_path,**options),headers=HEADERS) as client:
            if identity is None:
                setup_filters(client)
                cards=wait_for(client,lambda:(v if len(v:=client.get('/api/v3/cards').json())==2 and all(c['filter']['attempt']['state']=='ready' for c in v) else None))
                identity=cards[0]['id']
            expected=client.get('/api/v3/materials/'+identity).json()['filter']['result']['provenance']
            frame=client.get('/api/v3/reading/materials/'+identity).json()
            actual=next(r['data']['filter']['result']['provenance'] for r in frame['resources'] if r['kind']=='filter')
            assert actual==expected=={'kind':'local','processor_id':'llamacpp','model':{'name':'local:latest','state':'known'}}


def test_list_maintenance_failure_rolls_back_data_and_versions(tmp_path,monkeypatch):
    from oko.storage.store import Store
    import pytest
    store=Store(tmp_path,lambda _:None)
    store.subscriptions.ensure_demo()
    options={'target':{'scope':'all'},'sort':'newest','show_hidden':False}
    before=store.read_versions.list_version(options)['version']
    def fail():
        raise OSError('Synthetic commit failure')
    for explicit in [False,True]:
        with monkeypatch.context() as patch:
            patch.setattr(store.db,'flush_reading_lists',fail)
            with pytest.raises(OSError,match='Synthetic commit failure'):
                if explicit:
                    store.db.execute("INSERT INTO items(id,feed_id,published) VALUES ('rejected','demo',1)")
                    store.db.commit()
                else:
                    with store.db:
                        store.db.execute("INSERT INTO items(id,feed_id,published) VALUES ('rejected','demo',1)")
        assert store.read_versions.ids(options)==[]
        assert store.read_versions.list_version(options)['version']==before
    store.db.execute("INSERT INTO items(id,feed_id,published) VALUES ('uncommitted','demo',1)")
    store.db.close()
    reopened=Store(tmp_path,lambda _:None)
    assert reopened.read_versions.ids(options)==[]
    assert reopened.read_versions.list_version(options)['version']==before
    reopened.db.close()
