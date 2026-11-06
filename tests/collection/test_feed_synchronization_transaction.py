"""Feed configuration participates in its caller's transaction."""
import pytest

from oko.storage.transaction import transaction
from oko.storage.job_types import Job
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


def test_collection_attempt_joins_caller_transaction(tmp_path):
    configured_sources(tmp_path, [('https://example.test/feed', 'Feed', 'Folder')])
    store = Store(tmp_path, lambda _: None)
    try:
        feed = store.subscriptions.feeds()[0]['id']
        with transaction(store.db):
            identity = store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('discover',%s,'running',1) RETURNING id", (feed,)).fetchone()[0]
        job = Job(identity, 'discover', feed, 1)
        with pytest.raises(RuntimeError, match='owning transaction'):
            store.jobs.collection_start(job, feed)
        with pytest.raises(ValueError, match='abort action'):
            with transaction(store.db):
                store.jobs.collection_start(job, feed)
                assert store.db.execute('SELECT collection_attempt FROM jobs WHERE id=%s', (identity,)).fetchone()[0]
                raise ValueError('abort action')
        assert store.db.execute('SELECT collection_attempt FROM jobs WHERE id=%s', (identity,)).fetchone()[0] is None
        with transaction(store.db):
            attempt = store.jobs.collection_start(job, feed)
        with pytest.raises(ValueError, match='abort result'):
            with transaction(store.db):
                store.db.execute("UPDATE jobs SET status='done' WHERE id=%s", (identity,))
                store.jobs.collection_finish(job, feed, attempt, 1., {'error': None, 'requests': [], 'request_count': 0})
                raise ValueError('abort result')
        row=store.db.execute('SELECT status,collection_attempt FROM jobs WHERE id=%s', (identity,)).fetchone()
        assert row['status']=='running' and row['collection_attempt']['finished_at'] is None
    finally:
        store.db.close()


def test_discovery_scheduling_rolls_back_with_caller(tmp_path):
    configured_sources(tmp_path, [('https://example.test/feed', 'Feed', 'Folder')])
    store = Store(tmp_path, lambda _: None)
    try:
        feed = store.subscriptions.feeds()[0]['id']
        with pytest.raises(RuntimeError, match='owning transaction'):
            store.jobs.enqueue_discovery(feed)
        with pytest.raises(ValueError, match='abort scheduling'):
            with transaction(store.db):
                store.jobs.enqueue_discovery(feed)
                assert store.db.execute("SELECT id FROM jobs WHERE kind='discover' AND item=%s", (feed,)).fetchone()
                raise ValueError('abort scheduling')
        assert store.db.execute("SELECT id FROM jobs WHERE kind='discover' AND item=%s", (feed,)).fetchone() is None
    finally:
        store.db.close()
