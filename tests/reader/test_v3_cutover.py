"""Retired API security and mutation isolation at the real HTTP boundary."""
from fastapi.testclient import TestClient
from tests.support.app import create_app


def test_retired_api_is_typed_gone_after_security_and_never_executes(tmp_path):
    with TestClient(create_app(tmp_path, token='test', api_only=True)) as client:
        for method, path in [('GET','/api/status'),('POST','/api/stop'),('PATCH','/api/settings/oko/global'),('POST','/api/telegram/connect'),('GET','/api'),('GET','/api/unknown')]:
            assert client.request(method,path).status_code == 403
            assert client.request(method,path,headers={'Authorization':'Bearer test','Origin':'https://foreign.example'}).status_code == 403
            reply=client.request(method,path,headers={'Authorization':'Bearer test'},json={'theses':True})
            assert reply.status_code == 410, reply.text
            assert reply.json()=={'code':'api_version_removed','message':'Прежний API отключён; используйте /api/v3','details':{'api_version':'2.0.0'},'diagnostic_id':None,'command_id':None}
            assert reply.headers['cache-control']=='no-store'
        status=client.get('/api/v3/status',headers={'Authorization':'Bearer test'}).json()
        assert status['runtime_state']=='running'
        settings=client.get('/api/v3/settings/oko/global',headers={'Authorization':'Bearer test'}).json()
        assert settings['overrides']=={}
