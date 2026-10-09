"""Ephemeral Codex CLI connection, isolated by an audited macOS OS profile."""

import asyncio
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import sys
import tempfile
from typing import Any, Protocol

from .processing import ProcessingError, ProcessingRequest, Theses, FilterResult, result_type, processing_instruction

UNAVAILABLE = (
    'Codex недоступен: изоляция файлов и инструментов и отсутствие копий '
    'в логах не подтверждены. Материалы не отправлены.'
)

def status() -> dict[str, Any]:
    # Until explicitly checked, do not advertise a verified connection.
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
        await process.communicate()


async def diagnose() -> dict[str, Any]:
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

# External CLI boundary. Tests replace the service; the real connection always
# applies the OS profile and owns its child process and scratch directory.

APPROVED_SHA256 = '2c056bf3bd3a0ba04cdaa6d1db84c81974e6785f5fd72deaa2a3fcdcfb573d10'
FEATURES = ('shell_tool', 'unified_exec', 'plugins', 'apps', 'memories', 'hooks',
            'multi_agent', 'computer_use', 'browser_use', 'in_app_browser',
            'browser_use_external', 'code_mode', 'code_mode_only',
            'workspace_dependencies', 'image_generation', 'goals', 'shell_snapshot')


class CodexClient(Protocol):
    async def check(self) -> dict[str, Any]: ...
    async def complete(self, prompt: str, schema: dict[str, Any], directory: Path) -> str: ...


def supported_binary() -> Path | None:
    path = shutil.which('codex')
    if sys.platform != 'darwin' or not path or not Path('/usr/bin/sandbox-exec').exists():
        return None
    binary = Path(path).resolve()
    try:
        with binary.open('rb') as handle:
            digest = hashlib.file_digest(handle, 'sha256').hexdigest()
        return binary if digest == APPROVED_SHA256 else None
    except OSError:
        return None


def sandbox_profile(binary: Path, directory: Path, auth_root: Path) -> str:
    # Parent/child inherit the same policy. No other program may be executed.
    # Root directory read is required by dyld, not a recursive root grant.
    reads = ['/System', '/usr/lib', '/private/etc', '/dev', '/usr/share/icu',
             '/private/var/db/timezone', str(binary.parent), str(directory.resolve())]
    allowed = ' '.join('(subpath ' + json.dumps(p) + ')' for p in reads)
    literals = ['/', '/Library/Preferences/com.apple.networkd.plist',
                '/Library/Preferences/com.apple.security.plist',
                str(auth_root / 'auth.json'), str(auth_root / 'installation_id')]
    allowed += ' ' + ' '.join('(literal ' + json.dumps(p) + ')' for p in literals)
    writes = '(subpath ' + json.dumps(str(directory.resolve())) + ')(literal "/dev/null")(literal ' + json.dumps(str(auth_root / 'installation_id')) + ')'
    return ('(version 1)(allow default)'
            '(deny file-read-data (require-not (require-any ' + allowed + ')))'
            '(deny file-write* (require-not (require-any ' + writes + ')))'
            '(deny process-exec (require-not (literal ' + json.dumps(str(binary)) + ')))')


async def run_isolated(args: list[str], prompt: bytes, directory: Path) -> tuple[int, bytes]:
    environment = {k: v for k, v in os.environ.items() if k in ('HOME', 'CODEX_HOME', 'PATH', 'LANG', 'LC_ALL')}
    environment.update(RUST_LOG='off', TMPDIR=str(directory), RUST_BACKTRACE='0')
    process = await asyncio.create_subprocess_exec(
        *args, cwd=directory, env=environment, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        start_new_session=True)

    async def read(stream: asyncio.StreamReader | None) -> bytes:
        assert stream is not None
        result = bytearray()
        while chunk := await stream.read(4096):
            result.extend(chunk)
            if len(result) > 2_000_000:
                raise ProcessingError('Ответ Codex слишком большой')
        return bytes(result)

    async def feed() -> None:
        assert process.stdin is not None
        try:
            process.stdin.write(prompt)
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            process.stdin.close()

    readers = [asyncio.create_task(read(process.stdout)), asyncio.create_task(read(process.stderr))]
    writer = asyncio.create_task(feed())
    waiter = asyncio.create_task(process.wait())
    group = asyncio.gather(*readers, writer, waiter)
    try:
        values = await asyncio.wait_for(group, timeout=120)
        output = values[0]
        assert isinstance(output, bytes)
        code = values[3]
        assert isinstance(code, int)
        return code, output
    except asyncio.TimeoutError:
        raise ProcessingError('Codex не завершил задание за 120 секунд') from None
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        for task in [*readers, writer, waiter]:
            task.cancel()
        group.cancel()
        await asyncio.gather(group, return_exceptions=True)
        await asyncio.gather(*readers, writer, waiter, return_exceptions=True)
        # Producers are dead, so residual output is bounded by pipe buffers.
        await process.communicate()


async def quota_before_turn(binary: Path, work: Path, auth: Path) -> list[dict[str, Any]]:
    # Only initialize and account/rateLimits/read. No thread/turn is created and
    # there is no source input. Config reads remain blocked by the OS profile.
    args = ['/usr/bin/sandbox-exec', '-p', sandbox_profile(binary, work, auth),
            str(binary), 'app-server']
    for feature in FEATURES:
        args.extend(['--disable', feature])
    args.extend(['-c', 'mcp_servers={}', '-c', 'sqlite_home=' + json.dumps(str(work)),
                 '-c', 'log_dir=' + json.dumps(str(work)), '-c', 'analytics.enabled=false'])
    env = {k: v for k, v in os.environ.items() if k in ('HOME', 'CODEX_HOME', 'PATH', 'LANG', 'LC_ALL')}
    env.update(RUST_LOG='off', TMPDIR=str(work), RUST_BACKTRACE='0')
    process = await asyncio.create_subprocess_exec(*args, cwd=work, env=env,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, start_new_session=True, limit=2_000_000)
    async def receive() -> dict[str, Any]:
        assert process.stdin is not None and process.stdout is not None
        messages = [{'id': 1, 'method': 'initialize', 'params': {'clientInfo': {'name': 'oko_quota', 'version': '1'}}},
                    {'method': 'initialized'}, {'id': 2, 'method': 'account/rateLimits/read'}]
        for message in messages:
            process.stdin.write((json.dumps(message) + '\n').encode())
        await process.stdin.drain()
        total = 0
        while line := await process.stdout.readline():
            total += len(line)
            if total > 2_000_000:
                raise ValueError('quota response too large')
            message = json.loads(line)
            if not isinstance(message, dict):
                raise ValueError('quota response is not an object')
            if message.get('id') == 2:
                result = message.get('result')
                if not isinstance(result, dict):
                    raise ProcessingError('Не удалось проверить лимиты Codex; проверьте вход')
                return result
        raise ProcessingError('Проверка лимитов Codex прервана')
    try:
        response = await asyncio.wait_for(receive(), timeout=15)
        buckets = response.get('rateLimitsByLimitId')
        if not isinstance(buckets, dict) or not buckets:
            buckets = {'codex': response.get('rateLimits')}
        windows = []
        for bucket in buckets.values():
            if not isinstance(bucket, dict):
                raise ProcessingError('Лимиты Codex не определены; материалы не отправлены')
            if bucket.get('rateLimitReachedType'):
                raise ProcessingError('Лимит Codex исчерпан; ожидание сброса')
            found = False
            for name in ('primary', 'secondary'):
                window = bucket.get(name)
                if window is None:
                    continue
                if not isinstance(window, dict):
                    raise ProcessingError('Лимиты Codex не определены; материалы не отправлены')
                used = window.get('usedPercent')
                if not isinstance(used, (int, float)) or isinstance(used, bool) or not 0 <= used <= 100:
                    raise ProcessingError('Лимиты Codex не определены; материалы не отправлены')
                found = True
                if used >= 100:
                    raise ProcessingError('Лимит Codex исчерпан; ожидание сброса')
                windows.append(dict(used_percent=used, resets_at=window.get('resetsAt')))
            if not found:
                raise ProcessingError('Лимиты Codex не определены; материалы не отправлены')
        return windows
    except (OSError, ValueError, TypeError, asyncio.TimeoutError):
        raise ProcessingError('Не удалось проверить лимиты Codex; материалы не отправлены') from None
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.communicate()


class LocalCodex:
    async def check(self) -> dict[str, Any]:
        result = await diagnose()
        if result.get('auth') == 'missing':
            return dict(result, reason='Авторизация Codex отсутствует; выполните вход в CLI')
        if result.get('auth') == 'unknown':
            return dict(result, reason='Способ авторизации Codex не подтверждён')
        binary = await asyncio.to_thread(supported_binary)
        if not binary or result.get('auth') not in ('chatgpt', 'api_key'):
            return result
        # Probe the actual OS profile before reporting readiness, without inference.
        try:
            with tempfile.TemporaryDirectory(prefix='oko-codex-check-') as folder:
                work = Path(folder).resolve()
                auth = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))).resolve()
                code, _ = await run_isolated(['/usr/bin/sandbox-exec', '-p',
                    sandbox_profile(binary, work, auth), str(binary), '--version'], b'', work)
                if code:
                    return dict(result, reason='Внешний sandbox Codex недоступен. ' + UNAVAILABLE)
                if result['auth'] != 'chatgpt':
                    return dict(result, reason='Предварительная проверка лимитов требует входа ChatGPT')
                windows = await quota_before_turn(binary, work, auth)
        except ProcessingError as error:
            return dict(result, reason=error.label)
        except OSError:
            return dict(result, reason='Внешний sandbox Codex недоступен. ' + UNAVAILABLE)
        return dict(result, available=True, local=False, reason='', limits_checked=True, quota_windows=windows)

    async def complete(self, prompt: str, schema: dict[str, Any], directory: Path) -> str:
        binary = await asyncio.to_thread(supported_binary)
        if not binary:
            raise ProcessingError(UNAVAILABLE)
        directory.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(prefix='codex-', dir=directory) as folder:
                work = Path(folder).resolve()
                auth = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))).resolve()
                schema_file = work / 'schema.json'
                schema_file.write_text(json.dumps(schema), encoding='utf-8')
                args = ['/usr/bin/sandbox-exec', '-p', sandbox_profile(binary, work, auth),
                        str(binary), 'exec', '--ephemeral', '--ignore-user-config',
                        '--ignore-rules', '--skip-git-repo-check', '--json', '-C', str(work)]
                for feature in FEATURES:
                    args.extend(['--disable', feature])
                args.extend(['-c', 'mcp_servers={}', '-c', 'web_search="disabled"',
                             '-c', 'tools.view_image=false', '-c', 'sqlite_home=' + json.dumps(str(work)),
                             '-c', 'log_dir=' + json.dumps(str(work)), '--output-schema', str(schema_file), '-'])
                code, output = await run_isolated(args, prompt.encode(), work)
                # Logs are disabled even though CLI requires an empty logs DB.
                log = work / 'logs_2.sqlite'
                if log.exists():
                    with closing(sqlite3.connect('file:' + str(log) + '?mode=ro', uri=True)) as db:
                        if db.execute('SELECT count(*) FROM logs').fetchone()[0]:
                            raise ProcessingError('Codex создал журнал; результат не принят')
                allowed = {'schema.json', 'state_5.sqlite', 'state_5.sqlite-wal', 'state_5.sqlite-shm',
                           'logs_2.sqlite', 'logs_2.sqlite-wal', 'logs_2.sqlite-shm',
                           'memories_1.sqlite', 'memories_1.sqlite-wal', 'memories_1.sqlite-shm',
                           'goals_1.sqlite', 'goals_1.sqlite-wal', 'goals_1.sqlite-shm'}
                if any(p.is_file() and p.name not in allowed for p in work.rglob('*')):
                    raise ProcessingError('Codex создал непредусмотренную копию; результат не принят')
                return finished_result(code, output)
        except OSError:
            raise ProcessingError('Изолированный запуск Codex недоступен') from None
        finally:
            try:
                directory.rmdir()
            except OSError:
                pass


def finished_result(code: int, output: bytes) -> str:
    text: str | None = None
    completed = False
    try:
        for line in output.splitlines():
            event = json.loads(line)
            if event.get('type') == 'turn.failed' or event.get('type') == 'error':
                message = str(event).lower()
                if 'limit' in message or 'quota' in message:
                    raise ProcessingError('Лимит Codex исчерпан; ожидание повтора')
                if any(word in message for word in ('unauthorized', 'authentication', '401')):
                    raise ProcessingError('Авторизация Codex недоступна')
                raise ProcessingError('Codex не завершил задание')
            if event.get('type') == 'item.completed':
                item = event['item']
                if item['type'] == 'agent_message':
                    text = item['text']
                elif item['type'] not in ('reasoning', 'error'):
                    raise ProcessingError('Codex попытался использовать инструмент; результат отклонён')
            if event.get('type') == 'turn.completed':
                completed = True
        if code or not completed or not isinstance(text, str):
            raise ProcessingError('Codex не завершил результат')
        return text
    except (ValueError, KeyError, TypeError):
        raise ProcessingError('Некорректный ответ Codex') from None


class Codex:
    def __init__(self, client: CodexClient) -> None:
        self.client = client

    async def check(self) -> dict[str, Any]:
        result = await self.client.check()
        if not result['available']:
            raise ProcessingError(result['reason'])
        if result.get('limits_checked') is not True:
            raise ProcessingError('Лимиты Codex не проверены; материалы не отправлены')
        return dict(result, local=False)

    async def generate(self, request: ProcessingRequest, directory: Path) -> Theses | FilterResult:
        if sum(len(s.text) + len(s.title) for s in request.sources) > 150_000:
            raise ProcessingError('Материал превышает лимит входа; текст не обрезан')
        prompt = processing_instruction(request) + ' Не используй инструменты.\n' + request.model_dump_json()
        result = await self.client.complete(prompt, result_type(request).model_json_schema(), directory)
        try:
            theses = result_type(request).model_validate_json(result)
            theses.validate_sources(request)
            return theses
        except ValueError:
            raise ProcessingError('Результат не соответствует схеме обработки') from None


async def check() -> dict[str, Any]:
    return await LocalCodex().check()
