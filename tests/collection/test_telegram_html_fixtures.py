"""Recorded HTML evidence for #20, without pretending a web adapter exists."""
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'fixtures/telegram'


class Markup(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.elements = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def attributes(self, tag, name):
        return [attrs[name] for kind, attrs in self.elements if kind == tag and name in attrs]

    def has_class(self, name):
        return any(name in attrs.get('class', '').split() for _, attrs in self.elements)


def test_public_snapshots_preserve_actual_media_album_anchors_and_page_states():
    manifest = json.loads((ROOT / 'manifest.json').read_text())
    pages = {}
    for name, evidence in manifest.items():
        data = (ROOT / (name + '.html')).read_bytes()
        assert hashlib.sha256(data).hexdigest() == evidence['sha256']
        pages[name] = Markup(data.decode())
    public = pages['public']
    assert public.has_class('tgme_widget_message_text')
    assert public.has_class('tgme_widget_message_photo_wrap')
    assert any('background-image:' in style for style in public.attributes('a', 'style'))
    assert any(url.startswith('https://cdn1.telesco.pe/') for url in public.attributes('video', 'src'))
    assert 'https://telegram.org/blog/direct-to-channel-trim-voice-and-more' in public.attributes('a', 'href')
    album = pages['album']
    assert 'durov/373' in album.attributes('div', 'data-post')
    assert [attrs['href'] for tag, attrs in album.elements if tag == 'a' and 'grouped_media_wrap' in attrs.get('class', '').split()] == [
        f'https://t.me/durov/{identity}?single' for identity in range(373, 382)]
    assert len(album.attributes('video', 'src')) >= 9
    assert album.has_class('message_media_not_supported')
    assert '/s/durov?before=373' in album.attributes('a', 'href')
    empty, unavailable = pages['empty'], pages['unavailable']
    assert empty.has_class('tgme_channel_history')
    assert not empty.attributes('div', 'data-post')
    assert not empty.has_class('js-messages_more')
    assert not unavailable.has_class('tgme_channel_history')
    assert unavailable.has_class('tgme_page_description')
