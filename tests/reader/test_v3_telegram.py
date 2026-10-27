"""API v3 integration of the implemented Telegram collection modules."""
from tests.support.cards import read_all_cards
import httpx
import pytest

from tests.support.app import create_app
from tests.support.telegram import Telegram
from tests.support.telegram_web import page
from tests.support.v3 import TestClient

HEADERS = {'Authorization': 'Bearer test'}


class Accounts(Telegram):
    async def accounts(self):
        return ['one', 'two']

    async def identity(self, account):
        return {'one': 9007199254740993, 'two': 202}[account]

    async def history_page(self, account, peer, upper, before, since):
        result = await self.page(account, peer, 0, since)
        return dict(messages=result['messages'], upper=11, next_before=11, more=False)


@pytest.mark.parametrize('access', [None, {'kind':'web'}, {'kind':'client','profile':'personal'}, {'kind':'client','profile':'collection'}])
def test_closed_access_defaults_and_invalid_selection_have_no_feed_writes(tmp_path, access):
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text=page([(11, 'Public text')])))
    with TestClient(create_app(tmp_path, token='test', api_only=True, telegram_client=Accounts(), rss_transport=transport), headers=HEADERS) as client:
        assert client.put('/api/v3/telegram/profiles/personal', json={'configuration_ref':'one'}).status_code == 200
        assert client.put('/api/v3/telegram/profiles/collection', json={'configuration_ref':'two'}).status_code == 200
        body = {'source_locator':'@fixture', 'folder':'Telegram'}
        if access is not None:
            body['access'] = access
        for invalid in [None, {}, {'mode':'count'}, {'mode':'date','date':'2026-01-01'}]:
            result = client.post('/api/v3/telegram/connect', json=dict(body, initial_history=invalid))
            assert result.status_code == 422, result.text
            assert client.get('/api/v3/subscriptions').json()['feeds'] == []
        result = client.post('/api/v3/telegram/connect', json=body, headers={'X-Oko-Action':'new-telegram'})
        assert result.status_code == 200, result.text
        feed = client.get('/api/v3/subscriptions').json()['feeds'][0]
        assert feed['history']['selection'] == {'mode':'count','count':'20'}
        assert result.json()['feed_id'] == feed['id']
        assert 'cursor' not in feed['history']
        if access and access['kind'] == 'client':
            assert feed['auto_read'] is False
            assert feed['access']['role'] == access['profile']
            assert feed['access']['account_user_id'] == ('9007199254740993' if access['profile'] == 'personal' else '202')
        else:
            assert feed['access']['kind'] == 'web'
            assert 'auto_read' not in feed
        command = client.get('/api/v3/commands/new-telegram').json()
        assert command['outcome'] == 'succeeded'
        assert command['context'] == {'operation':'telegram_connect'}
        duplicate = client.post('/api/v3/telegram/connect', json=body)
        assert duplicate.status_code == 409
        assert duplicate.json()['code'] == 'duplicate_source'
        assert duplicate.json()['details']['existing_feeds'][0]['feed_id'] == feed['id']
        for mutation in [{'access':{'kind':'web'}}, {'telegram_account':'two'}, {'telegram_peer':'-1009'}]:
            result = client.patch('/api/v3/feeds/'+feed['id'], json=mutation)
            assert result.status_code in (409, 422), result.text


def test_confirmed_telegram_access_is_identical_in_catalog_composition_card_and_material(tmp_path):
    from tests.support.reader import wait_for
    with TestClient(create_app(tmp_path, token='test', api_only=True, telegram_client=Accounts()),headers=HEADERS) as client:
        assert client.put('/api/v3/telegram/profiles/personal',json={'configuration_ref':'one'}).status_code==200
        reply=client.post('/api/v3/telegram/connect',json={'source_locator':'@fixture','folder':'Inbox','access':{'kind':'client','profile':'personal'},'initial_history':{'mode':'count','count':'1'}})
        assert reply.status_code==200,reply.text
        saved_id=reply.json()['feed_id']
        catalog=next(feed for feed in client.get('/api/v3/subscriptions').json()['feeds'] if feed['id']==saved_id)
        assert catalog['access']['kind']=='client' and catalog['access']['role']=='personal'
        assert catalog['access']['account_user_id']=='9007199254740993'
        composition=next(row['feed'] for row in client.get('/api/v3/reading-composition').json()['feeds'] if row['feed']['id']==saved_id)
        assert composition['access']==catalog['access']
        assert composition['connection']==catalog['connection']=='ready'
        card=wait_for(client,lambda: next((card for card in read_all_cards(client, '/api/v3/cards') if card['feed_id']==saved_id and client.get('/api/v3/materials/'+card['id']).json()['availability']=='available'),None))
        assert 'source' not in card
        material=client.get('/api/v3/materials/'+card['id']).json()
        assert material['source']['access']==catalog['access']
        response=client.get('/api/v3/reading/materials/'+card['id'])
        assert response.status_code==200,response.text
        text=next(resource['data'] for resource in response.json()['resources'] if resource['kind']=='material')
        assert text['source']['access']==catalog['access']
