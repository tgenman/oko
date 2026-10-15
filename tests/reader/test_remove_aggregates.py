"""Upgrade a synthetic reader snapshot captured before aggregate removal."""
import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from tests.support.app import create_app


def test_upgrade_preserves_individual_results_and_removes_group_work(tmp_path):
    fixture = json.loads((Path(__file__).parents[1] / 'fixtures/legacy-aggregate-reader.json').read_text())
    with sqlite3.connect(tmp_path / 'state.sqlite') as db:
        db.executescript(fixture['sql'])
    for name, contents in fixture['files'].items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes.fromhex(contents))
    for _ in range(2):
        with TestClient(create_app(tmp_path, token='test'), headers={'Authorization': 'Bearer test'}) as client:
            settings = client.get('/api/settings/oko/global').json()
            assert set(settings['effective']) == {'originals', 'theses', 'prompt_theses', 'prompt_filters', 'processor'}
            cards = client.get('/api/cards').json()
            assert {c['id'] for c in cards} == {m['id'] for m in fixture['expected']}
            for expected in fixture['expected']:
                material = client.get('/api/materials/' + expected['id']).json()
                for field in ('text', 'version', 'theses', 'provenance', 'filter', 'images'):
                    assert material[field] == expected[field], field
            assert client.post('/api/digests/folder/demo', json={}).status_code == 404
            assert client.get('/api/materials/event-legacy').status_code == 404
            assert client.get('/api/materials/digest-legacy').status_code == 404
            status = client.get('/api/status').json()
            assert not any(j['kind'] in ('events', 'digest') for j in status['jobs'])
            assert set(client.get('/api/processors/codex').json()['config']) == {'filter', 'theses'}
        assert not (tmp_path / 'events').exists()
        assert not (tmp_path / 'digests').exists()


def test_interrupted_upgrade_recovers_accepted_results(tmp_path):
    fixture = json.loads((Path(__file__).parents[1] / 'fixtures/legacy-aggregate-reader.json').read_text())
    with sqlite3.connect(tmp_path / 'state.sqlite') as db:
        db.executescript(fixture['sql'])
    for name, contents in fixture['files'].items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes.fromhex(contents))
    failed = []
    def fault(point):
        if point == 'after_aggregate_result_relabel':
            failed.append(point)
            raise OSError('synthetic interruption after atomic relabel')
    with TestClient(create_app(tmp_path, token='test', fault=fault), headers={'Authorization': 'Bearer test'}) as client:
        assert failed
        assert client.get('/api/status').json()['error']
    with TestClient(create_app(tmp_path, token='test'), headers={'Authorization': 'Bearer test'}) as client:
        for expected in fixture['expected']:
            material = client.get('/api/materials/' + expected['id']).json()
            assert material['theses'] == expected['theses']
            assert material['filter'] == expected['filter']
