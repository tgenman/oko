from tests.support.collection_acceptance import discovered
"""Bound HTTP reading work independently of machine speed and background work."""
from contextvars import ContextVar

from oko.collection.contracts import Material, Publication
from tests.support.storage import Store
from oko.subscriptions.subscriptions import AddFeed
from tests.support.app import create_app
from tests.support.library_templates import templates
from tests.support.source import publication_packet


request_sql: ContextVar[list[str] | None] = ContextVar('reading_sql', default=None)


def library(root, counts):
    counts = tuple(counts)
    return templates().copy(root, ('reading', counts), lambda source: build_library(source, counts))


def build_library(root, counts):
    store = Store(root, lambda _: None)
    try:
        feeds, items = [], []
        for index, count in enumerate(counts):
            feed = store.subscriptions.add_feed(AddFeed(
                url='https://fixture.test/feed/'+str(index),
                title=f'Synthetic {index}', folder=f'Folder {index}', verification='synthetic'))['id']
            feeds.append(feed)
            store.jobs.enqueue_discovery(feed)
            job = store.jobs.next_job(0, feeds=[feed])
            assert job is not None
            discovered(store,[publication_packet(Publication(str(n), f'https://example.org/{index}/{n}', n),
                              Material(f'Video {n}', '', captions='not_requested', description='Synthetic video'),needs_fetch=True)
                              for n in range(count)], '{}', job, feed)
            items.append(store.db.execute('SELECT id FROM items WHERE feed_id=%s ORDER BY id', (feed,)).fetchone()[0])
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
