import time


class Clock:
    def __init__(self):
        self.now = 100
    def __call__(self):
        return self.now


HEADERS = {'Authorization': 'Bearer test'}


def wait_for(client, predicate):
    for _ in range(150):
        value = predicate()
        if value:
            return value
        time.sleep(.02)
    raise AssertionError('expected reader state did not arrive')


def setup_reader(client, *, theses):
    response = client.patch('/api/settings/oko/global', json={'theses': theses, 'prompt_filters': ''})
    assert response.status_code == 200, response.text
    client.post('/api/tabs/reader/heartbeat')
    cards = wait_for(client, lambda: (cards if len(cards := client.get('/api/cards').json()) == 3 and all(c['complete'] for c in cards) else None))
    return cards[0]['id']
