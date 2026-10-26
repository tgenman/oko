"""Single-feed HTTP setup and explicit pre-history database fixtures."""
from tests.support.presence import presence, close_presence
import json
from datetime import date, timedelta
from pathlib import Path


def add_source(client, url, title='Источник', folder='Без папки', *, count=None):
    presence(client, 'setup')
    checked = client.post('/api/v3/feeds/verify', json={'url':url})
    assert checked.status_code == 200, checked.text
    value = checked.json()
    if value['state'] == 'existing':
        close_presence(client, 'setup')
        return value['existing_feed']
    chosen=count if count is not None else (1 if value['candidate']['kind']=='youtube' else 20)
    result = client.post('/api/v3/feeds', json=dict(url=value['candidate']['url'], verification=value['verification'], title=title, folder=folder,
        initial_history={'mode':'count','count':str(chosen)}))
    assert result.status_code == 200, result.text
    close_presence(client, 'setup')
    return result.json()


def add_sources(client, rows):
    for url, title, folder in rows:
        add_source(client, url, title, folder)
    return client.get('/api/v3/subscriptions')


def configured_sources(root: Path,rows, *, history=True):
    """Closed current subscription fixtures; no network verification is performed."""
    from tests.support.storage import Store
    from oko.subscriptions.subscriptions import AddFeed
    from oko.subscriptions.history import InitialHistory
    store=Store(root,lambda _:None)
    try:
        for url,title,folder in rows:
            store.subscriptions.add_feed(AddFeed(url=url,title=title,folder=folder,verification='synthetic',initial_history=InitialHistory(count=1 if 'youtube.com' in url else 20)))
        if not history:
            with store.db:
                store.db.execute('DELETE FROM initial_history')
        store.publications.sync_feeds([feed['id'] for feed in store.subscriptions.feeds()])
    finally:
        store.db.close()
