"""Single-feed HTTP setup and explicit pre-history database fixtures."""
import json
from datetime import date, timedelta
from pathlib import Path


def add_source(client, url, title='Источник', folder='Без папки'):
    client.post('/api/tabs/setup/heartbeat')
    checked = client.post('/api/v3/feeds/verify', json={'url':url})
    assert checked.status_code == 200, checked.text
    value = checked.json()
    if value['state'] == 'existing':
        client.post('/api/tabs/setup/close')
        return value['existing_feed']
    result = client.post('/api/v3/feeds', json=dict(url=value['candidate']['url'], verification=value['verification'], title=title, folder=folder,
        initial_history={'mode':'date','date':str(date.today()-timedelta(days=1)), 'timezone':'UTC'}))
    assert result.status_code == 200, result.text
    client.post('/api/tabs/setup/close')
    return result.json()


def add_sources(client, rows):
    for url, title, folder in rows:
        add_source(client, url, title, folder)
    return client.get('/api/subscriptions')


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
