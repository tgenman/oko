"""The exported contract is the same interface as actual reader responses."""
import json
import subprocess
import sys

from fastapi.testclient import TestClient
from tests.support.app import create_app
from oko.collection.demo import DemoSource
from tests.support.reader import HEADERS, setup_reader


def test_exported_reading_contract_rejects_missing_fields_and_wrong_types(tmp_path):
    result = subprocess.run([sys.executable, '-m', 'oko.reader.contracts'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    schemas = json.loads(result.stdout)
    assert set(schemas) == {'navigation', 'cards', 'material', 'status'}
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test'), headers=HEADERS) as client:
        setup_reader(client, theses=False)
        from oko.reader.contracts import CONTRACTS
        navigation = client.get('/api/v3/reading-composition').json()
        assert CONTRACTS['navigation'].dump_python(CONTRACTS['navigation'].validate_python(navigation), mode='json', by_alias=True, exclude_unset=True) == navigation
        cards = client.get('/api/v3/cards').json()
        assert CONTRACTS['cards'].dump_python(CONTRACTS['cards'].validate_python(cards), mode='json', by_alias=True, exclude_unset=True) == cards
        import pytest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            CONTRACTS['navigation'].validate_python({**navigation, 'count': '3'})
        with pytest.raises(ValidationError):
            CONTRACTS['cards'].validate_python({**cards, 'items':[{key: value for key, value in cards['items'][0].items() if key != 'id'}]})


def test_material_and_stopped_status_preserve_wire_fields(tmp_path):
    from oko.reader.contracts import CONTRACTS
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test'), headers=HEADERS) as client:
        identity = setup_reader(client, theses=False)
        body = client.get('/api/v3/materials/' + identity).json()
        assert CONTRACTS['material'].dump_python(CONTRACTS['material'].validate_python(body), mode='json', by_alias=True, exclude_unset=True) == body
        assert body['theses']['attempt']['state'] == 'disabled'
        assert body['theses']['result'] is None
        assert 'previous_versions' not in body['content']
        status = client.get('/api/v3/status/details').json()
        assert CONTRACTS['status'].dump_python(CONTRACTS['status'].validate_python(status), mode='json', by_alias=True, exclude_unset=True) == status
        assert status['runtime_state'] == 'running'
        client.post('/api/v3/stop')
        stopped = client.get('/api/v3/status/details').json()
        assert CONTRACTS['status'].dump_python(CONTRACTS['status'].validate_python(stopped), mode='json', by_alias=True, exclude_unset=True) == stopped
        assert stopped['runtime_state'] == 'stopped'
        assert stopped['youtube']['admission'] == 'ready'
        assert 'review_restarts' not in stopped


def test_work_status_rejects_history_without_progress_fields(tmp_path):
    import pytest
    from pydantic import ValidationError
    from oko.reader.contracts import CONTRACTS
    with TestClient(create_app(tmp_path, token='test'), headers=HEADERS) as client:
        status = client.get('/api/v3/status/details').json()
        with pytest.raises(ValidationError):
            CONTRACTS['status'].validate_python({**status, 'initial_histories': [{}]})
