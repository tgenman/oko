import time


class Telegram:
    """Synthetic Telegram network boundary, never a personal account."""
    async def identity(self, account):
        return 10001

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
    response = connect_request(client, json=dict(account='fixture', peer=peer, folder=folder, auto_read=False))
    assert response.status_code == 200, response.json()
    return response.json()


def configure(client, account='fixture'):
    # Explicit synthetic user action at HTTP, not a default role in production.
    state = client.get('/api/v3/telegram/profiles').json()['personal']
    if state['state'] != 'ready' or state.get('configuration_ref') != account:
        return client.put('/api/v3/telegram/profiles/personal', json={'configuration_ref': account})
    return None


def connect_request(client, *, json):
    configure(client, json.get('account', 'fixture'))
    # Preserve existing imported/unconfigured feed IDs through explicit adoption.
    if 'feed_id' not in json:
        old = next((f for f in client.get('/api/subscriptions').json()['feeds'] if f['kind'] == 'telegram' and not f.get('telegram_peer')), None)
        if old:
            json = dict(json, feed_id=old['id'])
    return client.post('/api/telegram/connect', json=json)


def settled(client, count=1):
    for _ in range(200):
        cards = client.get('/api/cards').json()
        if len(cards) == count and all(c.get('complete') and not c.get('loading') for c in cards):
            return cards
        time.sleep(.02)
    raise AssertionError(client.get('/api/status').json())
