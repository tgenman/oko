"""Read the supported Codex catalogue without starting a thread or a turn."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import signal
import tempfile
from typing import Any

from oko.processing.codex import FEATURES, UNAVAILABLE, sandbox_command, supported_binary, invocation_auth
from oko.processing.contracts import ProcessingError


async def catalog() -> list[dict[str, Any]]:
    binary = await asyncio.to_thread(supported_binary)
    if binary is None:
        raise ProcessingError(UNAVAILABLE)
    try:
        with tempfile.TemporaryDirectory(prefix='oko-codex-catalog-') as folder, invocation_auth() as auth:
            work = Path(folder).resolve()
            # app-server does not expose exec's ignore flags. macOS denies
            # config reads; Linux requires a credential-only CODEX_HOME and
            # mounts no ambient user/project directories.
            args = [*sandbox_command(binary, work, auth), 'app-server']
            for feature in FEATURES:
                args.extend(['--disable', feature])
            args.extend(['-c', 'mcp_servers={}', '-c', 'analytics.enabled=false',
                         '-c', 'sqlite_home=' + json.dumps(str(work)),
                         '-c', 'log_dir=' + json.dumps(str(work))])
            env = {key: value for key, value in os.environ.items()
                   if key in ('HOME', 'CODEX_HOME', 'PATH', 'LANG', 'LC_ALL')}
            env.update(RUST_LOG='off', RUST_BACKTRACE='0', TMPDIR=str(work), CODEX_HOME=str(auth))
            process = await asyncio.create_subprocess_exec(
                *args, cwd=work, env=env, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True, limit=2_000_000)
            try:
                return await asyncio.wait_for(_receive(process), timeout=15)
            finally:
                # Always terminate only the owned process group, also on cancel.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.communicate()
    except (OSError, ValueError, TypeError, KeyError, asyncio.TimeoutError):
        raise ProcessingError('Не удалось получить каталог Codex; проверьте подключение') from None


async def _receive(process: asyncio.subprocess.Process) -> list[dict[str, Any]]:
    assert process.stdin is not None and process.stdout is not None
    reader, writer = process.stdout, process.stdin
    total = 0

    async def request(identifier: int, method: str, params: dict[str, Any]) -> dict[str, Any]:
        nonlocal total
        writer.write((json.dumps(dict(id=identifier, method=method, params=params)) + '\n').encode())
        await writer.drain()
        while line := await reader.readline():
            total += len(line)
            if total > 2_000_000:
                raise ValueError('catalogue exceeded bound')
            message = json.loads(line)
            if not isinstance(message, dict):
                raise ValueError('invalid envelope')
            if message.get('id') == identifier:
                result = message.get('result')
                if not isinstance(result, dict) or 'error' in message:
                    raise ValueError('catalogue request failed')
                return result
        raise ValueError('catalogue interrupted')

    await request(1, 'initialize', dict(clientInfo=dict(name='oko_catalog', title='Oko catalogue', version='1')))
    writer.write(b'{"method":"initialized"}\n')
    await writer.drain()
    rows: list[dict[str, Any]] = []
    cursors: set[str] = set()
    models: set[str] = set()
    cursor: str | None = None
    for identifier in range(2, 102):
        page = await request(identifier, 'model/list', dict(limit=100, includeHidden=False, cursor=cursor))
        data = page.get('data')
        if not isinstance(data, list):
            raise ValueError('invalid catalogue page')
        for raw in data:
            row = _normalise(raw)
            if row is not None:
                if row['model'] in models:
                    raise ValueError('duplicate model')
                models.add(row['model'])
                rows.append(row)
        cursor = page.get('nextCursor')
        if cursor is None:
            if not rows:
                raise ValueError('empty catalogue')
            return rows
        if not isinstance(cursor, str) or not cursor or cursor in cursors:
            raise ValueError('invalid cursor')
        cursors.add(cursor)
    raise ValueError('too many pages')


def _normalise(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        raise ValueError('invalid model')
    if raw.get('hidden') is True:
        return None
    model, name = raw.get('model'), raw.get('displayName')
    default = raw.get('isDefault')
    default_effort = raw.get('defaultReasoningEffort')
    options = raw.get('supportedReasoningEfforts')
    if (not isinstance(model, str) or not model.strip() or
            not isinstance(name, str) or not name.strip() or
            not isinstance(default, bool) or not isinstance(options, list)):
        raise ValueError('invalid model capabilities')
    efforts = []
    for option in options:
        effort = option.get('reasoningEffort') if isinstance(option, dict) else None
        if not isinstance(effort, str) or not effort or effort in efforts:
            raise ValueError('invalid reasoning capability')
        efforts.append(effort)
    if not isinstance(default_effort, str) or default_effort not in efforts:
        raise ValueError('invalid default reasoning effort')
    # The pinned CLI advertises Fast as serviceTiers id "priority";
    # older catalogues advertise additionalSpeedTiers ["fast"]. Unknown
    # capability metadata never grants Fast support.
    tiers = raw.get('serviceTiers', [])
    legacy = raw.get('additionalSpeedTiers', [])
    if (not isinstance(tiers, list) or not isinstance(legacy, list) or
            any(not isinstance(tier, dict) or not isinstance(tier.get('id'), str) for tier in tiers) or
            any(not isinstance(tier, str) for tier in legacy)):
        raise ValueError('invalid service tiers')
    fast = any(tier['id'] in ('priority', 'fast') for tier in tiers) or 'fast' in legacy
    return dict(model=model, display_name=name, default=default, reasoning_efforts=efforts,
                default_reasoning_effort=default_effort, fast_supported=fast)
