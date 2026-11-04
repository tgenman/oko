"""Automatic collection observed through HTTP commands and a synthetic source."""
import threading
import time

from fastapi.testclient import TestClient

from oko.collection.contracts import DiscoveryResult, SourceObservation
from tests.support.app import create_app
from tests.support.reader import HEADERS, Clock


class CollectionProbe:
    changes = None

    def __init__(self):
        self.rounds = []
        self.condition = threading.Condition()

    async def discover(self, cursor):
        with self.condition:
            self.rounds.append(cursor)
            self.condition.notify_all()
        return DiscoveryResult((), str(len(self.rounds)), SourceObservation())

    async def fetch(self, publication):
        raise AssertionError('No publications in this scenario')

    def reached(self, count):
        with self.condition:
            assert self.condition.wait_for(lambda: len(self.rounds) == count, timeout=2), self.rounds

    def unchanged(self, count):
        with self.condition:
            assert not self.condition.wait_for(lambda: len(self.rounds) != count, timeout=.12), self.rounds


def test_default_automatic_interval_and_manual_collection_do_not_shift_timer(tmp_path):
    clock, source = Clock(), CollectionProbe()
    with TestClient(create_app(tmp_path, token='test', api_only=True, source=source, clock=clock), headers=HEADERS) as client:
        source.reached(1)  # Existing initial collection.
        clock.now = 999  # 899 seconds after startup.
        source.unchanged(1)
        assert client.post('/api/v3/collect', json={'scope': 'feed', 'feed_id': 'fixture'}).status_code == 200
        source.reached(2)
        clock.now = 1000
        source.reached(3)  # Default 15 minutes; the manual command did not postpone it.
        clock.now = 1899
        source.unchanged(3)
        assert client.post('/api/v3/collect').status_code == 200
        source.reached(4)
        clock.now = 1900
        source.reached(5)


def test_interval_changes_recalculate_from_last_automatic_round(tmp_path):
    clock, source = Clock(), CollectionProbe()
    with TestClient(create_app(tmp_path, token='test', api_only=True, source=source, clock=clock), headers=HEADERS) as client:
        path = '/api/v3/settings/oko/global'
        source.reached(1)
        clock.now = 700
        source.unchanged(1)
        assert client.patch(path, json={'feed_refresh_minutes': 20}).status_code == 200
        clock.now = 1000
        source.unchanged(1)  # The former 15-minute deadline was postponed.
        clock.now = 1300
        source.reached(2)
        clock.now = 1400
        assert client.patch(path, json={'feed_refresh_minutes': 1}).status_code == 200
        source.reached(3)  # Already overdue: 100 seconds since the last automatic round.
        assert client.patch(path, json={'feed_refresh_minutes': 2}).status_code == 200
        clock.now = 1519
        source.unchanged(3)
        clock.now = 1520
        source.reached(4)
        assert client.patch(path, json={'feed_refresh_minutes': None}).status_code == 200
        clock.now = 2419
        source.unchanged(4)
        clock.now = 2420
        source.reached(5)
