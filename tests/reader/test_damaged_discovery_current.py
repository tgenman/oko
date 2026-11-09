"""Synthetic discovery admission and late responses around damaged history."""
import asyncio
import json

import pytest

from oko.collection.contracts import DiscoveryResult, SourceError
from oko.storage.transaction import transaction
from oko.subscriptions.history import InitialHistory
from tests.support.job_claim import claim_job
from tests.support.source import StructuredSource
from tests.support.storage import cleanup, owned_schemas
from tests.support.worker_library import collection_worker, worker_library


@pytest.mark.parametrize('failed', [False, True], ids=['late-page', 'late-error'])
@pytest.mark.parametrize('replacement', ['damaged', 'new-run'])
def test_damaged_history_discards_late_discovery_result_and_error(tmp_path, failed, replacement):
    async def scenario():
        previous = owned_schemas()
        entered, release = asyncio.Event(), asyncio.Event()
        class Held:
            changes = None
            async def discover(self, cursor):
                entered.set()
                await release.wait()
                if failed:
                    raise SourceError('synthetic late failure', retryable=False)
                return DiscoveryResult((), 'late-cursor')
        store, _, _, _ = worker_library(tmp_path)
        task = None
        try:
            with transaction(store.db):
                store.subscriptions.history.configure('fixture', InitialHistory(count=1))
                store.jobs.enqueue_discovery('fixture')
                store.db.execute("UPDATE feeds SET cursor='' WHERE id='fixture'")
            job = claim_job(store, store.clock(), feeds=['fixture'])
            assert job and job.kind == 'discover'
            worker = collection_worker(tmp_path, store, StructuredSource(Held()))
            task = asyncio.create_task(worker._execute(job))
            await asyncio.sleep(.1)
            assert not task.done(), dict(store.db.execute('SELECT status,reason_code,reason_message,generation FROM jobs WHERE id=%s', (job.id,)).fetchone())
            await asyncio.wait_for(entered.wait(), 5)
            with transaction(store.db):
                value = '{broken' if replacement == 'damaged' else json.dumps(dict(mode='count',count=2,since=None,run='replacement'))
                store.db.execute('UPDATE initial_history SET selection=%s WHERE feed=%s', (value, 'fixture'))
            history_before = dict(store.db.execute("SELECT * FROM initial_history WHERE feed='fixture'").fetchone())
            cursor_before = store.db.execute("SELECT cursor FROM feeds WHERE id='fixture'").fetchone()[0]
            release.set()
            await asyncio.wait_for(task, 5)
            task = None
            assert dict(store.db.execute("SELECT * FROM initial_history WHERE feed='fixture'").fetchone()) == history_before
            assert store.db.execute("SELECT cursor FROM feeds WHERE id='fixture'").fetchone()[0] == cursor_before
            state = store.db.execute('SELECT status,generation,reason_message FROM jobs WHERE id=%s', (job.id,)).fetchone()
            assert state['status'] == 'pending' and state['generation'] > job.generation
            assert state['reason_message'] is None
        finally:
            release.set()
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            store.db.close()
            cleanup(created_after=previous)
    asyncio.run(scenario())


def test_damaged_history_does_not_starve_other_discovery(tmp_path):
    previous = owned_schemas()
    store, _, _, _ = worker_library(tmp_path)
    try:
        with transaction(store.db):
            store.subscriptions.history.configure('fixture', InitialHistory(count=1))
            store.db.execute("UPDATE initial_history SET selection='{broken' WHERE feed='fixture'")
            store.db.execute("INSERT INTO feeds(id,identity,title,kind,folder_id,original_urls,connection,transport) VALUES ('healthy','https://healthy.invalid/feed','Healthy','rss','fixture',ARRAY['https://healthy.invalid/feed'],'connected','rss')")
            store.jobs.enqueue_discovery('fixture')
            store.jobs.enqueue_discovery('healthy')
        options = dict(feeds=['fixture','healthy'], invalid_history_feeds=['fixture'])
        assert store.jobs.has_due_job(store.clock(), **options)
        job = claim_job(store,store.clock(),**options)
        assert job and (job.kind,job.item)==('discover','healthy')
        damaged = store.db.execute("SELECT status FROM jobs WHERE kind='discover' AND item='fixture'").fetchone()
        assert damaged['status']=='pending'
    finally:
        store.db.close()
        cleanup(created_after=previous)


@pytest.mark.parametrize('broken', ['{}', '{broken'])
def test_healthy_feed_collects_and_damaged_feed_resumes_after_repair(tmp_path, broken):
    import httpx
    from fastapi.testclient import TestClient
    from oko.subscriptions.subscriptions import AddFeed
    from tests.support.app import create_app
    from tests.support.reader import HEADERS, wait_for
    from tests.support.rss import rss
    from tests.support.storage import Store

    previous = owned_schemas()
    store = Store(tmp_path, lambda _: None)
    try:
        with transaction(store.db):
            feeds = {label:store.subscriptions.add_feed(AddFeed(url=f'https://{label}.fixture.test/feed', title=label,
                folder='Synthetic', verification='synthetic'))['id'] for label in ('bad','good')}
            original=store.db.execute('SELECT selection FROM initial_history WHERE feed=%s', (feeds['bad'],)).fetchone()[0]
            store.db.execute('UPDATE initial_history SET selection=%s WHERE feed=%s', (broken,feeds['bad']))
            # Bad history has no pre-existing discovery job: repair itself
            # must schedule it without waiting for the periodic collection.
            store.jobs.enqueue_discovery(feeds['good'])
    finally:
        store.db.close()
    calls=[]
    def respond(request):
        calls.append((request.url.host,request.url.path))
        return httpx.Response(200,text=rss() if request.url.path=='/feed' else '<article>Synthetic</article>')
    app=create_app(tmp_path,token='test',api_only=True,rss_enabled=True,rss_transport=httpx.MockTransport(respond))
    try:
        with TestClient(app,headers=HEADERS) as client:
            wait_for(client,lambda:('good.fixture.test','/feed') in calls)
            assert ('bad.fixture.test','/feed') not in calls
            def repair():
                current=app.state.host.require_store()
                with transaction(current.db):
                    current.db.execute('UPDATE initial_history SET selection=%s WHERE feed=%s',(original,feeds['bad']))
            client.portal.call(repair)
            wait_for(client,lambda:('bad.fixture.test','/feed') in calls)
    finally:
        cleanup(created_after=previous)


def test_repairing_only_history_member_revalidates_worker(tmp_path):
    from tests.support.synthetic_source import SyntheticSource
    previous=owned_schemas()
    store,item,_,_=worker_library(tmp_path)
    try:
        with transaction(store.db):
            store.subscriptions.history.configure('fixture',InitialHistory(count=1))
            store.subscriptions.history.enroll('fixture',[item],True)
            store.db.execute("UPDATE history_members SET outcome='unknown-outcome' WHERE feed='fixture' AND item=%s",(item,))
        worker=collection_worker(tmp_path,store,StructuredSource(SyntheticSource()))
        worker._sync_sources()
        assert 'fixture' in worker._invalid_history
        with transaction(store.db):
            store.db.execute("UPDATE history_members SET outcome='pending' WHERE feed='fixture' AND item=%s",(item,))
        worker._next_history_revision_check=float('-inf')
        worker._sync_sources()
        assert 'fixture' not in worker._invalid_history
    finally:
        store.db.close()
        cleanup(created_after=previous)


def test_replay_rotates_past_blocked_feed_without_hot_loop(tmp_path):
    from tests.support.synthetic_source import SyntheticSource
    previous=owned_schemas()
    store,_,_,_=worker_library(tmp_path)
    try:
        with transaction(store.db):
            for identity in ('a','b'):
                store.db.execute("INSERT INTO feeds(id,identity,title,kind,folder_id,original_urls,connection,transport) VALUES (%s,%s,%s,'rss','fixture',ARRAY[]::text[],'connected','rss')",(identity,identity,identity))
            store.db.execute("INSERT INTO history_deferred_events(feed,item,run_id,transitions) VALUES ('a','a-item','run','{}'::jsonb),('b','b-item','run','{}'::jsonb)")
        worker=collection_worker(tmp_path,store,StructuredSource(SyntheticSource()))
        called=[]
        original=store.subscriptions.history.replay_deferred
        def replay(feed):
            called.append(feed)
            return (feed=='b', True)
        store.subscriptions.history.replay_deferred=replay
        try:
            assert not worker._replay_history(store,0)
            assert called==['a']
            assert not worker._replay_history(store,4.9)
            assert called==['a']
            assert worker._replay_history(store,5)
            assert called==['a','b']
            assert not worker._replay_history(store,6)
            assert called==['a','b','a']
        finally:
            store.subscriptions.history.replay_deferred=original
    finally:
        store.db.close()
        cleanup(created_after=previous)


def test_worker_replays_progress_after_repair_without_status_request(tmp_path):
    from tests.reader.test_history_deferred_outcomes import prepared, value
    from tests.support.synthetic_source import SyntheticSource
    previous=owned_schemas()
    store,feed,item,selection=prepared(tmp_path)
    try:
        with transaction(store.db):
            store.db.execute('UPDATE initial_history SET selection=%s WHERE feed=%s', ('{bad',feed))
            store.subscriptions.history.saved(item,True)
        assert value(store,'SELECT count(*) FROM history_deferred_events')==1
        with transaction(store.db):
            store.db.execute('UPDATE initial_history SET selection=%s WHERE feed=%s',(selection,feed))
        worker=collection_worker(tmp_path,store,StructuredSource(SyntheticSource()))
        assert worker._replay_history(store,0)
        assert value(store,'SELECT count(*) FROM history_deferred_events')==0
        assert value(store,'SELECT outcome FROM history_members WHERE feed=%s AND item=%s',(feed,item))=='loaded'
    finally:
        store.db.close()
        cleanup(created_after=previous)


def test_replay_skips_deleted_feed_until_restore(tmp_path):
    from tests.support.synthetic_source import SyntheticSource
    previous=owned_schemas()
    store,_,_,_=worker_library(tmp_path)
    try:
        with transaction(store.db):
            for identity,deleted in (('a',True),('b',False)):
                store.db.execute("INSERT INTO feeds(id,identity,title,kind,folder_id,original_urls,connection,transport,deleted) VALUES (%s,%s,%s,'rss','fixture',ARRAY[]::text[],'connected','rss',%s::integer::boolean)",(identity,identity,identity,deleted))
            store.db.execute("INSERT INTO history_deferred_events(feed,item,run_id,transitions) VALUES ('a','a-item','run','{}'::jsonb),('b','b-item','run','{}'::jsonb)")
        worker=collection_worker(tmp_path,store,StructuredSource(SyntheticSource()))
        called=[]
        original=store.subscriptions.history.replay_deferred
        store.subscriptions.history.replay_deferred=lambda feed: (called.append(feed) or True, True)
        try:
            assert worker._replay_history(store,0)
            assert called==['b']
            assert store.db.execute("SELECT count(*) FROM history_deferred_events WHERE feed='a'").fetchone()[0]==1
            with transaction(store.db):
                store.db.execute("UPDATE feeds SET deleted=false WHERE id='a'")
            assert worker._replay_history(store,1)
            assert called==['b','a']
        finally:
            store.subscriptions.history.replay_deferred=original
    finally:
        store.db.close()
        cleanup(created_after=previous)
