"""Public test factory and CLI contract checking, without external services."""
from tests.support.publications import captions_state,loading
from tests.support.cards import read_all_cards, read_publications_for_cards
from tests.support.presence import presence, close_presence
import asyncio
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient
from tests.support.reader import HEADERS


def test_factory_defaults_are_synthetic_and_accept_explicit_clients(tmp_path):
    from tests.support.app import create_app
    with TestClient(create_app(tmp_path / 'one', token='test'), headers=HEADERS) as client:
        presence(client, 'reader')
        checked = client.post('/api/v3/processors/codex/check').json()
        assert checked['state']=='unavailable'
        assert checked['auth'] == 'unknown'
        assert 'синтетический' in checked['message'].lower()
    from tests.support.codex import CodexService
    with TestClient(create_app(tmp_path / 'two', token='test', codex_client=CodexService()), headers=HEADERS) as client:
        presence(client, 'reader')
        assert client.post('/api/v3/processors/codex/check').json()['state']=='available'


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
        client.patch('/api/v3/settings/oko/global', json={'theses':True})
        subscribe(client)
        presence(client, 'reader')
        card = wait_for(client, lambda: (c[0] if len(c:=read_publications_for_cards(client, '/api/v3/cards'))==1 and c[0]['content'].get('blocks') is not None and not loading(c[0]) and c[0]['summarized_content'].get('points') is not None else None))
        assert captions_state(card)=='unavailable'
        assert card['summarized_content']['points']==[]


def test_factory_http_transport_rejects_external_hosts_before_connecting():
    import httpx
    from tests.support.app import RejectTransport
    import pytest
    async def request():
        async with httpx.AsyncClient(transport=RejectTransport()) as client:
            with pytest.raises(httpx.ConnectError, match='explicit synthetic transport'):
                await client.get('https://external.invalid/private')
    asyncio.run(request())


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
        presence(client, 'reader')
        client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://127.0.0.1:9931','model':'local','local_execution':True})
        assert client.post('/api/v3/processors/llamacpp/check').status_code == 503
    assert calls == []
