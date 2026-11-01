"""API v3 integration of the implemented Telegram collection modules."""
from tests.support.cards import read_all_cards
import httpx
import pytest

from tests.support.app import create_app
from tests.support.telegram import Telegram
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
        card=wait_for(client,lambda: next((card for card in read_all_cards(client, '/api/v3/cards') if card['feed_id']==saved_id and client.get('/api/v3/publication/'+card['id']).json()['availability']=='available'),None))
        assert 'source' not in card
        material=client.get('/api/v3/publication/'+card['id']).json()
        assert material['source']['access']==catalog['access']
        response=client.get('/api/v3/publication/'+card['id'])
        assert response.status_code==200,response.text
        text=response.json()
        assert text['source']['access']==catalog['access']
