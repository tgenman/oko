"""Telegram headings preserve the first line, including older saved publications."""
from tests.support.cards import read_all_cards
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.support.app import create_app
from tests.support.telegram import Telegram, connect, settled
from tests.support.telegram_web import page

URL = 'https://example.test/news/' + 'long-path-' * 25
FIRST_LINE = f'[Bloomberg]({URL}): ' + 'Полный заголовок новости ' * 12 + 'конец заголовка'
TEXT = FIRST_LINE + '\n\nСледующий абзац публикации.'


class LongPost(Telegram):
    async def page(self, account, peer, after, since):
        return dict(messages=await self.messages(account, peer, [11]), more=False, gap=None)

    async def messages(self, account, peer, ids):
        return [dict(id=11, date=time.time()-60, text=TEXT, group=None, media=None)]


@pytest.mark.parametrize('access', ['client', 'web'])
def test_full_telegram_heading_in_list_and_opened_material(tmp_path, access):
    raw = page([(11, f'<a href="{URL}">Bloomberg</a>: ' + 'Полный заголовок новости ' * 12 + 'конец заголовка<br><br>Следующий абзац публикации.')])
    options = dict(token='test', api_only=True, telegram_client=LongPost(),
                   rss_transport=httpx.MockTransport(lambda request: httpx.Response(200, text=raw)))
    headers = {'Authorization': 'Bearer test'}
    with TestClient(create_app(tmp_path, **options), headers=headers) as client:
        if access == 'client':
            connect(client)
        else:
            response = client.post('/api/v3/telegram/connect', json=dict(source_locator='@fixture', folder='Новости'))
            assert response.status_code == 200, response.text
        card = settled(client)[0]
        identity = card['id']
        version = client.get('/api/v3/publication/' + identity).json()['revision_id']
    with TestClient(create_app(tmp_path, **options), headers=headers) as client:
        card = read_all_cards(client, '/api/v3/cards')[0]
        material = client.get('/api/v3/publication/' + identity).json()
        assert card['title'] == FIRST_LINE
        assert material['revision_id'] == version
        assert material['title'] == FIRST_LINE
        assert material['text'] == TEXT
        assert not card['changed']
