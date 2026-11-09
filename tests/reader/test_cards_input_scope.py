"""Card reads stay bounded while preserving reading and linked-media behaviour."""
import json
from uuid import uuid4

from fastapi.testclient import TestClient

from oko.storage.transaction import transaction
from tests.support.performance_seed import seed
from tests.support.reader import HEADERS
from tests.support.reading_cost import traced_app, tracing, wait_for_recovery


def test_cards_have_bounded_cost_and_preserve_read_and_hidden_filters(tmp_path):
    seed(tmp_path, 50)
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        wait_for_recovery(app)
        tracing(client, app)
        empty = client.get('/api/v3/cards', params={'feed': 'missing'})
        assert empty.status_code == 200 and empty.json()['items'] == []
        assert int(empty.headers['X-Test-SQL']) <= 4
        for limit in (1, 40, 200):
            response = client.get('/api/v3/cards', params={'limit': limit})
            assert response.status_code == 200, response.text
            cards = response.json()['items']
            assert len(cards) == limit
            assert int(response.headers['X-Test-SQL']) <= 9
            unchanged = client.get('/api/v3/cards', params={'limit': limit},
                                   headers={'If-None-Match': response.headers['ETag']})
            assert unchanged.status_code == 304
            assert int(unchanged.headers['X-Test-SQL']) <= 5
        assert len({card['feed_id'] for card in cards}) == 4
        viewed, hidden = cards[0]['id'], cards[1]['id']
        assert client.post('/api/v3/cards/read', json={'ids':[viewed]}).status_code == 204
        def hide():
            store = app.state.host.require_store()
            with transaction(store.db):
                store.db.execute("UPDATE publications SET status='filter_out' WHERE id=%s", (hidden,))
        client.portal.call(hide)
        def identities(**params):
            result = client.get('/api/v3/cards', params={'limit':200, **params})
            assert result.status_code == 200, result.text
            return {card['id'] for card in result.json()['items']}
        assert viewed not in identities() and hidden not in identities()
        assert viewed in identities(show_read='true')
        assert hidden in identities(show_hidden='true')


def test_linked_media_and_empty_children_do_not_create_n_plus_one(tmp_path):
    seed(tmp_path, 50)
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        wait_for_recovery(app)
        def link_children():
            store = app.state.host.require_store()
            parents = [row['id'] for row in store.db.execute(
                'SELECT id FROM publications WHERE feed_id IS NOT NULL ORDER BY published_at DESC,id LIMIT 200')]
            child_rows, links = [], []
            child, media = str(uuid4()), str(uuid4())
            key = 'media/' + child + '/preview.png'
            path = tmp_path / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'synthetic-image')
            for index, parent in enumerate(parents):
                identity = child if index == 0 else str(uuid4())
                content = {'blocks':[{'type':'media','role':'image','media_id':media}] if index == 0 else [], 'extra':{}}
                url = 'https://example.org/linked/' + identity
                child_rows.append((identity,url,url,json.dumps(content)))
                links.append((parent,identity))
            # Exercise the 400-ID split as well as empty media for most children.
            for _ in range(202):
                identity = str(uuid4())
                url = 'https://example.org/linked/' + identity
                child_rows.append((identity,url,url,json.dumps({'blocks':[], 'extra':{}})))
                links.append((parents[0],identity))
            with transaction(store.db):
                store.db.executemany("""INSERT INTO publications(id,url,platform,external_id,feed_id,title,content,status)
                    VALUES (%s,%s,'other_web',%s,NULL,'Linked',%s::jsonb,'ready')""",child_rows)
                store.db.executemany('INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES (%s,%s)',links)
                store.db.execute("""INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,mime_type,storage_key,status)
                    VALUES (%s,%s,'image','https://example.org/image','preview.png','image/png',%s,'ready')""",(media,child,key))
            return parents[0],child,media
        first, child, media = client.portal.call(link_children)
        tracing(client, app)
        costs = []
        for limit in (1, 40, 200):
            response = client.get('/api/v3/cards', params={'limit':limit})
            assert response.status_code == 200, response.text
            items = response.json()['items']
            assert len(items) == limit
            assert items[0]['id'] == first
            assert items[0]['preview_url'] == '/api/v3/materials/' + child + '/media/' + media
            costs.append(int(response.headers['X-Test-SQL']))
        assert max(costs) <= 11, costs
        assert max(costs) - min(costs) <= 1, costs
        tag = response.headers['ETag']
        def remove_preview():
            store = app.state.host.require_store()
            with transaction(store.db):
                store.db.execute("UPDATE media_files SET status='unavailable' WHERE id=%s", (media,))
                store.db.execute('UPDATE publications SET content=content WHERE id=%s', (child,))
        client.portal.call(remove_preview)
        changed = client.get('/api/v3/cards', params={'limit':200}, headers={'If-None-Match':tag})
        assert changed.status_code == 200
        assert changed.headers['ETag'] != tag
        assert changed.json()['items'][0]['preview_url'] is None
        assert client.get('/api/v3/cards', params={'limit':200},
                          headers={'If-None-Match':changed.headers['ETag']}).status_code == 304
