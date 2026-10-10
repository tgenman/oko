import time


class Telegram:
    """Synthetic Telegram network boundary, never a personal account."""
    async def accounts(self):
        return ['fixture']

    async def info(self, account, peer):
        return dict(peer=-1001234567890, title='Канал', kind='channel', forum=False)

    async def page(self, account, peer, after, since):
        return dict(messages=[dict(id=11, date=time.time()-60, text='Текст канала', group=None, media=None)], more=False, gap=None)

    async def messages(self, account, peer, ids):
        return [dict(id=11, date=time.time()-60, text='Текст канала', group=None, media=None)]

    async def download(self, account, peer, id):
        return b'media'

    async def read_state(self, account, peer, since):
        return dict(read_max=0, initial=0)

    async def read_window(self, account, peer, after, until):
        page = await self.page(account, peer, 0, 0)
        return dict(ids=[m['id'] for m in page['messages'] if after < m['id'] <= until], more=False)

    async def acknowledge(self, account, peer, max_id):
        pass

    async def close(self):
        pass


def connect(client, peer='@fixture', folder='Новости'):
    response = client.post('/api/telegram/connect', json=dict(account='fixture', peer=peer, folder=folder, auto_read=False))
    assert response.status_code == 200, response.json()
    return response.json()


def settled(client, count=1):
    for _ in range(200):
        cards = client.get('/api/cards').json()
        if len(cards) == count and all(c.get('complete') and not c.get('loading') for c in cards):
            return cards
        time.sleep(.02)
    raise AssertionError(client.get('/api/status').json())
