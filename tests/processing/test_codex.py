from oko.processing.codex import LocalCodex
from tests.support.codex import CodexService, configure_codex
from pathlib import Path
from fastapi.testclient import TestClient
from tests.support.app import create_app
from oko.collection.demo import DemoSource
from tests.support.reader import HEADERS, setup_reader, wait_for


def test_codex_reports_unavailable_before_any_content_is_sent(tmp_path: Path, monkeypatch):
    record = tmp_path / 'unexpected-command'
    fake_cli(tmp_path, monkeypatch, f"echo \"$*\" >> '{record}'\nexit 99\n")
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test', codex_client=LocalCodex()), headers=HEADERS) as client:
        item = setup_reader(client)
        client.patch('/api/settings/oko/global', json={'processor': 'codex', 'theses': True})
        denied = wait_for(client, lambda: (m if (m := client.get('/api/materials/' + item).json()).get('ai', {}).get('error') else None))
        assert 'Запрещена публичная' in denied['ai']['error']
        assert not record.exists()
        client.patch('/api/feeds/demo', json={'public_ai': True})
        blocked = wait_for(client, lambda: (m if 'изоляция' in ((m := client.get('/api/materials/' + item).json()).get('ai', {}).get('error') or '') else None))
        assert blocked['theses'] is None
        assert blocked['text'].startswith('Это тестовая публикация.')
        assert not list(tmp_path.glob('content/*/1/theses.md'))
        assert client.post('/api/materials/' + item + '/viewed').status_code == 200
        assert client.get('/api/materials/' + item).status_code == 404
        assert not (tmp_path / 'content' / item).exists()
        assert set(record.read_text().splitlines()) == {'--version'}


def fake_cli(tmp_path, monkeypatch, body):
    binary = tmp_path / 'bin'
    binary.mkdir()
    script = binary / 'codex'
    script.write_text('#!/bin/sh\n' + body)
    script.chmod(0o700)
    monkeypatch.setenv('PATH', str(binary))
    return script


def test_connection_check_sanitizes_cli_output_and_never_launches_inference(tmp_path, monkeypatch):
    record = tmp_path / 'commands'
    fake_cli(tmp_path, monkeypatch, f"""
echo "$*" >> '{record}'
case "$*" in
  --version) echo 'codex-cli 0.136.0' ;;
  'exec --help') echo '--ephemeral --ignore-user-config --ignore-rules --output-schema' ;;
  'login status') echo 'Logged in using ChatGPT'; echo 'secret-canary' >&2 ;;
  *) exit 99 ;;
esac
""")
    with TestClient(create_app(tmp_path / 'store', token='test', codex_client=LocalCodex()), headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        response = client.post('/api/processors/codex/check')
        assert response.status_code == 200
        status = response.json()
        assert status['available'] is False
        assert status['version'] == '0.136.0'
        assert status['auth'] == 'chatgpt'
        assert 'изоляция' in status['reason']
        assert status['limits_checked'] is False
        assert 'secret-canary' not in response.text
        assert record.read_text().splitlines() == ['--version', 'exec --help', 'login status']
        assert client.get('/api/processors/codex').json()['available'] is False


def test_missing_cli_is_a_visible_blocker_without_local_model_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv('PATH', str(tmp_path))
    with TestClient(create_app(tmp_path / 'store', source=DemoSource(), token='test', codex_client=LocalCodex()), headers=HEADERS) as client:
        setup_reader(client)
        result = client.post('/api/processors/codex/check').json()
        assert result['available'] is False
        assert 'CLI не найден' in result['reason']
        assert result['auth'] == 'unchecked'


def test_stop_cancels_only_the_owned_connection_probe(tmp_path, monkeypatch):
    import concurrent.futures
    import os
    import time

    marker = tmp_path / 'pid'
    fake_cli(tmp_path, monkeypatch, f"""
echo $$ > '{marker}'
exec /bin/sleep 30
""")
    with TestClient(create_app(tmp_path / 'store', token='test', codex_client=LocalCodex()), headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        with concurrent.futures.ThreadPoolExecutor() as pool:
            pending = pool.submit(client.post, '/api/processors/codex/check')
            wait_for(client, lambda: marker.exists() and marker.read_text().strip())
            pid = int(marker.read_text())
            started = time.monotonic()
            assert client.post('/api/stop').status_code == 200
            assert pending.result(timeout=2).status_code == 409
            assert time.monotonic() - started < 2
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                pass
            else:
                raise AssertionError('owned probe survived stop')
        assert client.post('/api/processors/codex/check').status_code == 409


def test_probe_output_limit_does_not_expose_diagnostics(tmp_path, monkeypatch):
    fake_cli(tmp_path, monkeypatch, "/usr/bin/head -c 20000 /dev/zero\n")
    with TestClient(create_app(tmp_path / 'store', token='test', codex_client=LocalCodex()), headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        response = client.post('/api/processors/codex/check')
        assert response.status_code == 200
        assert 'Проверка Codex не завершена' in response.json()['reason']
        assert len(response.content) < 1000
        assert response.json()['available'] is False


def test_allowed_codex_theses_use_contract_and_are_deleted(tmp_path):
    service = CodexService()
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test', codex_client=service), headers=HEADERS) as client:
        configure_codex(client)
        item = setup_reader(client)
        client.patch('/api/settings/oko/global', json={'processor': 'codex', 'theses': True, 'prompt_theses': 'Один тезис'})
        denied = wait_for(client, lambda: (m if (m := client.get('/api/materials/' + item).json()).get('ai', {}).get('error') else None))
        assert 'Запрещена публичная' in denied['ai']['error']
        assert not service.inputs
        client.patch('/api/feeds/demo', json={'public_ai': True})
        material = wait_for(client, lambda: (m if (m := client.get('/api/materials/' + item).json()).get('theses') else None))
        assert material['theses']['points'][0]['text'] == 'Просмотрено удаляет содержимое.'
        assert 'Один тезис' in service.inputs[0]
        assert '"generation":' in service.inputs[0]
        assert client.post('/api/materials/' + item + '/viewed').status_code == 200
        assert client.get('/api/materials/' + item).status_code == 404
        assert not (tmp_path / 'content' / item).exists()
        assert not (tmp_path / 'staging' / item).exists()


def test_codex_refuses_unknown_source_and_keeps_original(tmp_path):
    class BadSource(CodexService):
        async def complete(self, prompt, schema, directory, choice):
            return '{"points":[{"text":"Чужой тезис","sources":[{"id":"alien","url":"https://alien.example"}]}]}'
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test', codex_client=BadSource()), headers=HEADERS) as client:
        configure_codex(client)
        item = setup_reader(client)
        client.patch('/api/feeds/demo', json={'public_ai': True})
        client.patch('/api/settings/oko/global', json={'processor': 'codex', 'theses': True})
        material = wait_for(client, lambda: (m if (m := client.get('/api/materials/' + item).json()).get('ai', {}).get('error') else None))
        assert 'неизвестный источник' in material['ai']['error']
        assert material['theses'] is None
        assert material['text']
        assert not list(tmp_path.glob('content/*/1/theses.md'))


def test_late_codex_result_after_permission_change_is_not_published(tmp_path):
    import asyncio
    import threading
    entered, release = threading.Event(), threading.Event()
    class Held(CodexService):
        async def complete(self, prompt, schema, directory, choice):
            entered.set()
            try:
                while not release.is_set():
                    await asyncio.sleep(.01)
            except asyncio.CancelledError:
                while not release.is_set():
                    await asyncio.sleep(.01)
            return await super().complete(prompt, schema, directory, choice)
    service=Held()
    with TestClient(create_app(tmp_path,source=DemoSource(),token='test',codex_client=service),headers=HEADERS) as client:
        configure_codex(client)
        item=setup_reader(client)
        client.patch('/api/feeds/demo',json={'public_ai':True})
        client.patch('/api/settings/oko/global',json={'processor':'codex','theses':True})
        assert entered.wait(2)
        try:
            client.patch('/api/feeds/demo',json={'public_ai':False})
            before=client.get('/api/materials/'+item).json()['theses']
        finally:
            release.set()
        denied=wait_for(client,lambda: (m if 'Запрещена публичная' in ((m:=client.get('/api/materials/'+item).json()).get('ai',{}).get('error') or '') else None))
        assert before is None and denied['theses'] is None


def test_completed_codex_theses_survive_restart_without_another_call(tmp_path):
    service=CodexService()
    with TestClient(create_app(tmp_path,source=DemoSource(),token='test',codex_client=service),headers=HEADERS) as client:
        configure_codex(client)
        item=setup_reader(client)
        client.patch('/api/feeds/demo',json={'public_ai':True})
        client.patch('/api/settings/oko/global',json={'processor':'codex','theses':True})
        wait_for(client,lambda: all(c.get('theses') for c in client.get('/api/cards').json()))
        count=len(service.inputs)
    with TestClient(create_app(tmp_path,source=DemoSource(),token='test',codex_client=service),headers=HEADERS) as client:
        client.post('/api/tabs/reader/heartbeat')
        assert client.get('/api/materials/'+item).json()['theses']
        assert len(service.inputs)==count


def test_codex_cancellation_on_view_keeps_late_result_deleted(tmp_path):
    import asyncio
    import threading
    entered, release = threading.Event(), threading.Event()
    class Held(CodexService):
        async def complete(self, prompt, schema, directory, choice):
            entered.set()
            try:
                while not release.is_set():
                    await asyncio.sleep(.01)
            except asyncio.CancelledError:
                while not release.is_set():
                    await asyncio.sleep(.01)
            return await super().complete(prompt, schema, directory, choice)
    service=Held()
    with TestClient(create_app(tmp_path,source=DemoSource(),token='test',codex_client=service),headers=HEADERS) as client:
        configure_codex(client)
        item=setup_reader(client)
        client.patch('/api/feeds/demo',json={'public_ai':True})
        client.patch('/api/settings/oko/global',json={'processor':'codex','theses':True})
        assert entered.wait(2)
        try:
            assert client.post('/api/materials/'+item+'/viewed').status_code==200
        finally:
            release.set()
        assert client.get('/api/materials/'+item).status_code==404
        assert not (tmp_path/'content'/item).exists()
        assert not (tmp_path/'staging'/item).exists()


def test_missing_login_is_reported_as_authorization_in_material(tmp_path,monkeypatch):
    fake_cli(tmp_path,monkeypatch,"""
case "$*" in
  --version) echo 'codex-cli 0.136.0' ;;
  'exec --help') echo '--ephemeral --ignore-user-config --ignore-rules --output-schema' ;;
  'login status') exit 1 ;;
  *) exit 99 ;;
esac
""")
    with TestClient(create_app(tmp_path/'store',source=DemoSource(),token='test', codex_client=LocalCodex()),headers=HEADERS) as client:
        item=setup_reader(client)
        client.patch('/api/feeds/demo',json={'public_ai':True})
        client.patch('/api/settings/oko/global',json={'processor':'codex','theses':True})
        material=wait_for(client,lambda: (m if (m:=client.get('/api/materials/'+item).json()).get('ai',{}).get('error') else None))
        assert 'Авторизация' in material['ai']['error']
        assert material['theses'] is None


def test_codex_unknown_quota_prevents_source_transfer(tmp_path):
    class UnknownQuota(CodexService):
        async def check(self):
            return dict(available=True,local=False,auth='chatgpt',version='0.136.0',reason='',limits_checked=False)
    service=UnknownQuota()
    with TestClient(create_app(tmp_path,source=DemoSource(),token='test',codex_client=service),headers=HEADERS) as client:
        item=setup_reader(client)
        client.patch('/api/feeds/demo',json={'public_ai':True})
        client.patch('/api/settings/oko/global',json={'processor':'codex','theses':True})
        material=wait_for(client,lambda: (m if (m:=client.get('/api/materials/'+item).json()).get('theses') or m.get('ai',{}).get('error') else None))
        assert not service.inputs
        assert 'Лимиты' in material['ai']['error']


def test_codex_exhausted_quota_is_visible_without_source_transfer(tmp_path):
    class Exhausted(CodexService):
        async def check(self):
            return dict(available=False,local=False,auth='chatgpt',version='0.136.0',reason='Лимит Codex исчерпан; ожидание сброса',limits_checked=True)
    service=Exhausted()
    with TestClient(create_app(tmp_path,source=DemoSource(),token='test',codex_client=service),headers=HEADERS) as client:
        item=setup_reader(client)
        client.patch('/api/feeds/demo',json={'public_ai':True})
        client.patch('/api/settings/oko/global',json={'processor':'codex','theses':True})
        material=wait_for(client,lambda: (m if (m:=client.get('/api/materials/'+item).json()).get('ai',{}).get('error') else None))
        assert 'Лимит Codex исчерпан' in material['ai']['error']
        assert not service.inputs and material['theses'] is None


def test_large_cli_output_cannot_block_connection_check(tmp_path,monkeypatch):
    import concurrent.futures
    fake_cli(tmp_path,monkeypatch,"/usr/bin/head -c 10000000 /dev/zero\n")
    with TestClient(create_app(tmp_path/'store',token='test', codex_client=LocalCodex()),headers=HEADERS) as client:
        client.post('/api/tabs/r/heartbeat')
        with concurrent.futures.ThreadPoolExecutor() as pool:
            pending=pool.submit(client.post,'/api/processors/codex/check')
            try:
                response=pending.result(timeout=3)
            finally:
                if not pending.done():
                    client.post('/api/stop')
                    pending.result(timeout=3)
            assert response.status_code==200
            assert 'Проверка Codex не завершена' in response.json()['reason']
