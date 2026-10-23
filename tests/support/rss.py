import time
from datetime import datetime, timezone
from email.utils import format_datetime


def rss(url='https://source.test/article', guid='one', days=0):
    date = format_datetime(datetime.fromtimestamp(time.time() - days * 86400, timezone.utc))
    return f'<rss version="2.0"><channel><item><guid>{guid}</guid><link>{url}</link><title>Заголовок</title><pubDate>{date}</pubDate><description>Анонс статьи</description></item></channel></rss>'


def import_feed(client, url='https://source.test/feed'):
    client.patch('/api/v3/settings/oko/global', json={})
    from tests.support.subscriptions import add_source
    return add_source(client, url, 'Источник', 'Новости')


def wait_cards(client, count=1, *, idle=True):
    for _ in range(200):
        cards = client.get('/api/v3/cards').json()
        if len(cards) == count and all((c['availability'] == 'available') and not c.get('loading') for c in cards) and (not idle or not any(work['phase']=='collecting' for work in client.get('/api/v3/status').json()['work'])):
            return cards
        time.sleep(.02)
    raise AssertionError(client.get('/api/v3/status').json())
