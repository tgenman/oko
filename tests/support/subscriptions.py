"""Single-feed HTTP setup and explicit pre-history database fixtures."""
from tests.support.presence import presence, close_presence
import json
from datetime import date, timedelta
from pathlib import Path


def add_source(client, url, title='Источник', folder='Без папки'):
    presence(client, 'setup')
    checked = client.post('/api/v3/feeds/verify', json={'url':url})
    assert checked.status_code == 200, checked.text
    value = checked.json()
    if value['state'] == 'existing':
        close_presence(client, 'setup')
        return value['existing_feed']
    result = client.post('/api/v3/feeds', json=dict(url=value['candidate']['url'], verification=value['verification'], title=title, folder=folder,
        initial_history={'mode':'count','count':'20'}))
    assert result.status_code == 200, result.text
    close_presence(client, 'setup')
    return result.json()


def add_sources(client, rows):
    for url, title, folder in rows:
        add_source(client, url, title, folder)
    return client.get('/api/v3/subscriptions')


def saved_sources(root: Path, rows):
    """Initialize an old saved subscription before the app starts; no import API."""
    from oko.storage.store import Store
    from oko.subscriptions.subscriptions import identify, key
    store = Store(root, lambda _:None)
    with store.db:
        for url,title,folder in rows:
            identity,kind,connection=identify(url)
            folder_id=key('folder:'+folder)
            store.db.execute('INSERT OR IGNORE INTO folders VALUES (?,?,?)',(folder_id,folder,len(store.subscriptions.folders())))
            store.db.execute('INSERT OR IGNORE INTO feeds(id,identity,title,kind,folder_id,original_urls,connection,transport) VALUES (?,?,?,?,?,?,?,?)',
                (key(identity),identity,title,kind,folder_id,json.dumps([url]),connection,kind if kind in ('rss','youtube') else None))
    store.db.close()
