"""Bound HTTP reading work independently of machine speed and background work."""
from contextvars import ContextVar

from oko.collection.contracts import Material, Publication
from oko.storage.store import Store
from oko.subscriptions.subscriptions import AddFeed
from tests.support.app import create_app


request_sql: ContextVar[list[str] | None] = ContextVar('reading_sql', default=None)


def library(root, counts):
    store = Store(root, lambda _: None)
    feeds, items = [], []
    for index, count in enumerate(counts):
        feed = store.subscriptions.add_feed(AddFeed(
            url='https://www.youtube.com/channel/UC' + str(index).zfill(22),
            title=f'Synthetic {index}', folder=f'Folder {index}', verification='synthetic'))['id']
        feeds.append(feed)
        store.enqueue_discovery(feed)
        job = store.next_job(0, feeds=[feed])
        assert job is not None
        store.discovered([Publication(str(n), f'https://example.org/{index}/{n}', n,
                          teaser=Material(f'Video {n}', '', captions='not_requested', description='Synthetic video'))
                          for n in range(count)], '{}', job, feed)
        items.append(store.db.execute('SELECT id FROM items WHERE feed_id=? ORDER BY id', (feed,)).fetchone()[0])
    store.set_work_enabled(False)
    store.db.close()
    return feeds, items


def traced_app(root):
    app = create_app(root, token='test')

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
    client.portal.call(lambda: app.state.runtime.require_store().db.set_trace_callback(record))
