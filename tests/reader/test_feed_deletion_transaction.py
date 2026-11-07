"""Removing a feed hides it atomically without discarding its publications."""
import pytest
from oko.storage.transaction import transaction
from tests.support.storage import Store


def test_feed_removal_rolls_back_with_reader_transaction(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,feed_id) VALUES ('p','rss','p','f')")
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                store.remove_feed('f')
                assert store.db.execute("SELECT deleted FROM feeds WHERE id='f'").fetchone()[0]
                raise ValueError('rollback')
        assert not store.db.execute("SELECT deleted FROM feeds WHERE id='f'").fetchone()[0]
        with transaction(store.db):
            store.remove_feed('f')
        assert store.db.execute("SELECT deleted FROM feeds WHERE id='f'").fetchone()[0]
        assert store.db.execute("SELECT feed_id FROM publications WHERE id='p'").fetchone()[0]=='f'
    finally:
        store.db.close()
