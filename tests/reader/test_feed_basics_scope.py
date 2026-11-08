"""Navigation reads feed facts without losing history from full responses."""
from fastapi.testclient import TestClient

from tests.support.reader import HEADERS
from tests.support.reading_cost import library, traced_app, tracing, wait_for_recovery


def test_navigation_omits_history_reads_and_other_views_keep_history(tmp_path):
    feeds, _ = library(tmp_path, [1, 1])
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        wait_for_recovery(app)
        tracing(client, app)
        nav = client.get('/api/v3/navigation')
        assert nav.status_code == 200
        assert int(nav.headers['X-Test-SQL']) <= 8
        assert not any('initial_history' in sql.lower() for sql in app.state.last_sql)
        unchanged = client.get('/api/v3/navigation', headers={'If-None-Match': nav.headers['ETag']})
        assert unchanged.status_code == 304
        assert int(unchanged.headers['X-Test-SQL']) <= 8
        assert not any('initial_history' in sql.lower() for sql in app.state.last_sql)

        subscriptions = client.get('/api/v3/subscriptions')
        assert subscriptions.status_code == 200
        histories = {feed['id']: feed['history'] for feed in subscriptions.json()['feeds']}
        assert set(histories) == set(feeds)
        assert all(value['state'] != 'absent' for value in histories.values())
        composition = client.get('/api/v3/reading-composition')
        assert composition.status_code == 200
        assert {item['feed']['id']: item['feed']['history'] for item in composition.json()['feeds']} == histories
        for feed in feeds:
            settings = client.get('/api/v3/settings/feed/' + feed)
            assert settings.status_code == 200
            assert settings.json()['feed']['history'] == histories[feed]
        details = client.get('/api/v3/status/details')
        assert details.status_code == 200
        assert {item['feed_id']: item['history'] for item in details.json()['initial_histories']} == histories

        assert client.delete('/api/v3/feeds/' + feeds[0]).status_code == 200
        def deleted_feed_history():
            store = app.state.host.require_store()
            basic = store.subscriptions.feeds(include_deleted=True)
            full = store.subscriptions.feeds_with_history(include_deleted=True)
            return next(f for f in basic if f['id'] == feeds[0]), next(f for f in full if f['id'] == feeds[0])
        basic, full = client.portal.call(deleted_feed_history)
        assert 'initial_history' not in basic
        assert full['initial_history'] is not None
        assert feeds[0] not in [item['id'] for item in client.get('/api/v3/subscriptions').json()['feeds']]


def test_refresh_skips_damaged_history_but_continues_other_work(tmp_path):
    from unittest.mock import patch
    from oko.services.feed_refresh import FeedRefresh
    from oko.storage.transaction import transaction
    from oko.subscriptions.history import InvalidHistoryError

    feeds, _ = library(tmp_path, [1, 1])
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        wait_for_recovery(app)
        def refresh():
            store = app.state.host.require_store()
            calls = []
            class Media:
                def publications_for_feed(self, feed):
                    return [feed + '-publication']
            class Jobs:
                def retry_media(self, item, *, revalidate_ready=True):
                    calls.append(('media', item, revalidate_ready))
                def refresh_discovery(self, feed):
                    calls.append(('discovery', feed))
                def retry_feed_fetches(self, feed):
                    calls.append(('fetch', feed))
                def retry_feed_processing(self, feed):
                    calls.append(('processing', feed))
            history = store.subscriptions.history
            original = history.status
            def status(feed):
                if feed == feeds[0]:
                    raise InvalidHistoryError('synthetic damaged history')
                return original(feed)
            with transaction(store.db), patch.object(history, 'status', side_effect=status):
                for feed in feeds:
                    FeedRefresh(Media(), store.subscriptions, Jobs()).refresh(feed)
            return calls
        calls = client.portal.call(refresh)
        assert ('discovery', feeds[0]) not in calls
        assert ('discovery', feeds[1]) in calls
        for feed in feeds:
            assert ('media', feed + '-publication', False) in calls
            assert ('fetch', feed) in calls
            assert ('processing', feed) in calls


def test_full_feed_marks_invalid_history_without_mutating_basic_feed(tmp_path):
    from unittest.mock import patch
    from oko.storage.reading import reading_snapshot
    from oko.subscriptions.history import InvalidHistory

    feeds, _ = library(tmp_path, [1])
    app = traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        wait_for_recovery(app)
        def read():
            subscriptions = app.state.host.require_store().subscriptions
            with reading_snapshot(), patch.object(subscriptions.history, 'statuses', return_value={feeds[0]: InvalidHistory(feeds[0])}):
                basic = subscriptions.feeds(feeds[0])[0]
                full = subscriptions.feeds_with_history(feeds[0])[0]
                return basic, full
        basic, full = client.portal.call(read)
        assert 'initial_history' not in basic
        assert full['initial_history'] is None
        assert full['initial_history_invalid'] is True
