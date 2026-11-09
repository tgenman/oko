"""Conditional reads keep the representation and invalidate older validators."""
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from tests.support.reader import HEADERS
from tests.support.reading_cost import library, traced_app, wait_for_recovery


@pytest.mark.parametrize('kind', ['navigation', 'publication'])
def test_conditional_representation_and_previous_canonical_tag(tmp_path, kind):
    feeds, items = library(tmp_path, [1])
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        wait_for_recovery(app)
        if kind == 'publication':
            def seed_metadata():
                store = app.state.host.require_store()
                with store.db:
                    store.db.execute('UPDATE publications SET processing_metadata=%s WHERE id=%s',
                                     ('{"z_test":1,"a_test":2}', items[0]))
            client.portal.call(seed_metadata)
        storage = client.get('/api/v3/navigation').headers['X-Oko-Storage-Id']
        path = '/api/v3/navigation' if kind == 'navigation' else '/api/v3/publication/' + items[0]
        first = client.get(path)
        assert first.status_code == 200, first.text
        tag = first.headers['ETag']
        assert not tag.startswith('W/')
        repeated = client.get(path)
        assert repeated.status_code == 200
        assert repeated.headers['ETag'] == tag and repeated.content == first.content
        conditional = client.get(path, headers={'If-None-Match':tag})
        assert conditional.status_code == 304 and conditional.content == b''

        # Older releases hashed sorted JSON while returning JSONResponse bytes.
        # If the order differs, the old validator must fetch the bytes once.
        canonical = json.dumps(first.json(), ensure_ascii=False, sort_keys=True, separators=(',',':')).encode()
        previous = '"' + hashlib.sha256(storage.encode()+b'\0'+canonical).hexdigest() + '"'
        upgraded = client.get(path, headers={'If-None-Match':previous})
        if canonical == first.content:
            assert previous == tag and upgraded.status_code == 304
        else:
            assert previous != tag
            assert upgraded.status_code == 200 and upgraded.content == first.content
        if kind == 'publication':
            assert canonical != first.content  # preserves metadata key order

        if kind == 'navigation':
            assert client.patch('/api/v3/feeds/' + feeds[0], json={'title':'Changed feed'}).status_code == 200
        else:
            def change_title():
                store = app.state.host.require_store()
                with store.db:
                    store.db.execute('UPDATE publications SET title=%s WHERE id=%s', ('Changed publication', items[0]))
            client.portal.call(change_title)
        changed = client.get(path, headers={'If-None-Match':tag})
        assert changed.status_code == 200 and changed.headers['ETag'] != tag
        assert changed.content != first.content
