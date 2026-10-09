import time
from datetime import datetime, timezone
from email.utils import format_datetime


def rss(url='https://source.test/article', guid='one', days=0):
    date = format_datetime(datetime.fromtimestamp(time.time() - days * 86400, timezone.utc))
    return f'<rss version="2.0"><channel><item><guid>{guid}</guid><link>{url}</link><title>Заголовок</title><pubDate>{date}</pubDate><description>Анонс статьи</description></item></channel></rss>'


def import_feed(client, url='https://source.test/feed'):
    client.patch('/api/settings/oko/global', json={'events': False})
    body = {'opml': f'<opml><body><outline text="Новости"><outline text="Источник" xmlUrl="{url}"/></outline></body></opml>'}
    return client.post('/api/import', json=body).json()['feeds'][0]


def wait_cards(client, count=1):
    for _ in range(200):
        cards = client.get('/api/cards').json()
        if len(cards) == count and all(c['complete'] and not c.get('loading') for c in cards):
            return cards
        time.sleep(.02)
    raise AssertionError(client.get('/api/status').json())
