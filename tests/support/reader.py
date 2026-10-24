from tests.support.presence import presence, close_presence
import time


class Clock:
    def __init__(self):
        self.now = 100
    def __call__(self):
        return self.now
    def options(self):
        """Control lease expiry and wall-clock discontinuities with the same clock."""
        return dict(clock=self, wall_clock=self)


HEADERS = {'Authorization': 'Bearer test'}


def wait_for(client, predicate):
    for _ in range(150):
        value = predicate()
        if value:
            return value
        time.sleep(.02)
    raise AssertionError('expected reader state did not arrive')


def setup_reader(client, *, theses):
    response = client.patch('/api/v3/settings/oko/global', json={'theses': theses, 'prompt_filters': ''})
    assert response.status_code == 200, response.text
    presence(client, 'reader')
    cards = wait_for(client, lambda: (cards if len(cards := client.get('/api/v3/cards').json()) == 3 and all((c['availability'] == 'available') for c in cards) else None))
    return cards[0]['id']
