import base64
from datetime import datetime, timezone


VIDEO = 'abcdefghijk'


PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAUAAAAC0CAIAAABqhmJGAAACJ0lEQVR4nO3TMQ0AIBDAwPcviwkBCMIDC2lyyQno0tlnAVHzvQB4ZmAIMzCEGRjCDAxhBoYwA0OYgSHMwBBmYAgzMIQZGMIMDGEGhjADQ5iBIczAEGZgCDMwhBkYwgwMYQaGMANDmIEhzMAQZmAIMzCEGRjCDAxhBoYwA0OYgSHMwBBmYAgzMIQZGMIMDGEGhjADQ5iBIczAEGZgCDMwhBkYwgwMYQaGMANDmIEhzMAQZmAIMzCEGRjCDAxhBoYwA0OYgSHMwBBmYAgzMIQZGMIMDGEGhjADQ5iBIczAEGZgCDMwhBkYwgwMYQaGMANDmIEhzMAQZmAIMzCEGRjCDAxhBoYwA0OYgSHMwBBmYAgzMIQZGMIMDGEGhjADQ5iBIczAEGZgCDMwhBkYwgwMYQaGMANDmIEhzMAQZmAIMzCEGRjCDAxhBoYwA0OYgSHMwBBmYAgzMIQZGMIMDGEGhjADQ5iBIczAEGZgCDMwhBkYwgwMYQaGMANDmIEhzMAQZmAIMzCEGRjCDAxhBoYwA0OYgSHMwBBmYAgzMIQZGMIMDGEGhjADQ5iBIczAEGZgCDMwhBkYwgwMYQaGMANDmIEhzMAQZmAIMzCEGRjCDAxhBoYwA0OYgSHMwBBmYAgzMIQZGMIMDGEGhjADQ5iBIczAEGZgCDMwhBkYwgwMYQaGMANDmIEhzMAQZmAIMzCEGRjCDAxhBoYwA0OYgSHMwBBmYAgzMIRdj069LYHQXNAAAAAASUVORK5CYII=')


def subscribe(client):
    opml = f'<opml><body><outline text="Наука"><outline text="Канал" xmlUrl="https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL}"/></outline></body></opml>'
    first = client.post('/api/import', json={'opml': opml}).json()['feeds'][0]
    again = client.post('/api/import', json={'opml': opml}).json()['feeds']
    assert len(again) == 1 and again[0]['id'] == first['id']
    return first['id']


def feed_xml():
    stamp = datetime.now(timezone.utc).isoformat()
    return f'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/">
    <entry><id>yt:video:{VIDEO}</id><yt:videoId>{VIDEO}</yt:videoId><title>О границах модели</title><published>{stamp}</published><link href="https://www.youtube.com/watch?v={VIDEO}"/>
    <media:group><media:thumbnail url="https://i.ytimg.com/vi/{VIDEO}/hqdefault.jpg"/></media:group></entry></feed>'''


CHANNEL = 'UC' + 'a' * 22
