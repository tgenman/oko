import io
import json
from urllib.parse import parse_qs, urlsplit

from yt_dlp import YoutubeDL
from yt_dlp.networking import Response
from oko.collection.youtube.history_worker import collect
from tests.support.youtube import CHANNEL


def ident(n):
    return f'v{n:010d}'


def renderer(n):
    return {'playlistVideoRenderer': {'videoId': ident(n), 'title': {'runs': [{'text': f'Video {n}'}]},
                                     'navigationEndpoint': {'watchEndpoint': {'videoId': ident(n)}}}}


def listing(items, continuation=None):
    contents = [renderer(n) for n in items]
    if continuation:
        contents.append({'continuationItemRenderer': {'continuationEndpoint': {'continuationCommand': {'token': continuation}}}})
    data = {'metadata': {'playlistMetadataRenderer': {'title': 'Channel'}},
            'contents': {'twoColumnBrowseResultsRenderer': {'tabs': [{'tabRenderer': {'selected': True,
                'content': {'sectionListRenderer': {'contents': [{'itemSectionRenderer': {'contents': [
                    {'playlistVideoListRenderer': {'contents': contents}}]}}]}}}}]}}}
    return ('<html><script>var ytInitialData = ' + json.dumps(data) + ';</script></html>').encode()


def test_real_ytdlp_combines_videos_and_streams_without_fetching_any_video(monkeypatch):
    requests = []
    lists = {'UULF': [9, 7, 5], 'UULV': [8, 6], 'UU': [10, 9, 8, 7, 6, 5]}
    def reply(self, request):
        path = urlsplit(request.url)
        assert path.hostname == 'www.youtube.com' and path.path == '/playlist'
        kind = parse_qs(path.query)['list'][0][:-22]
        requests.append(kind)
        return Response(io.BytesIO(listing(lists[kind])), request.url, headers={'Content-Type': 'text/html'}, status=200)
    monkeypatch.setattr(YoutubeDL, 'urlopen', reply)
    result = collect(CHANNEL, 3, {})
    assert result['complete'] is True and result['error'] is None, result
    assert [e['id'] for e in result['entries']] == [ident(9), ident(8), ident(7)]
    assert result['requests'] == 3 and requests == ['UULF', 'UULV', 'UU']
    # Short 10 is first in Uploads, but never consumes a place in N.
    assert ident(10) not in {e['id'] for e in result['entries']}


def test_request_portion_resumes_past_replayed_pages_instead_of_stalling(monkeypatch):
    calls = []
    uploads = [15, 14, 13, 12, 11, 10, 9, 8, 7]
    def reply(self, request):
        path = urlsplit(request.url)
        calls.append(path.path)
        if path.path == '/playlist':
            kind = parse_qs(path.query)['list'][0][:-22]
            raw = listing([9, 7, 5] if kind == 'UULF' else [8, 6] if kind == 'UULV' else uploads[:1],
                          'page:1' if kind == 'UU' else None)
            mime = 'text/html'
        else:
            assert path.path == '/youtubei/v1/browse'
            n = int(json.loads(request.data)['continuation'].split(':')[1])
            contents = [renderer(uploads[n])]
            if n + 1 < len(uploads):
                contents.append({'continuationItemRenderer': {'continuationEndpoint': {'continuationCommand': {'token': f'page:{n+1}'}}}})
            raw = json.dumps({'onResponseReceivedActions': [{'appendContinuationItemsAction': {'continuationItems': contents}}]}).encode()
            mime = 'application/json'
        return Response(io.BytesIO(raw), request.url, headers={'Content-Type': mime}, status=200)
    monkeypatch.setattr(YoutubeDL, 'urlopen', reply)
    first = collect(CHANNEL, 3, {})
    assert first['complete'] is False and first['error'] is None, first
    assert first['requests'] == 8
    second = collect(CHANNEL, 3, first['state'])
    assert second['complete'] is True and second['error'] is None, second
    assert [e['id'] for e in second['entries']] == [ident(9), ident(8), ident(7)]
    assert second['requests'] == 9


def test_failed_page_is_not_mistaken_for_end_of_history(monkeypatch):
    def reply(self, request):
        kind = parse_qs(urlsplit(request.url).query)['list'][0][:-22]
        if kind == 'UULV':
            raise OSError('Synthetic network failure')
        return Response(io.BytesIO(listing([9, 7, 5])), request.url, headers={'Content-Type': 'text/html'}, status=200)
    monkeypatch.setattr(YoutubeDL, 'urlopen', reply)
    result = collect(CHANNEL, 3, {})
    assert not result['complete'] and 'Synthetic network failure' in result['error']
    assert len(result['state']['lists']['UULF']) == 3
    assert result['diagnostic']['traceback']


def test_channel_without_streams_still_returns_last_n_regular_videos(monkeypatch):
    def reply(self, request):
        kind = parse_qs(urlsplit(request.url).query)['list'][0][:-22]
        raw = ('<html><script>var ytInitialData = ' + json.dumps({'alerts':[{'alertRenderer':{'type':'ERROR','text':{'simpleText':'The playlist does not exist.'}}}]}) + ';</script></html>').encode() if kind == 'UULV' else listing([9,7,5])
        return Response(io.BytesIO(raw), request.url, headers={'Content-Type':'text/html'}, status=200)
    monkeypatch.setattr(YoutubeDL, 'urlopen', reply)
    result = collect(CHANNEL, 3, {})
    assert result['complete'] and result['error'] is None, result
    assert [entry['id'] for entry in result['entries']] == [ident(9),ident(7),ident(5)]
