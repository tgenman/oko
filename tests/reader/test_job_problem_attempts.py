from tests.support.job_claim import claim_job
import json

import pytest

from oko.collection.adapters import AdapterErrorKind, AdapterIssue
from oko.storage.job_types import Job
from oko.storage.transaction import transaction
from tests.support.storage import Store


@pytest.mark.parametrize('kind', ['fetch', 'download_media', 'filter', 'theses'])
def test_failure_claim_and_stale_attempt_keep_one_problem(tmp_path, postgres_url, kind):
    store = Store(tmp_path, lambda _: None, database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('article','other_web','article')")
            store.db.execute("UPDATE publications SET feed_id='feed' WHERE id='article'")
            if kind in ('filter', 'theses'):
                store.db.execute("UPDATE publications SET status=%s WHERE id='article'", ('pending_filtering' if kind == 'filter' else 'pending_summary',))
            if kind == 'download_media':
                store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('file','article','image:1','https://example.test/file','file','pending_download')")
            item = 'file' if kind == 'download_media' else 'article'
            job_id = store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES (%s,%s,'running',1) RETURNING id", (kind, item)).fetchone()[0]
        old = Job(job_id, kind, item, 1)
        issue = AdapterIssue('test-failure', 'failure', AdapterErrorKind.temporary)
        with transaction(store.db):
            if kind == 'download_media':
                store.jobs.fail_media(old, issue)
            else:
                store.jobs.retry(old, 1, 'failure', issue=issue)
        problem = store.db.execute("SELECT id,body FROM reader_problems WHERE body::jsonb->>'operation'=%s AND finished IS NULL", ('producer:' + kind,)).fetchone()
        assert problem
        with transaction(store.db):
            store.db.execute('UPDATE jobs SET next=0 WHERE id=%s', (job_id,))
        def claim():
            return claim_job(store, 1, feeds=['feed'], media_only=kind == 'download_media',
                                       ai_only=kind in ('filter', 'theses'))
        with pytest.raises(RuntimeError, match='rollback'):
            with transaction(store.db):
                assert claim()
                assert json.loads(store.db.execute('SELECT body FROM reader_problems WHERE id=%s', (problem['id'],)).fetchone()[0])['state'] == 'retrying'
                raise RuntimeError('rollback')
        assert store.db.execute('SELECT status FROM jobs WHERE id=%s', (job_id,)).fetchone()[0] == 'pending'
        assert store.db.execute('SELECT body FROM reader_problems WHERE id=%s', (problem['id'],)).fetchone()[0] == problem['body']
        claimed = claim()
        assert claimed and claimed.generation > old.generation
        after = store.db.execute('SELECT id,body FROM reader_problems WHERE id=%s AND finished IS NULL', (problem['id'],)).fetchone()
        assert after and json.loads(after['body'])['state'] == 'retrying'
        with transaction(store.db):
            if kind == 'download_media':
                store.jobs.fail_media(old, issue)
            else:
                store.jobs.retry(old, 1, 'stale failure', issue=issue)
        assert store.db.execute('SELECT generation,status FROM jobs WHERE id=%s', (job_id,)).fetchone()[:] == (claimed.generation, 'running')
        assert store.db.execute('SELECT body FROM reader_problems WHERE id=%s', (problem['id'],)).fetchone()[0] == after['body']
    finally:
        store.db.close()
