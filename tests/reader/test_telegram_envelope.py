"""Invalid Telegram cursor in its owning Feed and reader HTTP."""
from tests.support.storage import database
import json
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
    with database(tmp_path) as db:
        db.execute('UPDATE feeds SET cursor = %s WHERE id = %s',(incompatible,feed))
        db.execute("UPDATE jobs SET status='pending',next=0 WHERE kind='discover'")
    with TestClient(create_app(tmp_path,token='test',api_only=True,telegram_client=telegram),headers=headers) as client:
        deadline = time.monotonic()+2
        while time.monotonic()<deadline:
            state = client.get('/api/v3/status').json()
            if 'Несовместимый курсор Telegram' in str(state):
                break
            time.sleep(.02)
        else:
            raise AssertionError(state)
        assert client.get('/api/v3/materials/'+material).status_code == 200
    with database(tmp_path) as db:
        assert db.execute('SELECT cursor FROM feeds WHERE id = %s',(feed,)).fetchone()[0] == incompatible
