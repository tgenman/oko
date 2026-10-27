"""Lexical HTTP scalars remain distinct from JSON primitive validation."""
from fastapi import Request
from fastapi.testclient import TestClient
from tests.support.app import create_app
from oko.reader.api_v3 import scalar_boolean

HEADERS = {'Authorization': 'Bearer synthetic'}


def test_textual_query_validates_before_domain_admission(tmp_path):
    app = create_app(tmp_path, api_only=True, token='synthetic')
    @app.get('/api/v3/lexical-fixture')
    async def lexical(request: Request):
        return {'flag': scalar_boolean(request, 'flag')}
    with TestClient(app, headers=HEADERS) as client:
        assert client.get('/api/v3/lexical-fixture?flag=true').json() == {'flag': True}
        assert client.get('/api/v3/lexical-fixture?flag=false').json() == {'flag': False}
        for query in ['flag=True', 'flag=1', 'flag=false&flag=true', 'flag=']:
            response = client.get('/api/v3/lexical-fixture?' + query)
            assert response.status_code == 422
            assert response.json()['code'] == 'request_invalid'


def test_lifetime_intervals_are_positive_finite_and_renew_is_less_than_lease():
    import pytest
    from oko.reader.lifetime import Lifetime
    for lease, grace in [(0, 15), (-1, 15), (6, 0), (float('inf'), 15), (float('nan'), 15), (5e-324, 15)]:
        with pytest.raises(ValueError):
            Lifetime('client_presence', clock=lambda: 0, wall_clock=lambda: 0, lease=lease, grace=grace)
    lifetime = Lifetime('client_presence', clock=lambda: 0, wall_clock=lambda: 0)
    assert (lifetime.lease_seconds, lifetime.grace_seconds, lifetime.renew_after_seconds) == (6, 15, 2)
    assert 0 < lifetime.renew_after_seconds < lifetime.lease_seconds
