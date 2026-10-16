"""Telegram cursor compatibility through persisted state and reader HTTP."""
import json
import sqlite3
import time

from tests.support.app import create_app
from tests.support.telegram import Telegram, settled
from tests.support.v3 import TestClient


class HistoryTelegram(Telegram):
    async def history_page(self, account, peer, upper, before, since):
        page = await self.page(account, peer, 0, since)
        return dict(messages=page['messages'], upper=11, next_before=11, more=False)


def test_unknown_telegram_cursor_is_a_visible_compatibility_problem_without_reset(tmp_path):
    telegram = HistoryTelegram()
    headers = {'Authorization':'Bearer test'}
    with TestClient(create_app(tmp_path,token='test',api_only=True,telegram_client=telegram),headers=headers) as client:
        client.put('/api/v3/telegram/profiles/personal',json={'configuration_ref':'fixture'})
        result = client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox',
                    access={'kind':'client','profile':'personal'}))
        feed = result.json()['feed_id']
        material = settled(client)[0]['id']
    incompatible = json.dumps(dict(format_version=99,adapter='client',selection={},position={'after':123}))
    with sqlite3.connect(tmp_path/'state.sqlite') as db:
        db.execute('UPDATE state SET value=? WHERE key=?',(incompatible,'cursor:telegram:'+feed))
        db.execute("UPDATE jobs SET status='pending',next=0 WHERE kind='discover'")
    with TestClient(create_app(tmp_path,token='test',api_only=True,telegram_client=telegram),headers=headers) as client:
        deadline = time.monotonic()+2
        while time.monotonic()<deadline:
            state = client.get('/api/status').json()
            if 'Несовместимый курсор Telegram' in str(state):
                break
            time.sleep(.02)
        else:
            raise AssertionError(state)
        assert client.get('/api/materials/'+material).status_code == 200
    with sqlite3.connect(tmp_path/'state.sqlite') as db:
        assert db.execute('SELECT value FROM state WHERE key=?',('cursor:telegram:'+feed,)).fetchone()[0] == incompatible


def test_flat_client_cursor_upgrades_without_reenrolling_viewed_history(tmp_path):
    class Growing(HistoryTelegram):
        phase = 0
        def __init__(self):
            self.after = []
        def rows(self):
            return [dict(id=i,date=time.time()-60,text=str(i),group=None,media=None)
                    for i in ([11] if not self.phase else [11,12])]
        async def page(self,account,peer,after,since):
            self.after.append(after)
            return dict(messages=self.rows(),more=False)
        async def messages(self,account,peer,ids):
            return [row for row in self.rows() if row['id'] in ids]
    telegram = Growing()
    headers = {'Authorization':'Bearer test'}
    options = dict(token='test',api_only=True,telegram_client=telegram)
    with TestClient(create_app(tmp_path,**options),headers=headers) as client:
        client.put('/api/v3/telegram/profiles/personal',json={'configuration_ref':'fixture'})
        feed = client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Inbox',
                    access={'kind':'client','profile':'personal'},initial_history={'mode':'count','count':'1'})).json()['feed_id']
        old = settled(client)[0]['id']
        client.post('/api/materials/'+old+'/viewed')
    legacy = dict(after=11,since=0,upper=11,before=11,enrolled=1,lane='live',scan_complete=True,oldest=11,history_enroll=[])
    with sqlite3.connect(tmp_path/'state.sqlite') as db:
        db.execute('UPDATE state SET value=? WHERE key=?',(json.dumps(legacy),'cursor:telegram:'+feed))
        db.execute("UPDATE jobs SET status='pending',next=0 WHERE kind='discover'")
    telegram.phase = 1
    telegram.after.clear()
    with TestClient(create_app(tmp_path,**options),headers=headers) as client:
        cards = settled(client)
        assert cards[0]['members'] == [12]
        assert client.get('/api/materials/'+old).status_code == 404
        assert 11 in telegram.after
    with sqlite3.connect(tmp_path/'state.sqlite') as db:
        saved = json.loads(db.execute('SELECT value FROM state WHERE key=?',('cursor:telegram:'+feed,)).fetchone()[0])
        assert saved['format_version']==1 and saved['adapter']=='client'
        assert saved['position']['after']==12 and saved['selection']['scan_complete'] is True
