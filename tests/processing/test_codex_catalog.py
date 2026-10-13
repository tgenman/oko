"""Catalogue adapter protocol boundary; no inference or account sources."""
import asyncio
import json
from pathlib import Path

from oko.processing import codex_catalog


def model(name='synthetic', **extra):
    value = dict(model=name, displayName='Synthetic', hidden=False, isDefault=True,
                supportedReasoningEfforts=[{'reasoningEffort': 'medium'}],
                defaultReasoningEffort='medium')
    value.update(extra)
    return value


class Server:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.stdout = None
        self.stdin = self
        self.pid = 987654321
        self.returncode = None
        self.messages = []
        self.closed = False

    def write(self, data):
        message = json.loads(data)
        self.messages.append(message)
        if message['method'] == 'initialize':
            result = {}
        elif message['method'] == 'model/list':
            result = next(self.pages)
        else:
            return
        self.stdout.feed_data((json.dumps(dict(id=message['id'], result=result)) + '\n').encode())

    async def drain(self):
        pass

    async def communicate(self):
        self.closed = True
        return b'', b''


def transport(monkeypatch, pages):
    server = Server(pages)
    calls = []

    async def spawn(*args, **options):
        server.stdout = asyncio.StreamReader()
        calls.append((args, options))
        return server

    monkeypatch.setattr(codex_catalog, 'supported_binary', lambda: Path('/synthetic/codex'))
    monkeypatch.setattr(codex_catalog.asyncio, 'create_subprocess_exec', spawn)
    monkeypatch.setattr(codex_catalog.os, 'killpg', lambda pid, sig: None)
    return server, calls


def test_catalog_reads_every_page_and_normalises_capabilities_without_inference(monkeypatch):
    server, calls = transport(monkeypatch, [
        dict(data=[model(serviceTiers=[dict(id='priority', name='Fast', description='speed')])], nextCursor='page2'),
        dict(data=[model('standard')], nextCursor=None),
    ])
    result = asyncio.run(codex_catalog.catalog())
    assert result == [
        dict(model='synthetic', display_name='Synthetic', default=True,
             reasoning_efforts=['medium'], default_reasoning_effort='medium', fast_supported=True),
        dict(model='standard', display_name='Synthetic', default=True,
             reasoning_efforts=['medium'], default_reasoning_effort='medium', fast_supported=False),
    ]
    assert [message['method'] for message in server.messages] == ['initialize', 'initialized', 'model/list', 'model/list']
    assert server.messages[-1]['params']['cursor'] == 'page2'
    assert calls[0][0][0] == '/usr/bin/sandbox-exec'
    assert calls[0][1]['start_new_session'] is True
    assert calls[0][1]['stderr'] == asyncio.subprocess.DEVNULL
    assert server.closed


def test_catalog_sanitises_failure_and_terminates_owned_process(monkeypatch):
    import pytest
    from oko.processing.contracts import ProcessingError

    server, _ = transport(monkeypatch, [dict(data=[model(defaultReasoningEffort='alien')])])
    with pytest.raises(ProcessingError) as caught:
        asyncio.run(codex_catalog.catalog())
    assert 'alien' not in caught.value.label
    assert 'каталог' in caught.value.label
    assert server.closed


def test_catalog_rejects_cursor_loop_and_partial_results(monkeypatch):
    import pytest
    from oko.processing.contracts import ProcessingError

    server, _ = transport(monkeypatch, [
        dict(data=[model()], nextCursor='loop'), dict(data=[], nextCursor='loop')])
    with pytest.raises(ProcessingError):
        asyncio.run(codex_catalog.catalog())
    assert server.closed


def test_catalog_fast_is_capability_not_a_model_name(monkeypatch):
    transport(monkeypatch, [dict(data=[
        model('gpt-fast', additionalSpeedTiers=['ultrafast']),
        model('legacy', additionalSpeedTiers=['fast']),
        model('hidden', hidden=True),
    ])])
    result = asyncio.run(codex_catalog.catalog())
    assert [(row['model'], row['fast_supported']) for row in result] == [('gpt-fast', False), ('legacy', True)]


def test_catalog_cancellation_kills_its_owned_process(monkeypatch):
    server, _ = transport(monkeypatch, [])
    killed = []
    monkeypatch.setattr(codex_catalog.os, 'killpg', lambda pid, sig: killed.append(pid))
    original_write = server.write

    def write(data):
        if json.loads(data)['method'] == 'model/list':
            return
        original_write(data)
    server.write = write

    async def run():
        task = asyncio.create_task(codex_catalog.catalog())
        while len(server.messages) < 2:
            await asyncio.sleep(.001)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError('catalogue cancellation was swallowed')
    asyncio.run(run())
    assert killed == [server.pid]
    assert server.closed
