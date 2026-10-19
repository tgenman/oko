"""Automatic collection observed through HTTP commands and a synthetic source."""
import threading
import time

from fastapi.testclient import TestClient

from oko.collection.contracts import DiscoveryResult, SourceObservation, FetchRequirements
from tests.support.app import create_app
from tests.support.reader import HEADERS, Clock


class CollectionProbe:
    fetch_requirements = FetchRequirements()
    changes = None

    def __init__(self):
        self.rounds = []
        self.condition = threading.Condition()

    async def discover(self, cursor):
        with self.condition:
            self.rounds.append(cursor)
            self.condition.notify_all()
        return DiscoveryResult((), str(len(self.rounds)), SourceObservation())

    async def fetch(self, publication, resources):
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
        assert client.post('/api/v3/collect', json={'scope': 'feed', 'feed_id': 'demo'}).status_code == 200
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


def test_interval_save_does_not_cancel_in_flight_collection(tmp_path):
    import asyncio
    from oko.collection.demo import DemoSource
    class HeldSource(DemoSource):
        started = threading.Event()
        release = threading.Event()
        cancelled = threading.Event()
        async def discover(self, cursor):
            self.started.set()
            try:
                while not self.release.is_set():
                    await asyncio.sleep(.01)
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
            return await super().discover(cursor)
    source = HeldSource()
    with TestClient(create_app(tmp_path, token='test', api_only=True, source=source), headers=HEADERS) as client:
        assert source.started.wait(2)
        try:
            response = client.patch('/api/v3/settings/oko/global', json={'feed_refresh_minutes': 30})
            assert response.status_code == 200, response.text
            assert not source.cancelled.is_set()
        finally:
            source.release.set()
        from tests.support.reader import wait_for
        wait_for(client, lambda: len(client.get('/api/v3/cards').json()) == 3)


def test_interval_change_preserves_running_and_ready_theses(tmp_path):
    import asyncio
    from oko.collection.demo import DemoSource
    from tests.support.codex import CodexService
    from tests.support.reader import wait_for
    class HeldModel(CodexService):
        started = threading.Event()
        release = threading.Event()
        cancelled = threading.Event()
        async def complete(self, prompt, schema, directory, choice):
            self.started.set()
            try:
                while not self.release.is_set():
                    await asyncio.sleep(.01)
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
            return await super().complete(prompt, schema, directory, choice)
    model = HeldModel()
    with TestClient(create_app(tmp_path, token='test', api_only=True, source=DemoSource(), codex_client=model), headers=HEADERS) as client:
        wait_for(client, lambda: len(client.get('/api/v3/cards').json()) == 3)
        config = {kind: dict(model='synthetic', reasoning_effort='low', fast=False) for kind in ('filter', 'theses')}
        assert client.put('/api/v3/processors/codex', json=config).status_code == 200
        assert client.patch('/api/v3/feeds/demo', json={'public_ai': True}).status_code == 200
        path = '/api/v3/settings/oko/global'
        assert client.patch(path, json={'theses': True}).status_code == 200
        assert model.started.wait(2)
        try:
            assert client.patch(path, json={'feed_refresh_minutes': 30}).status_code == 200
            assert not model.cancelled.is_set()
        finally:
            model.release.set()
        cards = wait_for(client, lambda: (rows if len(rows := client.get('/api/v3/cards').json()) == 3 and all(c['theses']['result'] for c in rows) else None))
        before = [c['theses']['result'] for c in cards]
        count = len(model.inputs)
        assert client.patch(path, json={'feed_refresh_minutes': None}).status_code == 200
        time.sleep(.12)
        assert [c['theses']['result'] for c in client.get('/api/v3/cards').json()] == before
        assert len(model.inputs) == count
