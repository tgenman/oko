"""Synthetic RSS/Atom pages and real adapter walks, without a reader scheduler."""
import json

import httpx

from oko.collection.rss import RSSSource


def page(ids, links='', atom=False):
    if atom:
        return '<feed xmlns="http://www.w3.org/2005/Atom"><title>History</title>' + links + ''.join(
            f'<entry><id>{n}</id><title>News {n}</title><link href="https://source.test/post/{n}"/><published>2025-10-01T12:00:00Z</published><summary>Excerpt {n}</summary></entry>' for n in ids) + '</feed>'
    return '<rss xmlns:atom="http://www.w3.org/2005/Atom"><channel><title>History</title>' + links + ''.join(
        f'<item><guid>{n}</guid><title>News {n}</title><link>https://source.test/post/{n}</link><pubDate>Wed, 01 Oct 2025 12:00:00 GMT</pubDate><description>Excerpt {n}</description></item>' for n in ids) + '</channel></rss>'


async def walk(reply, *, selection=None, stop_after=None):
    source = RSSSource('https://source.test/feed', 7, httpx.MockTransport(reply))
    source.initial_history = selection or dict(mode='count', count=200, run='fixture')
    cursor, publications = None, []
    for expected in range(1, 1002):
        result = await source.discover(cursor)
        cursor = result.cursor
        assert json.loads(cursor)['history_requests'] == expected
        publications.extend(result.publications)
        if result.continuation is None or expected == stop_after:
            return result, publications
    raise AssertionError('RSS walk exceeded its 1000-request budget')
