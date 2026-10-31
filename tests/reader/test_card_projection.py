"""Card reads must not construct the opened material's mutable document."""
from fastapi.testclient import TestClient
from tests.support.reader import HEADERS
from tests.support.worker_library import worker_library
from tests.support.app import create_app
from oko.storage.store import Store


def test_card_does_not_construct_opened_material(tmp_path, monkeypatch):
    store, item, _, _ = worker_library(tmp_path, text='<p>Useful text</p>' * 2000)
    store.set_work_enabled(False)
    store.db.close()
    app = create_app(tmp_path, token='test', api_only=True)
    with TestClient(app, headers=HEADERS) as client:
        original = Store.original
        opened = []
        def observed(self, identity):
            opened.append(identity)
            return original(self, identity)
        monkeypatch.setattr(Store, 'original', observed)
        response = client.get('/api/v3/cards')
        assert response.status_code == 200
        card = next(card for card in response.json()['items'] if card['id']==item)
        assert 'snippet' not in card and 'text' not in card
        assert opened == [], 'Card read constructed the full opened material'
        response = client.get('/api/v3/publication/' + item)
        assert response.status_code == 200
        assert response.json()['id'] == item
        assert item in opened


def test_card_keeps_linked_preview_without_mutating_opened_material(tmp_path):
    import json
    from oko.subscriptions.subscriptions import Overrides
    store, item, _, _ = worker_library(tmp_path, text='Own text')
    store.set_work_enabled(False)
    store.subscriptions.update_settings('oko', 'global', Overrides(prompt_filters='', theses=False))
    store.settings_changed()
    with store.db:
        row = dict(store.db.execute('SELECT * FROM publications WHERE id=%s', (item,)).fetchone())
        for derived in ('source_deleted','incomplete_group_ready','incomplete_group_unknown','incomplete_group_conflict'):
            row.pop(derived)
        content = json.loads(row['content'])
        content['blocks'] = [dict(type='text', role='body', text='Linked body')]
        content['extra']['_reader'].update(title='Article', changed=True,
            attachments=[dict(name='image.png', kind='image', mime='image/png')],
            _media_urls={'image.png': '/api/v3/materials/child/media/image.png'})
        child = dict(row, id='child', source_id='child', external_id='child', title='Article',
                     url='https://example.org/article', content=json.dumps(content))
        fields = list(child)
        store.db.execute('INSERT INTO publications (' + ','.join(fields) + ') VALUES (' + ','.join('%s' for _ in fields) + ')', tuple(child.values()))
        store.db.execute('INSERT INTO publication_links VALUES (%s,%s)', (item, 'child'))
    store.db.close()
    with TestClient(create_app(tmp_path, token='test', api_only=True), headers=HEADERS) as client:
        response = client.get('/api/v3/cards')
        assert response.status_code == 200
        card = next(card for card in response.json()['items'] if card['id']==item)
        assert 'snippet' not in card and 'text' not in card
        assert card['changed'] is True
        assert card['preview_url'] == '/api/v3/materials/child/media/image.png'
        opened = client.get('/api/v3/publication/'+item).json()
        material = opened
        assert 'Linked body' in material['text']
        repeated = client.get('/api/v3/cards').json()
        assert next(c for c in repeated['items'] if c['id']==item) == card


def test_cards_page_keeps_membership_and_card_in_one_snapshot(tmp_path, postgres_url):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    import psycopg

    store, item, _, _ = worker_library(tmp_path, database_url=postgres_url)
    store.set_work_enabled(False)
    store.db.close()
    app = create_app(tmp_path, token='test', api_only=True, database_url=postgres_url)
    with TestClient(app, headers=HEADERS) as client, psycopg.connect(postgres_url) as writer:
        initial = client.get('/api/v3/cards')
        assert initial.status_code == 200, initial.text
        assert initial.json()['total'] == 1
        assert [card['id'] for card in initial.json()['items']] == [item]
        entered, release = threading.Event(), threading.Event()
        def trace(query):
            if ' AS reader_hidden FROM publications i' in query and not entered.is_set():
                entered.set()
                assert release.wait(5)
        client.portal.call(lambda: app.state.host.require_store().db.set_trace_callback(trace))
        try:
            with ThreadPoolExecutor() as pool:
                pending = pool.submit(client.get, '/api/v3/cards')
                assert entered.wait(5)
                writer.execute('DELETE FROM feed_publications WHERE publication_id=%s', (item,))
                writer.commit()
                release.set()
                result = pending.result(5)
            assert result.status_code == 200, result.text
            assert result.json()['total'] == 1
            assert [card['id'] for card in result.json()['items']] == [item]
        finally:
            release.set()
            client.portal.call(lambda: app.state.host.require_store().db.set_trace_callback(None))
        assert client.get('/api/v3/cards').json()['items'] == []
