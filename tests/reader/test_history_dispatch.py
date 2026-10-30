from tests.support.storage import analyze as analyze_schema
"""History admission preserves dispatch rules without traversing foreign archives."""
import json
from contextlib import nullcontext
import psycopg

import pytest

from tests.support.storage import Store
from tests.support.postgres_work import measure_reads
from tests.support.library_templates import templates


@pytest.mark.parametrize('transport,mode,paused,complete,blocked', [
    ('youtube', 'count', 1, 1, True),
    ('youtube', 'count', 0, 0, True),
    ('youtube', 'count', 0, 1, False),
    ('youtube', 'date', 0, 0, False),
    ('rss', 'date', 0, 0, True),
    ('rss', 'date', 0, 1, False),
    ('telegram', 'date', 1, 1, True),
    ('telegram', 'date', 0, 0, False),
    ('rss', None, None, None, False),
])
def test_history_admission_only_blocks_enrolled_fetches(tmp_path, transport, mode, paused, complete, blocked):
    store = Store(tmp_path, lambda _: None)
    try:
        store.subscriptions.ensure_demo()
        with store.db:
            store.db.execute('UPDATE feeds SET transport = %s WHERE id = %s', (transport, 'demo'))
            store.db.executemany('INSERT INTO publications ( id ) VALUES ( %s )', [('enrolled',), ('live',)])
            store.db.executemany('INSERT INTO feed_publications (feed_id, publication_id, is_primary) VALUES (%s, %s, true)', [('demo', 'enrolled'), ('demo', 'live')])
            store.db.execute('INSERT INTO initial_history ( feed , selection , paused , scan_complete ) VALUES ( %s , %s , %s , %s )',
                             ('demo', json.dumps(dict(mode=mode, cancelled=bool(paused))), paused, complete))
            store.db.execute("INSERT INTO history_members(feed,item) VALUES ('demo','enrolled')")
            # AI and cleanup keep their admission even for an enrolled publication.
            for kind, item, deadline in [('fetch', 'enrolled', 0), ('fetch', 'live', 0),
                                         ('filter', 'enrolled', 0), ('delete', 'enrolled', 0),
                                         ('fetch', 'later', 200)]:
                store.db.execute('INSERT INTO jobs ( kind , item , next ) VALUES ( %s , %s , %s )', (kind, item, deadline))
        ai = store.jobs.next_job(100, ai_only=True)
        assert ai and (ai.kind, ai.item) == ('filter', 'enrolled')
        deletion = store.jobs.next_job(100)
        assert deletion and (deletion.kind, deletion.item) == ('delete', 'enrolled')
        ready = []
        while job := store.jobs.next_job(100):
            ready.append(job.item)
        assert ready == (['live'] if blocked else ['enrolled', 'live'])
        assert store.db.execute("SELECT status FROM jobs WHERE item='later'").fetchone()[0] == 'pending'
    finally:
        store.db.close()


@pytest.mark.parametrize('item,member,paused,expected', [
    ('live', None, 1, False),
    ('live', 'other', 1, True),
    (None, 'other', 1, False),
])
def test_postgres_rejects_null_memberships_and_preserves_dispatch(tmp_path, item, member, paused, expected):
    store = Store(tmp_path, lambda _: None)
    try:
        store.subscriptions.ensure_demo()
        invalid = item is None or member is None
        with pytest.raises(psycopg.errors.NotNullViolation) if invalid else nullcontext():
            with store.db:
                store.db.execute('INSERT INTO publications ( id ) VALUES ( %s )', (item,))
                store.db.execute("INSERT INTO feed_publications (feed_id,publication_id,is_primary) VALUES ('demo',%s,true)", (item,))
                store.db.execute("INSERT INTO jobs ( kind , item ) VALUES ( 'fetch' , %s )", (item,))
                store.db.execute('INSERT INTO initial_history ( feed , selection , paused ) VALUES ( %s , %s , %s )',
                                 ('demo', json.dumps(dict(cancelled=bool(paused))), paused))
                store.db.execute("INSERT INTO history_members ( feed , item ) VALUES ( 'demo' , %s )", (member,))
        if invalid:
            assert store.db.execute('SELECT count(*) FROM publications').fetchone()[0] == 0
            return
        job = store.jobs.next_job(0)
        assert (job is not None) is expected
        if job:
            assert job.item == item
    finally:
        store.db.close()


@pytest.mark.parametrize('analyze', [False, True])
def test_foreign_cancelled_history_does_not_add_linear_dispatch_work(tmp_path, analyze):
    costs = []
    for count in (100, 10000):
        root = tmp_path / str(count)
        templates().copy(root, ('history-dispatch-archive', 10000), archive_seed)
        store = Store(root, lambda _: None)
        try:
            with store.db:
                store.db.executemany("INSERT INTO history_members ( feed , item , outcome ) VALUES ( 'archive' , %s , 'loaded' )",
                                     [(f'old-{i}',) for i in range(count)])
                store.db.execute("INSERT INTO publications(id) VALUES ('live')")
                store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('demo','live',true)")
                store.db.execute("INSERT INTO jobs(kind,item) VALUES ('fetch','live')")
                if analyze:
                    analyze_schema(store.db)
            assert store.db.execute("SELECT count(*) FROM items_read WHERE feed_id='archive' AND viewed=1").fetchone()[0] == 10000
            assert store.db.execute("SELECT count(*) FROM jobs WHERE kind='fetch' AND status='done'").fetchone()[0] == 10000
            assert store.db.execute("SELECT count(*) FROM history_members WHERE feed='archive'").fetchone()[0] == count
            with measure_reads(store.db) as work:
                job = store.jobs.next_job(0)
            assert job and (job.kind, job.item) == ('fetch', 'live')
            # Measure PostgreSQL read plans; writes still execute once. Hold
            # items/jobs fixed: their plans are outside history admission scope.
            costs.append(work.rows)
        finally:
            store.db.close()
    assert costs[1] <= costs[0] + 200, costs


def archive_seed(root):
    """Closed immutable base; each measurement owns its history and dispatch."""
    store = Store(root, lambda _: None)
    try:
        store.subscriptions.ensure_demo()
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection) VALUES ('archive','archive','Archive','default','rss','rss','connected')")
            store.db.execute("INSERT INTO initial_history(feed,selection,paused,scan_complete) VALUES ('archive','{\"mode\":\"count\",\"cancelled\":true}',1,1)")
            store.db.executemany("INSERT INTO publications ( id , viewed , platform , external_id ) VALUES ( %s , 1 , 'other_web' , %s )",
                                 [(f'old-{i}',f'old-{i}') for i in range(10000)])
            store.db.executemany("INSERT INTO feed_publications (feed_id,publication_id,is_primary,is_unviewed) VALUES ('archive',%s,true,false)", [(f'old-{i}',) for i in range(10000)])
            store.db.executemany("INSERT INTO jobs ( kind , item , status ) VALUES ( 'fetch' , %s , 'done' )",
                                 [(f'old-{i}',) for i in range(10000)])
    finally:
        store.db.close()
