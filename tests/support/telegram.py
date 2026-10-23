from datetime import date, timedelta
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

    async def history_page(self, account, peer, upper, before, since):
        after=0
        rows=[]
        while True:
            batch=await self.page(account,peer,after,since)
            rows.extend(batch['messages'])
            if not batch['more']:
                break
            following=max([after]+[row['id'] for row in batch['messages']])
            assert following>after, 'Synthetic history page must advance'
            after=following
        upper=upper or max([0]+[row['id'] for row in rows])
        rows=[row for row in rows if row['id']<=upper and (not before or row['id']<before)]
        return dict(messages=rows,upper=upper,next_before=min([row['id'] for row in rows],default=before),more=False)

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
    response = connect_request(client, json=dict(account='fixture', peer=peer, folder=folder, auto_read=False, initial_history={'mode':'count','count':20}))
    assert response.status_code == 200, response.json()
    feed_id = response.json()['feed_id']
    return next(feed for feed in client.get('/api/v3/subscriptions').json()['feeds'] if feed['id'] == feed_id)


def configure(client, account='fixture'):
    # Explicit synthetic user action at HTTP, not a default role in production.
    state = client.get('/api/v3/telegram/profiles').json()['personal']
    if state['state'] != 'ready' or state.get('configuration_ref') != account:
        return client.put('/api/v3/telegram/profiles/personal', json={'configuration_ref': account})
    return None


def connect_request(client, *, json):
    configure(client, json.get('account', 'fixture'))
    payload = dict(source_locator=json['peer'], folder=json.get('folder', 'Новости'),
                   access={'kind':'client','profile':'personal'}, auto_read=json.get('auto_read',False))
    payload.update({key:value for key,value in json.items() if key not in {'account','peer','folder','auto_read','initial_history'}})
    if 'initial_history' in json:
        selection = dict(json['initial_history'])
        if selection.get('mode') == 'count' and type(selection.get('count')) is int:
            selection['count'] = str(selection['count'])
        payload['initial_history'] = selection
    return client.post('/api/v3/telegram/connect', json=payload)


def settled(client, count=1, timeout=4):
    deadline = time.monotonic()+timeout
    while time.monotonic()<deadline:
        cards = client.get('/api/v3/cards').json()
        if len(cards) == count and all((c.get('availability') == 'available') and not c.get('loading') for c in cards):
            return cards
        time.sleep(.02)
    raise AssertionError(client.get('/api/v3/status').json())


def connected_feed(client, response):
    assert response.status_code == 200, response.text
    identity=response.json()['feed_id']
    return next(feed for feed in client.get('/api/v3/subscriptions').json()['feeds'] if feed['id']==identity)


def connected_feed(client, response):
    assert response.status_code == 200, response.text
    identity=response.json()['feed_id']
    return next(feed for feed in client.get('/api/v3/subscriptions').json()['feeds'] if feed['id']==identity)


def configure_fast_web(client):
    """Explicit fast transport in functional fixtures; budget tests keep their own rate."""
    response = client.patch('/api/v3/settings/oko/global', json={'telegram_http_rps':10})
    assert response.status_code == 200, response.text
