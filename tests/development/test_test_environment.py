"""Public test factory and CLI contract checking, without external services."""
import asyncio
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient
from tests.support.reader import HEADERS


def test_factory_defaults_are_synthetic_and_accept_explicit_clients(tmp_path):
    from tests.support.app import create_app
    with TestClient(create_app(tmp_path / 'one', token='test'), headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        checked = client.post('/api/processors/codex/check').json()
        assert checked['available'] is False
        assert checked['auth'] == 'synthetic'
        assert 'синтетический' in checked['reason'].lower()
    from tests.support.codex import CodexService
    with TestClient(create_app(tmp_path / 'two', token='test', codex_client=CodexService()), headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        assert client.post('/api/processors/codex/check').json()['available'] is True


def test_contract_cli_rejects_old_inherited_signature_before_test_execution(tmp_path):
    root = Path(__file__).resolve().parents[2]
    protocol = tmp_path / 'oko/processing'
    protocol.mkdir(parents=True)
    (protocol / 'codex.py').write_text('class CodexClient:\n    async def complete(self, prompt, schema, directory, choice): ...\n')
    support = tmp_path / 'tests/support'
    support.mkdir(parents=True)
    (support / 'codex.py').write_text('from oko.processing.codex import CodexClient\nclass CodexService(CodexClient):\n    async def complete(self, prompt, schema, directory, choice): return "{}"\n')
    case = tmp_path / 'tests/test_case.py'
    case.write_text('from tests.support.codex import CodexService\nclass Parent(CodexService): pass\nclass Old(Parent):\n    async def complete(self, prompt, schema, directory): return "{}"\n')
    subprocess.run(['git', 'init', '-q'], cwd=tmp_path, check=True)
    cli = root / 'scripts/check_test_contracts.py'
    result = subprocess.run([sys.executable, str(cli)], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 1 and 'Old.complete' in result.stdout + result.stderr
    case.write_text(case.read_text().replace('schema, directory)', 'schema, directory, choice)'))
    assert subprocess.run([sys.executable, str(cli)], cwd=tmp_path).returncode == 0


def test_factory_collects_youtube_without_starting_a_real_caption_worker(tmp_path):
    import httpx
    from tests.support.app import create_app
    from tests.support.reader import wait_for
    from tests.support.youtube import feed_xml, subscribe, PNG
    def reply(request):
        if request.url.path == '/feeds/videos.xml':
            return httpx.Response(200, text=feed_xml())
        return httpx.Response(200, content=PNG, headers={'Content-Type':'image/png'})
    with TestClient(create_app(tmp_path, token='test', rss_enabled=True, rss_transport=httpx.MockTransport(reply)), headers=HEADERS) as client:
        client.patch('/api/settings/oko/global', json={'theses':True})
        subscribe(client)
        client.post('/api/tabs/reader/heartbeat')
        card = wait_for(client, lambda: (c[0] if len(c:=client.get('/api/cards').json())==1 and c[0]['complete'] and not c[0].get('loading') else None))
        assert card['captions']=='unavailable'
        assert card['theses'] is None


def test_factory_http_transport_rejects_external_hosts_before_connecting():
    import httpx
    from tests.support.app import RejectTransport
    import pytest
    async def request():
        async with httpx.AsyncClient(transport=RejectTransport()) as client:
            with pytest.raises(httpx.ConnectError, match='explicit synthetic transport'):
                await client.get('https://external.invalid/private')
    asyncio.run(request())


def test_factory_default_transport_also_blocks_local_models():
    import httpx
    import pytest
    from tests.support.app import RejectTransport
    async def request():
        async with httpx.AsyncClient(transport=RejectTransport()) as client:
            with pytest.raises(httpx.ConnectError, match='explicit synthetic transport'):
                await client.post('http://127.0.0.1:9931/v1/chat/completions', json={'private':'source'})
    asyncio.run(request())


def test_contract_cli_resolves_shared_parents_imported_from_another_file(tmp_path):
    root = Path(__file__).resolve().parents[2]
    protocol = tmp_path / 'oko/processing'
    protocol.mkdir(parents=True)
    (protocol / 'codex.py').write_text('class CodexClient:\n    async def complete(self, prompt, schema, directory, choice): ...\n')
    support = tmp_path / 'tests/support'
    support.mkdir(parents=True)
    (support / 'codex.py').write_text('from oko.processing.codex import CodexClient\nclass CodexService(CodexClient):\n    async def complete(self, prompt, schema, directory, choice): return "{}"\n')
    (support / 'parent.py').write_text('from tests.support.codex import CodexService as Client\nclass Parent(Client): pass\n')
    case = tmp_path / 'tests/test_case.py'
    case.write_text('from tests.support.parent import Parent as Base\nclass Old(Base):\n    async def complete(self, prompt, schema, directory): return "{}"\n')
    subprocess.run(['git', 'init', '-q'], cwd=tmp_path, check=True)
    result = subprocess.run([sys.executable, str(root / 'scripts/check_test_contracts.py')], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 1 and 'Old.complete' in result.stdout + result.stderr


def test_contract_cli_checks_staged_bytes_even_when_working_copy_differs(tmp_path):
    root = Path(__file__).resolve().parents[2]
    protocol = tmp_path / 'oko/processing'
    protocol.mkdir(parents=True)
    (protocol / 'codex.py').write_text('class CodexClient:\n    async def complete(self, prompt, schema, directory, choice): ...\n')
    support = tmp_path / 'tests/support'
    support.mkdir(parents=True)
    valid = 'from oko.processing.codex import CodexClient\nclass CodexService(CodexClient):\n    async def complete(self, prompt, schema, directory, choice): return "{}"\n'
    case = support / 'codex.py'
    case.write_text(valid)
    subprocess.run(['git','init','-q'],cwd=tmp_path,check=True)
    subprocess.run(['git','add','.'],cwd=tmp_path,check=True)
    case.write_text(valid.replace('directory, choice)', 'directory)'))
    command = [sys.executable,str(root/'scripts/check_test_contracts.py'),'--staged']
    assert subprocess.run(command,cwd=tmp_path).returncode == 0
    subprocess.run(['git','add','.'],cwd=tmp_path,check=True)
    case.write_text(valid)
    result = subprocess.run(command,cwd=tmp_path,capture_output=True,text=True)
    assert result.returncode == 1 and 'CodexService.complete' in result.stderr


def test_factory_does_not_contact_even_a_local_model_without_explicit_transport(tmp_path, monkeypatch):
    import httpx
    from tests.support.app import create_app
    calls=[]
    async def connect(transport, request):
        calls.append(str(request.url))
        return httpx.Response(200,json={})
    monkeypatch.setattr(httpx.AsyncHTTPTransport,'handle_async_request',connect)
    with TestClient(create_app(tmp_path,token='test'),headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        client.put('/api/processors/llamacpp',json={'endpoint':'http://127.0.0.1:9931','model':'local','local_execution':True})
        assert client.post('/api/processors/llamacpp/check').status_code == 409
    assert calls == []


def test_owned_transport_only_reaches_the_selected_fixture_origin(monkeypatch):
    import httpx
    import pytest
    from tests.support.app import OwnedServerTransport
    calls=[]
    async def connect(transport,request):
        calls.append(str(request.url))
        return httpx.Response(200,json={})
    monkeypatch.setattr(httpx.AsyncHTTPTransport,'handle_async_request',connect)
    async def requests():
        async with httpx.AsyncClient(transport=OwnedServerTransport('http://127.0.0.1:1')) as client:
            assert (await client.get('http://127.0.0.1:1/fixture')).status_code == 200
            with pytest.raises(httpx.ConnectError,match='outside the owned fixture origin'):
                await client.get('http://127.0.0.1:2/private')
    asyncio.run(requests())
    assert calls==['http://127.0.0.1:1/fixture']


def test_factory_default_empty_history_completes_without_a_real_history_worker(tmp_path):
    import inspect, pytest
    from tests.support.app import production_app
    if 'youtube_history' not in inspect.signature(production_app).parameters:
        pytest.skip('Separate YouTube history API is not present in this checkout revision')
    import httpx
    from tests.support.app import create_app
    from tests.support.reader import wait_for
    from tests.support.youtube import CHANNEL
    from tests.support.subscriptions import saved_sources
    # This scenario starts from a saved feed without initial history. The
    # single-feed add API now deliberately configures history at creation.
    saved_sources(tmp_path, [(f'https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL}', 'Канал', 'Наука')])
    transport=httpx.MockTransport(lambda request: httpx.Response(200,text='<feed xmlns="http://www.w3.org/2005/Atom"/>'))
    with TestClient(create_app(tmp_path,token='test',rss_enabled=True,rss_transport=transport),headers=HEADERS) as client:
        client.patch('/api/settings/oko/global',json={'theses':False})
        feed=client.get('/api/subscriptions').json()['feeds'][0]['id']
        client.post('/api/tabs/reader/heartbeat')
        assert client.post('/api/feeds/'+feed+'/history',json={'count':3}).status_code==200
        history=wait_for(client,lambda: (h if (h:=client.get('/api/settings/feed/'+feed).json()['feed']['initial_history']) and h['state']=='done' else None))
        assert history['loaded']==0
        assert not client.get('/api/cards').json()
