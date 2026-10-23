"""Synthetic RSS/Atom pages and real adapter walks, without a reader scheduler."""
import json

import httpx

from oko.collection.rss_adapter import RSSAdapter


def page(ids, links='', atom=False):
    if atom:
        return '<feed xmlns="http://www.w3.org/2005/Atom"><title>History</title>' + links + ''.join(
            f'<entry><id>{n}</id><title>News {n}</title><link href="https://source.test/post/{n}"/><published>2025-10-01T12:00:00Z</published><summary>Excerpt {n}</summary></entry>' for n in ids) + '</feed>'
    return '<rss xmlns:atom="http://www.w3.org/2005/Atom"><channel><title>History</title>' + links + ''.join(
        f'<item><guid>{n}</guid><title>News {n}</title><link>https://source.test/post/{n}</link><pubDate>Wed, 01 Oct 2025 12:00:00 GMT</pubDate><description>Excerpt {n}</description></item>' for n in ids) + '</channel></rss>'


async def walk(reply, *, count=200):
    adapter=RSSAdapter(httpx.MockTransport(reply))
    feed=await adapter.resolve_feed('https://source.test/feed')
    cursor=await adapter.cursor_before(feed.reference,feed.latest_cursor,count-1)
    publications=[]
    for _ in range(1002):
        result=await adapter.discover(feed.reference,cursor,50)
        cursor=result.next_cursor
        publications.extend(result.publications)
        if not result.has_more:
            return result,publications
    raise AssertionError('RSS walk exceeded its publication-page budget')
