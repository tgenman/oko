"""Feed, publications and Jobs finish deletion as one application action."""
import json

import pytest

from oko.domain.job import Job
from oko.services.feed_deletion import FeedDeletion
from oko.storage.transaction import transaction
from tests.support.storage import Store


def test_feed_deletion_rolls_back_across_all_repositories(tmp_path,postgres_url,monkeypatch):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','Feed','default','rss','rss','deleting',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('p','rss','p')")
            store.db.execute("INSERT INTO jobs(kind,item,status,payload) VALUES ('delete','p','done',%s::jsonb)",(json.dumps({'cleanup_feed':'f'}),))
            identity=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('feed-delete','f','running',1) RETURNING id").fetchone()[0]
        job=Job(identity,'feed-delete','f',1)
        action=FeedDeletion(store.jobs,store.publications,store.subscriptions)
        remove=store.subscriptions.remove_cleaned_feed

        def fail_after_feed_removal(feed):
            remove(feed)
            raise RuntimeError('synthetic feed deletion failure')

        monkeypatch.setattr(store.subscriptions,'remove_cleaned_feed',fail_after_feed_removal)
        with pytest.raises(RuntimeError,match='synthetic feed deletion failure'):
            with transaction(store.db):
                action.finish(job)
        assert store.db.execute("SELECT 1 FROM feeds WHERE id='f'").fetchone()
        assert store.db.execute("SELECT 1 FROM publications WHERE id='p'").fetchone()
        assert store.db.execute("SELECT count(*) FROM jobs WHERE item IN ('f','p')").fetchone()[0]==2

        monkeypatch.setattr(store.subscriptions,'remove_cleaned_feed',remove)
        with transaction(store.db):
            assert action.finish(job)
        assert store.db.execute("SELECT 1 FROM feeds WHERE id='f'").fetchone() is None
        assert store.db.execute("SELECT 1 FROM publications WHERE id='p'").fetchone() is None
        assert store.db.execute("SELECT 1 FROM jobs WHERE item IN ('f','p')").fetchone() is None
    finally:
        store.db.close()
