"""Runtime invariants retained after removing the obsolete file migration tools."""
from tests.support.cards import read_all_cards
import asyncio
import json
import httpx
from tests.support.storage import Store, database
from tests.support.worker_library import pass_worker, saved, worker_library, workers
from tests.support.app import create_app
from tests.support.filters import transport
from tests.support.presence import presence
from tests.support.reader import wait_for
from tests.support.source import Image, SyntheticMaterial
from tests.support.v3 import TestClient
from oko.collection.contracts import DiscoveryResult, FetchResult, Publication


def test_postgres_runs_all_workers_http_media_and_restart(tmp_path):
    source=tmp_path/'previous'
    store,item,execution,tasks=worker_library(source)
    asyncio.run(pass_worker(workers(execution,tasks)[0],tasks))
    context=saved(store,item)['processing_metadata']['_pass']
    with store.db:store.db.execute("UPDATE feeds SET cursor='{\"synthetic_cursor\":null}' WHERE id='demo'")
    store.db.close()
    target=source
    calls=[]
    native=transport(calls,theses=True)
    app=None
    def model(request):
        assert not app.state.host.require_store().db.in_transaction
        return native.handler(request)
    class Source:
        changes=None
        async def discover(self,cursor):
            assert not app.state.host.require_store().db.in_transaction
            return DiscoveryResult((Publication('two','https://example.org/two',200),),'accepted')
        async def fetch(self,publication):
            assert not app.state.host.require_store().db.in_transaction
            return FetchResult(SyntheticMaterial('New title','New useful text',images=(Image('test.png',b'synthetic-image'),)))
    app=create_app(target,token='test',api_only=True,source=Source(),ai_transport=httpx.MockTransport(model))
    with TestClient(app,headers={'Authorization':'Bearer test'}) as client:
        presence(client)
        assert client.post('/api/v3/collect').status_code==200
        cards=wait_for(client,lambda: rows if len(rows:=read_all_cards(client, '/api/v3/cards'))==2 and all(client.get('/api/v3/publication/'+row['id']).json()['theses']['result'] for row in rows) else None)
        newest=next(card for card in cards if card['id']!=item)
        material=wait_for(client,lambda: value if (value := client.get('/api/v3/publication/'+newest['id']).json()) and any(media['role'] == 'attachment' for media in value['media']) else None)
        assert 'New useful text' in material['text']
        assert client.get([media for media in material['media'] if media['role'] == 'attachment'][0]['url']).content==b'synthetic-image'
        assert client.get('/api/v3/status').status_code==200
    with database(target) as db:
        assert json.loads(db.execute('SELECT processing_metadata FROM publications WHERE id=%s',(item,)).fetchone()[0])['_pass']==context
        assert db.execute("SELECT count(*) FROM jobs WHERE kind='filter' AND status='done'").fetchone()[0]==2
        assert db.execute("SELECT count(*) FROM jobs WHERE kind='theses' AND status='done'").fetchone()[0]==2
        assert db.execute("SELECT count(*) FROM jobs WHERE kind='download_media' AND status='done'").fetchone()[0]==1
    generated=len(calls)
    # Reopen the same accepted copy, with no source/model activity admitted.
    with TestClient(create_app(target,token='test',api_only=True,ai_transport=httpx.MockTransport(model)),headers={'Authorization':'Bearer test'}) as client:
        restored=read_all_cards(client, '/api/v3/cards')
        assert {card['id'] for card in restored}=={card['id'] for card in cards}
        assert all(client.get('/api/v3/publication/'+card['id']).json()['theses']['result'] for card in restored)
        reopened=client.get('/api/v3/publication/'+newest['id']).json()
        assert client.get([media for media in reopened['media'] if media['role'] == 'attachment'][0]['url']).content==b'synthetic-image'
    assert len(calls)==generated
