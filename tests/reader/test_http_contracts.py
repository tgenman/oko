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
    assert set(schemas) == {'navigation', 'cards', 'material', 'version', 'status'}
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test'), headers=HEADERS) as client:
        setup_reader(client, theses=False)
        from oko.reader.contracts import CONTRACTS
        navigation = client.get('/api/navigation').json()
        assert CONTRACTS['navigation'].validate_python(navigation) == navigation
        cards = client.get('/api/cards').json()
        assert CONTRACTS['cards'].validate_python(cards) == cards
        import pytest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            CONTRACTS['navigation'].validate_python({**navigation, 'count': '3'})
        with pytest.raises(ValidationError):
            CONTRACTS['cards'].validate_python([{key: value for key, value in cards[0].items() if key != 'id'}])


def test_material_and_stopped_status_preserve_wire_fields(tmp_path):
    from oko.reader.contracts import CONTRACTS
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test'), headers=HEADERS) as client:
        identity = setup_reader(client, theses=False)
        body = client.get('/api/materials/' + identity).json()
        assert CONTRACTS['material'].validate_python(body) == body
        assert body['ai'] is None
        assert body['provenance'] is None
        assert body['previous'] == []
        status = client.get('/api/status').json()
        assert CONTRACTS['status'].validate_python(status) == status
        assert not status['stopped']
        client.post('/api/stop')
        stopped = client.get('/api/status').json()
        assert CONTRACTS['status'].validate_python(stopped) == stopped
        assert stopped['stopped']
        assert stopped['youtube'] is None
        assert 'review_restarts' not in stopped
