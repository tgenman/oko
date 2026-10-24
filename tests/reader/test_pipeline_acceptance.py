"""#115 joint acceptance on synthetic previous-format storage, never installed data."""
import asyncio
import json
import sqlite3

import httpx
import pytest

from oko.collection.contracts import DiscoveryResult, FetchResult, Publication
from oko.storage.migration import MigrationRefused, file_hash, inventory, prepare
from oko.storage.store import Store
from tests.support.app import create_app
from tests.support.filters import transport
from tests.support.presence import presence
from tests.support.reader import wait_for
from tests.support.source import Image, SyntheticMaterial
from tests.support.v3 import TestClient
from tests.support.worker_library import pass_worker, saved, worker_library, workers


def test_previous_schema_copy_runs_all_three_crons_http_media_and_restart(tmp_path):
    source=tmp_path/'previous'
    store,item,execution,tasks=worker_library(source)
    asyncio.run(pass_worker(workers(execution,tasks)[0],tasks))
    context=saved(store,item)['processing_metadata']['_pass']
    with store.db:store.db.execute("UPDATE feeds SET cursor='{\"synthetic_cursor\":null}' WHERE id='demo'")
    store.db.close()
    # #113 shape: no per-Job input revision column; upgrading must retain results.
    with sqlite3.connect(source/'state.sqlite') as db:
        db.execute('ALTER TABLE jobs DROP COLUMN payload')
    (source/'instance.lock').touch()
    original=file_hash(source/'state.sqlite'),inventory(source)
    target=tmp_path/'candidate'
    report=prepare(source,target)
    assert report['verified'] and report['settings']['verified']
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
        cards=wait_for(client,lambda: rows if len(rows:=client.get('/api/v3/cards').json())==2 and all(row['theses']['result'] for row in rows) else None)
        newest=next(card for card in cards if card['id']!=item)
        material=wait_for(client,lambda: value if (value:=client.get('/api/v3/materials/'+newest['id']).json())['content']['attachments'] else None)
        assert 'New useful text' in material['content']['text']
        assert client.get(material['content']['attachments'][0]['url']).content==b'synthetic-image'
        assert client.get('/api/v3/status').status_code==200
    with sqlite3.connect(target/'state.sqlite') as db:
        assert json.loads(db.execute('SELECT processing_metadata FROM items WHERE id=?',(item,)).fetchone()[0])['_pass']==context
        assert db.execute("SELECT count(*) FROM jobs WHERE kind='filter' AND status='done'").fetchone()[0]==2
        assert db.execute("SELECT count(*) FROM jobs WHERE kind='theses' AND status='done'").fetchone()[0]==2
        assert db.execute("SELECT count(*) FROM jobs WHERE kind='download_media' AND status='done'").fetchone()[0]==1
    generated=len(calls)
    # Reopen the same accepted copy, with no source/model activity admitted.
    with TestClient(create_app(target,token='test',api_only=True,ai_transport=httpx.MockTransport(model)),headers={'Authorization':'Bearer test'}) as client:
        restored=client.get('/api/v3/cards').json()
        assert {card['id'] for card in restored}=={card['id'] for card in cards}
        assert all(card['theses']['result'] for card in restored)
        reopened=client.get('/api/v3/materials/'+newest['id']).json()
        assert client.get(reopened['content']['attachments'][0]['url']).content==b'synthetic-image'
    assert len(calls)==generated
    assert (file_hash(source/'state.sqlite'),inventory(source))==original


@pytest.mark.parametrize('residue', ['job','settings','history','message','topic','file','binding','unknown'])
def test_residual_group_or_unproven_ownership_refuses_without_touching_source(tmp_path,residue):
    source=tmp_path/'previous'
    store=Store(source,lambda _:None)
    with store.db:
        if residue=='job':store.db.execute("INSERT INTO jobs(kind,item) VALUES ('discover','orphan-group')")
        elif residue=='settings':store.db.execute("INSERT INTO settings VALUES ('feed','orphan-group','theses','true')")
        elif residue=='history':store.db.execute("INSERT INTO initial_history(feed,selection) VALUES ('orphan-group','{}')")
        elif residue=='file':store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('group-file','orphan-group','photo','https://fixture.invalid/photo','group.png','pending_download')")
        elif residue=='binding':store.db.execute("INSERT INTO telegram_bindings VALUES ('empty-group','{\"kind\":\"client\"}')")
        elif residue=='unknown':
            store.subscriptions.ensure_demo()
            store.db.execute("UPDATE feeds SET kind='telegram',config='[]' WHERE id='demo'")
        else:
            store.db.execute('CREATE TABLE legacy_'+residue+'s (value TEXT)')
            store.db.execute('INSERT INTO legacy_'+residue+"s VALUES ('retained')")
    store.db.close()
    (source/'instance.lock').touch()
    (source/'retained-file').write_bytes(b'group data must remain')
    before=file_hash(source/'state.sqlite'),inventory(source)
    with pytest.raises(MigrationRefused,match='group_or_unknown_data'):
        prepare(source,tmp_path/'target')
    assert not (tmp_path/'target').exists()
    assert (file_hash(source/'state.sqlite'),inventory(source))==before


@pytest.mark.parametrize('access',[None,'web','client'])
def test_empty_store_and_proven_channels_pass(tmp_path,access):
    source=tmp_path/'previous'
    store=Store(source,lambda _:None)
    if access:
        store.subscriptions.ensure_demo()
        with store.db:
            store.db.execute("UPDATE feeds SET kind='telegram',config='{\"source_kind\":\"channel\"}' WHERE id='demo'")
            store.db.execute('INSERT INTO telegram_bindings VALUES (?,?)',('demo',json.dumps(dict(kind=access))))
    store.db.close()
    (source/'instance.lock').touch()
    assert prepare(source,tmp_path/'target')['verified']


@pytest.mark.parametrize('kind',['discover','read','feed-delete','fetch','filter','theses','download_media','delete','unknown'])
def test_every_residual_job_kind_needs_a_proven_owner(tmp_path,kind):
    source=tmp_path/'previous'
    store=Store(source,lambda _:None)
    with store.db:store.db.execute('INSERT INTO jobs(kind,item) VALUES (?,?)',(kind,'orphan-group'))
    store.db.close()
    (source/'instance.lock').touch()
    before=file_hash(source/'state.sqlite'),inventory(source)
    with pytest.raises(MigrationRefused,match='unproven_job_owner'):prepare(source,tmp_path/'target')
    assert not (tmp_path/'target').exists()
    assert (file_hash(source/'state.sqlite'),inventory(source))==before


@pytest.mark.parametrize('table',['telegram_read_members','telegram_read_state','telegram_claim_owners','telegram_avatars'])
def test_residual_telegram_records_need_a_proven_feed(tmp_path,table):
    source=tmp_path/'previous'
    store=Store(source,lambda _:None)
    statements={
        'telegram_read_members':"INSERT INTO telegram_read_members(feed,member) VALUES ('orphan-group',123)",
        'telegram_read_state':"INSERT INTO telegram_read_state(feed) VALUES ('orphan-group')",
        'telegram_claim_owners':"INSERT INTO telegram_claim_owners(kind,value,feed) VALUES ('peer','123','orphan-group')",
        'telegram_avatars':"INSERT INTO telegram_avatars VALUES ('orphan-group',x'00','image/png','synthetic')"}
    with store.db:store.db.execute(statements[table])
    store.db.close()
    (source/'instance.lock').touch()
    before=file_hash(source/'state.sqlite'),inventory(source)
    with pytest.raises(MigrationRefused,match='unproven_owner'):prepare(source,tmp_path/'target')
    assert not (tmp_path/'target').exists()
    assert (file_hash(source/'state.sqlite'),inventory(source))==before
