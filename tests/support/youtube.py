from tests.support.subscriptions import add_sources
import base64
from datetime import datetime, timezone


VIDEO = 'abcdefghijk'


PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAUAAAAC0CAIAAABqhmJGAAACJ0lEQVR4nO3TMQ0AIBDAwPcviwkBCMIDC2lyyQno0tlnAVHzvQB4ZmAIMzCEGRjCDAxhBoYwA0OYgSHMwBBmYAgzMIQZGMIMDGEGhjADQ5iBIczAEGZgCDMwhBkYwgwMYQaGMANDmIEhzMAQZmAIMzCEGRjCDAxhBoYwA0OYgSHMwBBmYAgzMIQZGMIMDGEGhjADQ5iBIczAEGZgCDMwhBkYwgwMYQaGMANDmIEhzMAQZmAIMzCEGRjCDAxhBoYwA0OYgSHMwBBmYAgzMIQZGMIMDGEGhjADQ5iBIczAEGZgCDMwhBkYwgwMYQaGMANDmIEhzMAQZmAIMzCEGRjCDAxhBoYwA0OYgSHMwBBmYAgzMIQZGMIMDGEGhjADQ5iBIczAEGZgCDMwhBkYwgwMYQaGMANDmIEhzMAQZmAIMzCEGRjCDAxhBoYwA0OYgSHMwBBmYAgzMIQZGMIMDGEGhjADQ5iBIczAEGZgCDMwhBkYwgwMYQaGMANDmIEhzMAQZmAIMzCEGRjCDAxhBoYwA0OYgSHMwBBmYAgzMIQZGMIMDGEGhjADQ5iBIczAEGZgCDMwhBkYwgwMYQaGMANDmIEhzMAQZmAIMzCEGRjCDAxhBoYwA0OYgSHMwBBmYAgzMIQZGMIMDGEGhjADQ5iBIczAEGZgCDMwhBkYwgwMYQaGMANDmIEhzMAQZmAIMzCEGRjCDAxhBoYwA0OYgSHMwBBmYAgzMIRdj069LYHQXNAAAAAASUVORK5CYII=')


def subscribe(client):
    first = add_sources(client, [(f'https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL}', 'Канал', 'Наука')]).json()['feeds'][0]
    return first['id']


def feed_xml():
    stamp = datetime.now(timezone.utc).isoformat()
    return f'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/">
    <entry><id>yt:video:{VIDEO}</id><yt:videoId>{VIDEO}</yt:videoId><title>О границах модели</title><published>{stamp}</published><link href="https://www.youtube.com/watch?v={VIDEO}"/>
    <media:group><media:thumbnail url="https://i.ytimg.com/vi/{VIDEO}/hqdefault.jpg"/></media:group></entry></feed>'''


CHANNEL = 'UC' + 'a' * 22


class History:
    """Synthetic history transport; never contacts a real YouTube playlist."""
    def __init__(self, videos):
        self.videos = tuple(videos)
        self.calls = 0

    async def fetch(self, channel, count, state):
        self.calls += 1
        return dict(entries=[dict(id=v, title=v) for v in self.videos[:count]],
                    complete=True, state={}, error=None, requests=3)


class NoCaptions:
    async def fetch(self, video):
        return None
