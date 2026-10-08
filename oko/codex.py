"""Codex admission is closed until isolation and copy control are verified."""

UNAVAILABLE = (
    'Codex недоступен: изоляция файлов и инструментов и отсутствие копий '
    'в логах не подтверждены. Материалы не отправлены.'
)

import asyncio
import os
import re
import shutil
import signal
import tempfile
from typing import Any


def status() -> dict[str, Any]:
    # This is a hard admission gate, not a claim that read-only or a CLI flag
    # protects files. There is deliberately no inference launch path.
    return dict(available=False, reason=UNAVAILABLE, limits_checked=False)


async def _probe(binary: str, directory: str, *args: str) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        binary, *args, cwd=directory, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        start_new_session=True)
    assert process.stdout is not None

    async def read() -> tuple[int, str]:
        assert process.stdout is not None
        output = bytearray()
        while chunk := await process.stdout.read(4096):
            output.extend(chunk)
            if len(output) > 16_384:
                raise ValueError('probe output exceeded limit')
        return await process.wait(), output.decode('utf-8', errors='replace')

    try:
        return await asyncio.wait_for(read(), timeout=5)
    finally:
        # Include descendants even if their parent already exited.
        # Only this owned process group. No shared Codex sessions are touched.
        if os.name == 'posix':
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif process.returncode is None:
            process.kill()
        await process.wait()


async def check() -> dict[str, Any]:
    result = status()
    result.update(version=None, auth='unchecked')
    binary = shutil.which('codex')
    if not binary:
        return dict(result, reason='Codex CLI не найден. ' + UNAVAILABLE)
    try:
        # No content, prompt, credentials copy or raw diagnostics are persisted.
        with tempfile.TemporaryDirectory(prefix='oko-codex-check-') as directory:
            code, version = await _probe(binary, directory, '--version')
            match = re.fullmatch(r'codex-cli (\d+\.\d+\.\d+)', version.strip().splitlines()[-1] if version.strip() else '')
            if code or not match:
                return dict(result, reason='Не удалось проверить версию Codex. ' + UNAVAILABLE)
            result['version'] = match[1]
            code, help_text = await _probe(binary, directory, 'exec', '--help')
            if code or not all(flag in help_text for flag in ('--ephemeral', '--ignore-user-config', '--ignore-rules', '--output-schema')):
                return dict(result, reason='CLI не поддерживает необходимые одноразовые задания. ' + UNAVAILABLE)
            code, auth = await _probe(binary, directory, 'login', 'status')
            if code:
                result['auth'] = 'missing'
            elif 'Logged in using ChatGPT' in auth:
                result['auth'] = 'chatgpt'
            elif 'Logged in using an API key' in auth or 'Logged in using API key' in auth:
                result['auth'] = 'api_key'
            else:
                result['auth'] = 'unknown'
            return result
    except (OSError, ValueError, asyncio.TimeoutError):
        return dict(result, reason='Проверка Codex не завершена; проверьте CLI. ' + UNAVAILABLE)
