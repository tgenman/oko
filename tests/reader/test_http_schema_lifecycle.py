"""Shared HTTP schemas must not share execution, payloads or reader state."""
import time
import gc
import weakref

import pytest

from fastapi.testclient import TestClient

from tests.support.app import create_app


def test_live_readers_keep_tokens_settings_diagnostics_and_shutdown_independent(tmp_path):
    first = create_app(tmp_path/'first', api_only=True, token='first')
    second = create_app(tmp_path/'second', api_only=True, token='second')
    with TestClient(first, headers={'Authorization':'Bearer first'}) as a, TestClient(second, headers={'Authorization':'Bearer second'}) as b:
        original = b.get('/api/v3/settings/oko/global').json()
        assert a.patch('/api/v3/settings/oko/global', json={'prompt_theses':'first only'}).status_code == 200
        assert b.get('/api/v3/settings/oko/global').json() == original
        assert a.post('/api/v3/diagnostics', json={'source':'schema probe','message':'first only'}).status_code == 200
        assert b.get('/api/v3/diagnostics').json()['groups'] == []
        assert b.get('/api/v3/status', headers={'Authorization':'Bearer first'}).status_code == 403
        assert a.post('/api/v3/stop').status_code == 200
        assert a.get('/api/v3/status').json()['runtime_state'] == 'stopped'
        assert b.get('/api/v3/status').json()['runtime_state'] == 'running'
        assert b.patch('/api/v3/settings/oko/global', json={'prompt_theses':'second only'}).status_code == 200
