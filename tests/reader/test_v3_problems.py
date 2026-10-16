"""Domain problem/recovery contracts at the reader HTTP boundary."""
import json
import sqlite3
import pytest

from tests.support.app import create_app
from tests.support.v3 import TestClient
from tests.support.reader import wait_for
from oko.collection.contracts import Publication, SourceError

HEADERS={'Authorization':'Bearer test'}


def test_live_model_producer_has_domain_code_and_recovery_without_server_navigation(tmp_path):
    with TestClient(create_app(tmp_path,token='test',api_only=True),headers=HEADERS) as client:
        problem=client.get('/api/v3/status').json()['problems']['current'][0]
        assert problem['code']=='processor_not_configured'
        assert problem['recovery']==[{'kind':'configure_processor','processor_id':'codex'}]
        assert problem['affected']==[{'kind':'processor','processor_id':'codex'}]
        response=client.post('/api/v3/command-observations',json={'id':'setting-failed','context':{'operation':'settings_patch','target':{'scope':'oko','owner':'global'},'keys':['prompt_theses']},'result':'failed'})
        assert response.status_code==200,response.text
        observed=next(p for p in client.get('/api/v3/status').json()['problems']['current'] if p['command_id']=='setting-failed')
        assert observed['recovery']==[{'kind':'change_setting','target':{'scope':'oko','owner':'global'},'keys':['prompt_theses']}]
    with sqlite3.connect(tmp_path/'state.sqlite') as db:
        for (body,) in db.execute('SELECT body FROM reader_action_results UNION ALL SELECT body FROM reader_problems'):
            assert '"page"' not in body and '"reconnect"' not in body


class Unavailable:
    failure_code='fetch:UnexpectedExternalException'
    async def discover(self,cursor):
        return [Publication('one','https://source.test/one',1800000000)],'one'

    async def fetch(self,publication):
        raise SourceError('Saved source reason',code=self.failure_code)


@pytest.mark.parametrize('external_code',['fetch:UnexpectedExternalException','fetch:PermissionError','fetch:OSError'])
def test_remote_exception_does_not_expand_public_codes_and_keeps_material_recovery(tmp_path,external_code):
    source=Unavailable()
    source.failure_code=external_code
    with TestClient(create_app(tmp_path,token='test',api_only=True,source=source),headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        problem=wait_for(client,lambda:next((p for p in client.get('/api/v3/status').json()['problems']['current'] if p['code']=='source_unavailable'),None))
        assert problem['message']=='Saved source reason'
        material=problem['affected'][0]['material_id']
        assert problem['recovery']==[{'kind':'retry_material','material_ids':[material]}]
        assert 'UnexpectedExternalException' not in problem['code']
        waiting=client.get('/api/v3/status').json()['work']
        assert any(w['phase']=='waiting' and w['reason']=='retry_scheduled' and w['retry_at'] for w in waiting)


def test_historical_reconnect_is_preserved_without_fabricated_command(tmp_path):
    from oko.storage.store import Store
    store=Store(tmp_path,lambda _:None)
    body=dict(code='source-reconnect',section='action',title='Old identity',consequence='Original consequence',next_step='Original reason',reason='Original diagnostic cause',objects=[],action={'kind':'feed-settings','id':'old','page':'reconnect'},state='current',next_at=None)
    with store.db:
        store.db.execute('INSERT INTO reader_problems VALUES (?,?,?,?,?,?,?)',('saved-problem','historical-source','background',json.dumps(body),1800000000,1800000010,'resolved'))
    store.db.close()
    with TestClient(create_app(tmp_path,token='test',api_only=True,wall_clock=lambda:1800000020),headers=HEADERS) as client:
        p=next(p for p in client.get('/api/v3/status').json()['problems']['recent'] if p['id']=='saved-problem')
        assert p['code']=='legacy_problem' and p['raw_code']=='source-reconnect'
        assert p['recovery']==[]
        assert p['created_at']=='2027-01-15T08:00:00Z'
        assert p['finished_at']=='2027-01-15T08:00:10Z'
        assert p['consequence']=='Original consequence'
        assert p['message']=='Original diagnostic cause'


def test_collection_recovery_target_is_admitted_and_only_schedules_that_feed(tmp_path):
    import httpx
    calls=[]
    def reply(request):
        calls.append(request.url.host)
        return httpx.Response(200,text='<rss><channel><title>Source</title></channel></rss>')
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_enabled=True,rss_transport=httpx.MockTransport(reply)),headers=HEADERS) as client:
        for host in ['one.test','two.test']:
            checked=client.post('/api/v3/feeds/verify',json={'url':'https://'+host+'/feed'}).json()
            assert client.post('/api/v3/feeds',json={'url':checked['candidate']['url'],'verification':checked['verification'],'title':host,'folder':'Sources'}).status_code==200
        def complete_feeds():
            feeds=client.get('/api/v3/subscriptions').json()['feeds']
            return feeds if len(feeds)==2 and all(f['history']['state']=='complete' for f in feeds) else None
        feeds=wait_for(client,complete_feeds)
        target=next(f['id'] for f in feeds if f['title']=='one.test')
        calls.clear()
        assert client.post('/api/v3/collect',json={'scope':'feed','feed_id':target},headers={'X-Oko-Action':'one-feed'}).status_code==200
        wait_for(client,lambda: calls)
        assert set(calls)=={'one.test'}
        assert client.get('/api/v3/commands/one-feed').json()['context']=={'operation':'collect','target':{'scope':'feed','feed_id':target}}
        assert client.post('/api/v3/collect',json={'scope':'feed','feed_id':'missing'}).status_code==404
        assert client.post('/api/v3/collect',json={'scope':'folder','folder_id':'all'}).status_code==422


def test_typed_conflict_recovery_never_proposes_reconnecting_an_existing_binding(tmp_path):
    from tests.support.telegram import Telegram
    class Account(Telegram):
        user=101
        async def accounts(self):
            return ['one']
        async def identity(self,account):
            return self.user
        async def history_page(self,account,peer,upper,before,since):
            return dict(messages=(await self.page(account,peer,0,since))['messages'],upper=11,next_before=11,more=False)
    network=Account()
    with TestClient(create_app(tmp_path,token='test',api_only=True,telegram_client=network),headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        assert client.put('/api/v3/telegram/profiles/personal',json={'configuration_ref':'one'}).status_code==200
        body=dict(source_locator='@fixture',folder='Inbox',access={'kind':'client','profile':'personal'})
        created=client.post('/api/v3/telegram/connect',json=body).json()['feed_id']
        assert client.post('/api/v3/telegram/connect',json=body,headers={'X-Oko-Action':'duplicate'}).status_code==409
        duplicate=next(p for p in client.get('/api/v3/status').json()['problems']['current'] if p['command_id']=='duplicate')
        assert duplicate['code']=='duplicate_source'
        assert duplicate['affected']==[{'kind':'feed','feed_id':created}]
        assert duplicate['recovery']==[]
        network.user=202
        assert client.post('/api/v3/telegram/connect',json=dict(body,source_locator='@different'),headers={'X-Oko-Action':'identity'}).status_code==409
        conflict=next(p for p in client.get('/api/v3/status').json()['problems']['current'] if p['command_id']=='identity')
        assert conflict['code']=='account_identity_mismatch'
        assert conflict['recovery']==[{'kind':'configure_telegram_profile','role':'personal'}]
        assert not any(r['kind']=='connect_source' for r in conflict['recovery'])


def test_unmapped_legacy_alias_has_no_role_and_deletion_finishes_it_as_irrelevant(tmp_path):
    from tests.support.subscriptions import saved_sources
    saved_sources(tmp_path,[('https://t.me/fixture','Legacy','Saved')])
    with sqlite3.connect(tmp_path/'state.sqlite') as db:
        db.execute("UPDATE feeds SET transport='telegram',connection='connected',telegram_account='old-alias',telegram_peer=-10042")
    with TestClient(create_app(tmp_path,token='test',api_only=True),headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        p=next(p for p in client.get('/api/v3/status').json()['problems']['current'] if p['code']=='profile_not_configured')
        assert p['recovery']==[]
        assert all(r['kind']=='feed' for r in p['affected'])
        feed=p['affected'][0]['feed_id']
        assert client.delete('/api/v3/feeds/'+feed).status_code==200
        finished=wait_for(client,lambda:next((r for r in client.get('/api/v3/status').json()['problems']['recent'] if r['id']==p['id']),None))
        assert finished['outcome']=='irrelevant'
        assert finished['created_at']==p['created_at']


def test_unknown_collection_outcome_never_offers_a_mutating_recovery(tmp_path):
    with TestClient(create_app(tmp_path,token='test',api_only=True),headers=HEADERS) as client:
        response=client.post('/api/v3/command-observations',json={'id':'unknown-collect','context':{'operation':'collect','target':{'scope':'all'}},'result':'unknown'})
        assert response.status_code==200,response.text
        p=next(p for p in client.get('/api/v3/status').json()['problems']['current'] if p['command_id']=='unknown-collect')
        assert p['code']=='command_outcome_unknown'
        assert p['recovery']==[]
        assert client.get('/api/v3/commands/unknown-collect').json()['outcome']=='unknown'
