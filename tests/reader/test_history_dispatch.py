"""History admission preserves dispatch rules without traversing foreign archives."""
import json

import pytest

from oko.storage.store import Store


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
            store.db.execute('UPDATE feeds SET transport=? WHERE id=?', (transport, 'demo'))
            store.db.executemany('INSERT INTO items(id,feed_id) VALUES (?,?)', [('enrolled', 'demo'), ('live', 'demo')])
            store.db.execute('INSERT INTO initial_history(feed,selection,paused,scan_complete) VALUES (?,?,?,?)',
                             ('demo', json.dumps(dict(mode=mode, cancelled=bool(paused))), paused, complete))
            store.db.execute("INSERT INTO history_members(feed,item) VALUES ('demo','enrolled')")
            # AI and cleanup keep their admission even for an enrolled publication.
            for kind, item, deadline in [('fetch', 'enrolled', 0), ('fetch', 'live', 0),
                                         ('filter', 'enrolled', 0), ('delete', 'enrolled', 0),
                                         ('fetch', 'later', 200)]:
                store.db.execute('INSERT INTO jobs(kind,item,next) VALUES (?,?,?)', (kind, item, deadline))
        ai = store.next_job(100, ai_only=True)
        assert ai and (ai.kind, ai.item) == ('filter', 'enrolled')
        deletion = store.next_job(100)
        assert deletion and (deletion.kind, deletion.item) == ('delete', 'enrolled')
        ready = []
        while job := store.next_job(100):
            ready.append(job.item)
        assert ready == (['live'] if blocked else ['enrolled', 'live'])
        assert store.db.execute("SELECT status FROM jobs WHERE item='later'").fetchone()[0] == 'pending'
    finally:
        store.db.close()


@pytest.mark.parametrize('item,member,paused,expected', [
    ('live', None, 1, False),
    ('live', None, 0, True),
    ('live', 'other', 1, True),
    (None, 'other', 1, False),
    (None, None, 1, False),
    (None, 'other', 0, False),
])
def test_nullable_history_ids_keep_existing_dispatch_outcome(tmp_path, item, member, paused, expected):
    store = Store(tmp_path, lambda _: None)
    try:
        store.subscriptions.ensure_demo()
        with store.db:
            store.db.execute("INSERT INTO items(id,feed_id) VALUES (?,'demo')", (item,))
            store.db.execute("INSERT INTO jobs(kind,item) VALUES ('fetch',?)", (item,))
            store.db.execute('INSERT INTO initial_history(feed,selection,paused) VALUES (?,?,?)',
                             ('demo', json.dumps(dict(cancelled=bool(paused))), paused))
            store.db.execute("INSERT INTO history_members(feed,item) VALUES ('demo',?)", (member,))
        job = store.next_job(0)
        assert (job is not None) is expected
        if job:
            assert job.item == item
    finally:
        store.db.close()


@pytest.mark.parametrize('analyze', [False, True])
def test_foreign_cancelled_history_does_not_add_linear_dispatch_work(tmp_path, analyze):
    costs = []
    for count in (100, 10000):
        store = Store(tmp_path / str(count), lambda _: None)
        try:
            store.subscriptions.ensure_demo()
            with store.db:
                store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection) VALUES ('archive','archive','Archive','default','rss','rss','connected')")
                store.db.execute("INSERT INTO initial_history(feed,selection,paused,scan_complete) VALUES ('archive','{\"cancelled\":true}',1,1)")
                store.db.executemany("INSERT INTO items(id,feed_id,viewed) VALUES (?,'archive',1)",
                                     [(f'old-{i}',) for i in range(10000)])
                store.db.executemany("INSERT INTO jobs(kind,item,status) VALUES ('fetch',?,'done')",
                                     [(f'old-{i}',) for i in range(10000)])
                store.db.executemany("INSERT INTO history_members(feed,item,outcome) VALUES ('archive',?,'loaded')",
                                     [(f'old-{i}',) for i in range(count)])
                store.db.execute("INSERT INTO items(id,feed_id) VALUES ('live','demo')")
                store.db.execute("INSERT INTO jobs(kind,item) VALUES ('fetch','live')")
                if analyze:
                    store.db.execute('ANALYZE')
            steps = []
            store.db.set_progress_handler(lambda: steps.append(1) or 0, 1)
            try:
                job = store.next_job(0)
            finally:
                store.db.set_progress_handler(None, 0)
            assert job and (job.kind, job.item) == ('fetch', 'live')
            # Measure real bound SQL plus reservation/generations/triggers. Hold
            # items/jobs fixed: their plans are outside history admission scope.
            costs.append(len(steps))
        finally:
            store.db.close()
    assert costs[1] <= costs[0] + 200, costs
