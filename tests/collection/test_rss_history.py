"""Large pagination dimensions belong at the RSS adapter HTTP boundary."""
import asyncio
import json
from collections import Counter

import httpx
import pytest

from tests.support.rss_history import page, walk


@pytest.mark.parametrize('atom', [False, True])
def test_rss_atom_walk_reaches_200_and_keeps_deferred_archive_with_relative_links(atom):
    calls = Counter()
    def render(ids, links=''):
        return page(ids, links if atom else links.replace('<link ', '<atom:link '), atom=atom)
    def reply(request):
        path = request.url.path
        calls[path] += 1
        if path == '/feed':
            return httpx.Response(302, headers={'location':'/pages/start'})
        if path == '/pages/start':
            return httpx.Response(200, text=render(range(200,150,-1), '<link rel="next" href="empty"/><link rel="prev-archive" href="../archive/first"/>'))
        if path == '/pages/empty':
            return httpx.Response(200, text=render([], '<link rel="next" href="second"/>'))
        if path == '/pages/second':
            return httpx.Response(200, text=render(range(151,100,-1), '<link rel="prev-archive" href="../archive/first"/>'))
        if path == '/archive/first':
            return httpx.Response(200, text=render(range(100,0,-1), '<link rel="next" href="should-not-load"/>').replace('<feed ', '<feed xml:base="https://source.test/archive/" '))
        assert not path.endswith('should-not-load')
        return httpx.Response(200, text='<article>Full article</article>')
    result, publications = asyncio.run(walk(reply))
    progress = json.loads(result.cursor)
    assert [p.id for p in publications] == [str(n) for n in range(200, 0, -1)]
    assert progress['stop_reason'] == 'count_reached'
    assert calls['/pages/empty'] == calls['/pages/second'] == calls['/archive/first'] == 1


def test_1000_page_budget_is_visible_even_when_empty_pages_continue():
    discovery = []
    def reply(request):
        path = request.url.path
        if path == '/feed':
            return httpx.Response(200, text=page([], '<atom:link rel="next" href="/pages/2"/>'))
        number = int(path.rsplit('/',1)[1])
        discovery.append(number)
        return httpx.Response(200, text=page([], f'<atom:link rel="next" href="/pages/{number+1}"/>'))
    result, publications = asyncio.run(walk(reply))
    progress = json.loads(result.cursor)
    assert progress['stop_reason'] == 'page_limit'
    assert progress['history_requests'] == 1000
    assert publications == []
    assert discovery == list(range(2,1001))


def test_redirect_to_other_visited_branch_cannot_exceed_1000_page_budget():
    calls = []
    def reply(request):
        path = request.url.path
        calls.append(path)
        if path == '/feed':
            return httpx.Response(200, text=page([], '<atom:link rel="next" href="/first"/><atom:link rel="prev-archive" href="/archive/3"/>'))
        if path == '/first':
            return httpx.Response(200, text=page([]))
        if path == '/alias':
            return httpx.Response(302, headers={'location':'/first'})
        if path.startswith('/archive/'):
            number = int(path.rsplit('/',1)[1])
            links = f'<atom:link rel="next" href="/archive/{number+1}"/>' if number<999 else '<atom:link rel="next" href="/alias"/><atom:link rel="prev-archive" href="/must-not-load"/>'
            return httpx.Response(200, text=page([], links))
        return httpx.Response(200, text=page([]))
    result, publications = asyncio.run(walk(reply))
    progress = json.loads(result.cursor)
    assert progress['stop_reason'] == 'page_limit'
    assert progress['history_requests'] == 1000
    assert '/must-not-load' not in calls
