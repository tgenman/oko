"""Content reuse is bounded by a real HTTP read; derived inputs cannot corrupt revisions."""
import json
import time
from collections import Counter

import pytest
from fastapi.testclient import TestClient

from tests.support.reader import HEADERS
from tests.support.reading_cost import traced_app, request_sql
from tests.support.worker_library import worker_library


def test_card_and_opened_read_decode_each_saved_content_once(tmp_path, monkeypatch, postgres_url):
    dsn = postgres_url
    store, parent, _, _ = worker_library(tmp_path, text='decoded-reading-own', database_url=dsn)
    store.set_work_enabled(False)
    store.processing.background = True
    with store.db:
        row = dict(store.db.execute('SELECT * FROM items WHERE id = %s', (parent,)).fetchone())
        content = json.loads(row['content'])
        content['blocks'][0]['text'] = 'decoded-reading-linked'
        linked = dict(row, id='linked', source_id='linked', external_id='linked',
                      platform='other_web', content=json.dumps(content))
        fields = list(linked)
        store.db.execute('INSERT INTO items('+','.join(fields)+') VALUES ('+','.join('%s' for _ in fields)+')', [linked[f] for f in fields])
        store.db.execute('INSERT INTO publication_links VALUES ( %s , %s )', (parent, 'linked'))
    store.db.close()
    loads = json.loads
    decoded = Counter()
    def observe(value, *args, **kwargs):
        if request_sql.get() is not None and isinstance(value, str) and value in (row['content'], linked['content']):
            decoded[value] += 1
        return loads(value, *args, **kwargs)
    monkeypatch.setattr(json, 'loads', observe)
    app = traced_app(tmp_path, database_url=dsn)
    with TestClient(app, headers=HEADERS) as client:
        deadline = time.monotonic()+20
        while app.state.host.execution._store is None or app.state.host.execution._store.processing.recovering:
            assert time.monotonic() < deadline
            time.sleep(.01)
        body = dict(storage_id=None, resources=[dict(kind='list', known_version=None, known_cards={},
                    options=dict(target=dict(scope='all'), sort='newest', show_hidden=False))])
        revisions = []
        for method, url, kwargs in [('POST', '/api/v3/reading/check', {'json': body}),
                                    ('GET', '/api/v3/reading/materials/'+parent, {}),
                                    ('POST', '/api/v3/reading/check', {'json': body})]:
            decoded.clear()
            response = client.request(method, url, **kwargs)
            assert response.status_code == 200
            resources = response.json()['resources']
            resource = next(r for r in resources if r['kind'] == ('card' if method == 'POST' else 'material'))
            assert resource['state'] == 'changed'
            data = resource['data']
            revisions.append(data['revision_id'] if method == 'POST' else data['content']['revision_id'])
            assert decoded[row['content']] == 1
            assert decoded[linked['content']] == 1
        assert len(set(revisions)) == 1


def test_derived_youtube_and_linked_content_does_not_mutate_saved_facts(tmp_path):
    from oko.storage import processing_input
    from oko.storage.reading import reading_snapshot

    store, parent, _, _ = worker_library(tmp_path, text='Own text')
    try:
        with store.db:
            row = dict(store.db.execute('SELECT * FROM items WHERE id = %s', (parent,)).fetchone())
            content = json.loads(row['content'])
            content['extra']['youtube'] = dict(captions='available', captions_digest='synthetic')
            content['blocks'].append(dict(type='media', role='subtitles', media_id='captions'))
            store.db.execute('UPDATE items SET content = %s , platform = %s WHERE id = %s', (json.dumps(content), 'youtube', parent))
            (tmp_path/'media'/'captions').write_text('Subtitle body')
            store.db.execute("INSERT INTO media_files ( id , publication_id , resource_key , source_locator , name , storage_key , status ) VALUES ( 'captions' , %s , 'captions' , 'synthetic' , 'captions.txt' , 'media/captions' , 'ready' )", (parent,))
            linked = dict(row, id='linked', source_id='linked', external_id='linked', platform='other_web')
            fields = list(linked)
            store.db.execute('INSERT INTO items('+','.join(fields)+') VALUES ('+','.join('%s' for _ in fields)+')', [linked[f] for f in fields])
            store.db.execute('INSERT INTO publication_links VALUES ( %s , %s )', (parent, 'linked'))
        expected = store.content_version(parent)
        for reverse in (False, True):
            with reading_snapshot():
                if reverse: store.original(parent)
                value = processing_input.content(store, parent)
                assert sum(b.get('text') == 'Subtitle body' for b in value['blocks']) == 1
                value['blocks'][0]['text'] = 'Caller changed a derived input'
                if not reverse: store.original(parent)
                again = processing_input.content(store, parent)
                assert again['blocks'][0]['text'] == 'Own text'
                assert sum(b.get('text') == 'Subtitle body' for b in again['blocks']) == 1
                assert store.content_version(parent) == expected
                assert store.content_version('linked') != expected
        (tmp_path/'media'/'captions').unlink()
        with reading_snapshot():
            assert 'Subtitle body' not in store.original(parent)[1]
            assert store.content_version(parent) == expected
        with store.db:
            content['blocks'][0]['text'] = 'Updated source'
            store.db.execute('UPDATE items SET content = %s WHERE id = %s', (json.dumps(content), parent))
        with reading_snapshot():
            assert store.content_version(parent) != expected
            assert 'Updated source' in store.original(parent)[1]
    finally:
        store.db.close()
