"""Linux boundary uses real bubblewrap/IPC and synthetic executables only."""
import asyncio
import json
import os
from pathlib import Path
import signal
import socket
import sys
import tempfile
import threading
import time

import httpx
import pytest
import uvicorn

from oko.processing.codex import run_isolated
from oko.processing.codex_bridge.client import RemoteCodex
from oko.processing.codex_bridge.protocol import BridgeConfig
from oko.processing.codex_bridge.service import create_app
from oko.processing.codex_linux import prefix
from oko.processing.execution import CodexChoice


class SyntheticCodex:
    async def check(self):
        return dict(available=True, auth='chatgpt', limits_checked=True)
    async def catalog(self):
        return [dict(model='synthetic', reasoning_efforts=['low'], fast_supported=False)]
    async def complete(self, prompt, schema, directory, choice):
        return '{"text":"isolated result"}'


def test_authenticated_unix_socket_uses_the_same_bounded_protocol(tmp_path):
    token = tmp_path / 'token'
    token.write_text('x' * 48)
    token.chmod(0o600)
    # macOS Unix socket names have a short kernel limit.
    with tempfile.TemporaryDirectory(prefix='oko-ipc-', dir='/tmp') as folder:
        path = Path(folder) / 'helper.sock'
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(str(path))
        path.chmod(0o660)
        config = BridgeConfig(token_file=token, scratch_root=tmp_path/'scratch', release='synthetic', socket_path=path)
        server = uvicorn.Server(uvicorn.Config(create_app(config, adapter_factory=SyntheticCodex), access_log=False, log_level='error'))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
        thread.start()
        try:
            for _ in range(200):
                if server.started:break
                time.sleep(.01)
            assert server.started
            async def scenario():
                transport = httpx.AsyncHTTPTransport(uds=str(path))
                async with httpx.AsyncClient(transport=transport, base_url='http://localhost') as denied:
                    assert (await denied.get('/v1/health')).status_code == 401
                client = RemoteCodex('http://localhost', 'x'*48, socket_path=path)
                assert (await client.check())['auth'] == 'chatgpt'
                assert (await client.catalog())[0]['model'] == 'synthetic'
                assert await client.complete('synthetic', {}, tmp_path/'unused', CodexChoice(model='synthetic', reasoning_effort='low')) == '{"text":"isolated result"}'
            asyncio.run(scenario())
        finally:
            server.should_exit = True
            thread.join(10)
            listener.close()
            assert not thread.is_alive()


def linux_only():
    if sys.platform != 'linux' or not Path('/usr/bin/bwrap').is_file():
        pytest.skip('Requires the Linux integration job with distribution bubblewrap')


def executable(root, code):
    binary = root/'runtime/bin/codex'
    binary.parent.mkdir(parents=True)
    binary.write_text('#!/usr/bin/python3\n'+code)
    binary.chmod(0o700)
    work, auth = root/'work', root/'auth'
    work.mkdir(mode=0o700)
    auth.mkdir(mode=0o700)
    return binary, work, auth


def test_linux_sandbox_denies_other_files_but_refreshes_private_auth(tmp_path, monkeypatch):
    linux_only()
    secret = tmp_path/'outside-secret'
    secret.write_text('outside-canary')
    binary, work, auth = executable(tmp_path, '''import os, pathlib
outside=pathlib.Path(__import__('sys').argv[-1])
assert not outside.exists(), 'outside data was exposed'
assert 'OPENAI_API_KEY' not in os.environ
assert 'CODEX_API_KEY' not in os.environ
pathlib.Path('result').write_text('owned scratch')
(pathlib.Path(os.environ['CODEX_HOME'])/'auth.json').write_text('refreshed')
print('ISOLATION_OK')
''')
    monkeypatch.setenv('CODEX_HOME', str(auth))
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-forbidden-key')
    monkeypatch.setenv('CODEX_API_KEY', 'synthetic-forbidden-key')
    code, output = asyncio.run(run_isolated([*prefix(binary, work, auth), str(secret)], b'', work))
    assert code == 0, output.decode(errors='replace')
    assert output.strip() == b'ISOLATION_OK'
    assert (work/'result').read_text() == 'owned scratch'
    assert (auth/'auth.json').read_text() == 'refreshed'
    assert secret.read_text() == 'outside-canary'


def test_linux_cancellation_reaps_the_owned_namespace_and_children(tmp_path, monkeypatch):
    linux_only()
    binary, work, auth = executable(tmp_path, '''import pathlib,subprocess,time
subprocess.Popen(['/bin/sleep','60'])
pathlib.Path('started').touch()
time.sleep(60)
''')
    monkeypatch.setenv('CODEX_HOME', str(auth))
    original = asyncio.create_subprocess_exec
    owned = []
    async def spawn(*args, **kwargs):
        child = await original(*args, **kwargs)
        owned.append(child.pid)
        return child
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    async def scenario():
        task = asyncio.create_task(run_isolated(prefix(binary, work, auth), b'', work))
        try:
            for _ in range(300):
                if (work/'started').exists():break
                if task.done():
                    assert False, await task
                await asyncio.sleep(.01)
            assert (work/'started').exists()
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    asyncio.run(scenario())
    assert owned
    for pid in owned:
        deadline=time.monotonic()+2
        while True:
            try:os.killpg(pid,0)
            except ProcessLookupError:break
            assert time.monotonic()<deadline, 'owned namespace group still exists'
            time.sleep(.01)  # Kernel/init reaping follows SIGKILL asynchronously.


def test_credential_view_excludes_config_rules_and_preserves_refresh(tmp_path):
    from oko.processing.codex_linux import credentials
    original = tmp_path/'persistent'
    original.mkdir(mode=0o700)
    initial = dict(auth_mode='chatgpt', OPENAI_API_KEY=None, tokens=dict(access_token='synthetic-old'))
    (original/'auth.json').write_text(json.dumps(initial))
    (original/'config.toml').write_text('model_provider="untrusted"')
    (original/'AGENTS.md').write_text('untrusted rules')
    (original/'skills').mkdir()
    with credentials(original) as view:
        assert view != original
        assert {path.name for path in view.iterdir()} == {'auth.json'}
        updated = {**initial, 'tokens':dict(access_token='synthetic-refreshed')}
        (view/'auth.json').write_text(json.dumps(updated))
    assert json.loads((original/'auth.json').read_text()) == updated
    assert (original/'config.toml').read_text() == 'model_provider="untrusted"'
    assert not view.exists()


def test_credential_refresh_cannot_overwrite_a_concurrent_new_login(tmp_path):
    from oko.processing.codex_linux import credentials
    from oko.processing.contracts import ProcessingError
    original=tmp_path/'persistent'
    original.mkdir(mode=0o700)
    (original/'auth.json').write_text(json.dumps(dict(auth_mode='chatgpt',tokens={})))
    with pytest.raises(ProcessingError,match='изменилась'):
        with credentials(original) as view:
            (view/'auth.json').write_text(json.dumps(dict(auth_mode='chatgpt',tokens={'access_token':'stale'})))
            (original/'auth.json').write_text('new operator login')
    assert (original/'auth.json').read_text() == 'new operator login'


def test_cancelled_lease_does_not_hide_unexpected_supervisor_loss(tmp_path):
    from oko.processing.codex_bridge.service import Bridge, Job
    token=tmp_path/'token';token.write_text('x'*48);token.chmod(0o600)
    called=[]
    bridge=Bridge(BridgeConfig(token_file=token,scratch_root=tmp_path/'scratch',release='synthetic'),SyntheticCodex,lambda:called.append(True))
    class Control:
        def send(self,value):pass
        def poll(self):return False
        def close(self):pass
    class Process:
        def is_alive(self):return False
        def join(self,timeout=0):pass
    job=Job('session','digest',Control(),Process(),time.time()+10,time.monotonic()+10,time.time()+20)
    bridge.jobs['owned']=job
    (tmp_path/'scratch/active.json').write_text('{}')
    bridge.cancel(job)
    bridge.update()
    assert job.state=='error' and bridge.supervisor_lost
    asyncio.run(bridge.watch())
    assert called==[True]


def test_refreshed_token_survives_supervisor_exit_before_context_commit(tmp_path):
    import subprocess
    from oko.processing.codex_linux import credentials
    original=tmp_path/'persistent';original.mkdir(mode=0o700)
    (original/'auth.json').write_text(json.dumps(dict(auth_mode='chatgpt',tokens={'access_token':'old'})))
    program='''import os,json,sys
from pathlib import Path
from oko.processing.codex_linux import credentials
with credentials(Path(sys.argv[1])) as view:
    (view/'auth.json').write_text(json.dumps(dict(auth_mode='chatgpt',tokens={'access_token':'refreshed'})))
    os._exit(79)
'''
    result=subprocess.run([sys.executable,'-c',program,str(original)],cwd=Path(__file__).resolve().parents[2])
    assert result.returncode==79
    assert json.loads((original/'auth.json').read_text())['tokens']['access_token']=='old'
    assert list(original.glob('.pending-*'))
    with credentials(original) as recovered:
        assert json.loads((recovered/'auth.json').read_text())['tokens']['access_token']=='refreshed'
    assert not list(original.glob('.pending-*'))
    assert json.loads((original/'auth.json').read_text())['tokens']['access_token']=='refreshed'
