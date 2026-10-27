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
        response = client.post('/api/v3/reading/check', json=dict(storage_id=None, resources=[
            dict(kind='card', id=item, known_version=None)]))
        assert response.status_code == 200
        card = response.json()['resources'][0]
        assert card['state'] == 'changed'
        assert card['data']['snippet'] == ('Useful text ' * 2000).strip()[:500]
        assert opened == [], 'Card read constructed the full opened material'
        response = client.get('/api/v3/reading/materials/' + item)
        assert response.status_code == 200
        assert any(r['kind'] == 'material' and r['state'] == 'changed' for r in response.json()['resources'])
        assert item in opened


def test_card_keeps_linked_preview_without_mutating_opened_material(tmp_path):
    import json
    from oko.subscriptions.subscriptions import Overrides
    store, item, _, _ = worker_library(tmp_path, text='Own text')
    store.set_work_enabled(False)
    store.subscriptions.update_settings('oko', 'global', Overrides(prompt_filters='', theses=False))
    store.settings_changed()
    with store.db:
        row = dict(store.db.execute('SELECT * FROM items WHERE id=%s', (item,)).fetchone())
        content = json.loads(row['content'])
        content['blocks'] = [dict(type='text', role='body', text='Linked body')]
        content['extra']['_reader'].update(title='Article', changed=True,
            attachments=[dict(name='image.png', kind='image', mime='image/png')],
            _media_urls={'image.png': '/api/v3/materials/child/media/image.png'})
        child = dict(row, id='child', source_id='child', external_id='child', title='Article',
                     url='https://example.org/article', content=json.dumps(content))
        fields = list(child)
        store.db.execute('INSERT INTO items (' + ','.join(fields) + ') VALUES (' + ','.join('%s' for _ in fields) + ')', tuple(child.values()))
        store.db.execute('INSERT INTO publication_links VALUES (%s,%s)', (item, 'child'))
    store.db.close()
    with TestClient(create_app(tmp_path, token='test', api_only=True), headers=HEADERS) as client:
        request = dict(storage_id=None, resources=[dict(kind=kind, id=item, known_version=None) for kind in ('card', 'material')])
        response = client.post('/api/v3/reading/check', json=request)
        assert response.status_code == 200
        resources = {r['kind']: r for r in response.json()['resources']}
        assert all(r['state'] == 'changed' for r in resources.values())
        card = resources['card']['data']
        assert card['snippet'] == 'Own text --- # Article Исходник: https://example.org/article Linked body'
        assert card['changed'] is True
        assert card['preview_url'] == '/api/v3/materials/child/media/image.png'
        assert 'Linked body' in resources['material']['data']['content']['text']
        assert card['revision_id'] == resources['material']['data']['content']['revision_id']
        repeated = client.post('/api/v3/reading/check', json=request).json()
        assert next(r['data'] for r in repeated['resources'] if r['kind'] == 'card') == card
