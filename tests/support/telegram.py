from tests.support.cards import read_all_cards
import time


def settled(client, count=1, timeout=4):
    deadline = time.monotonic()+timeout
    while time.monotonic()<deadline:
        cards = read_all_cards(client, '/api/v3/cards')
        if len(cards) == count and all(not c['loading'] for c in cards):
            return cards
        time.sleep(.02)
    raise AssertionError(client.get('/api/v3/status/details').json())


def configure_fast_web(client):
    """Explicit fast transport in functional fixtures; budget tests keep their own rate."""
    response = client.patch('/api/v3/settings/oko/global', json={'telegram_http_rps':10})
    assert response.status_code == 200, response.text
