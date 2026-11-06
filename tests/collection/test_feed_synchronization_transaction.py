"""Feed configuration participates in its caller's transaction."""
import pytest

from oko.storage.transaction import transaction
from tests.support.storage import Store
from tests.support.subscriptions import configured_sources


def test_feed_configuration_is_rolled_back_with_caller(tmp_path):
    configured_sources(tmp_path, [('https://example.test/feed', 'Feed', 'Folder')])
    store = Store(tmp_path, lambda _: None)
    try:
        feed = store.subscriptions.feeds()[0]['id']
        with transaction(store.db):
            store.db.execute('UPDATE feeds SET enabled = 0 WHERE id = %s', (feed,))

        with pytest.raises(RuntimeError, match='owning transaction'):
            store.publications.sync_feeds([feed])

        with pytest.raises(ValueError, match='abort action'):
            with transaction(store.db):
                store.publications.sync_feeds([feed])
                assert store.db.execute('SELECT enabled FROM feeds WHERE id = %s', (feed,)).fetchone()[0] == 1
                raise ValueError('abort action')
        assert store.db.execute('SELECT enabled FROM feeds WHERE id = %s', (feed,)).fetchone()[0] == 0

        with transaction(store.db):
            store.publications.sync_feeds([feed])
        assert store.db.execute('SELECT enabled FROM feeds WHERE id = %s', (feed,)).fetchone()[0] == 1
    finally:
        store.db.close()
