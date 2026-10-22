"""A clock barrier must observe real Runtime progress, including failure to progress."""
import pytest
from fastapi.testclient import TestClient
from tests.support.app import create_app
from tests.support.runtime_clock import ReaderClock


def test_clock_barrier_requires_runtime_progress_and_restores_observer(tmp_path):
    clock = ReaderClock()
    app = create_app(tmp_path, api_only=True, token='test', clock=clock)
    with TestClient(app, headers={'Authorization':'Bearer test'}) as client:
        original = app.state.runtime.reader_advance
        clock.advance_and_wait(client, 65)
        assert app.state.runtime.reader_advance is original
        assert client.get('/api/v3/status').json()['runtime_state'] == 'running'
        assert client.post('/api/v3/stop').status_code == 200
        with pytest.raises(TimeoutError):
            clock.advance_and_wait(client, 1)
        assert app.state.runtime.reader_advance is original
