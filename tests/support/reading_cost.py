from tests.support.subscription_action import subscription_action
from tests.support.job_claim import claim_job
from tests.support.collection_acceptance import discovered
"""Bound HTTP reading work independently of machine speed and background work."""
from contextvars import ContextVar
import time

from tests.support.legacy_source import Publication

from tests.support.synthetic_material import material as Material
from oko.storage.publication_owners import owner_expr, owned_ids
from oko.storage.transaction import transaction
from tests.support.storage import Store
from oko.subscriptions.subscriptions import AddFeed
from tests.support.app import create_app
from tests.support.library_templates import templates
from tests.support.source import publication_packet


request_sql: ContextVar[list[str] | None] = ContextVar('reading_sql', default=None)


def wait_for_recovery(app):
    deadline = time.monotonic() + 60
    while app.state.host.require_store().processing.recovering:
        assert time.monotonic() < deadline, 'Initial PostgreSQL recovery did not finish'
        time.sleep(.02)


def library(root, counts):
    counts = tuple(counts)
    return templates().copy(root, ('reading', counts), lambda source: build_library(source, counts))


def build_library(root, counts):
    store = Store(root, lambda _: None)
    try:
        feeds, items = [], []
        for index, count in enumerate(counts):
            feed = subscription_action(store, 'add_feed', AddFeed(
                url='https://fixture.test/feed/'+str(index),
                title=f'Synthetic {index}', folder=f'Folder {index}', verification='synthetic'))['id']
            feeds.append(feed)
            with transaction(store.db):
                store.jobs.enqueue_discovery(feed)
            job = claim_job(store, 0, feeds=[feed])
            assert job is not None
            discovered(store,[publication_packet(Publication(str(n), f'https://example.org/{index}/{n}', n),
                              Material(f'Video {n}', '', captions='not_requested', description='Synthetic video'),needs_fetch=True)
                              for n in range(count)], '{}', job, feed)
            items.append(store.db.execute(owned_ids('= %s')+' ORDER BY id', (feed,feed)).fetchone()[0])
        store.set_work_enabled(False)
    finally:
        store.db.close()
    return feeds, items


def traced_app(root, **options):
    app = create_app(root, token='test', api_only=True, **options)

    @app.middleware('http')
    async def trace(request, call_next):
        queries = []
        token = request_sql.set(queries)
        try:
            response = await call_next(request)
            response.headers['X-Test-SQL'] = str(len(queries))
            app.state.last_sql = queries
            return response
        finally:
            request_sql.reset(token)

    return app


def tracing(client, app):
    def record(query):
        queries = request_sql.get()
        if queries is not None:
            queries.append(query)
    client.portal.call(lambda: app.state.host.require_store().db.set_trace_callback(record))
