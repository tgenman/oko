"""Pinned Linux package and a minimal bubblewrap filesystem for Codex only."""
import hashlib
import fcntl
import shutil
import uuid
from contextlib import contextmanager
from collections.abc import Iterator
import os
import tempfile
import json
from pathlib import Path
import platform
import re

from oko.processing.contracts import ProcessingError

PIN = json.loads(Path(__file__).with_name('codex_runtime.json').read_text())


def package_root(binary: Path) -> Path:
    return binary.parent.parent


def approved(binary: Path) -> bool:
    if platform.machine() not in ('x86_64', 'amd64') or not Path('/usr/bin/bwrap').is_file():
        return False
    try:
        root = package_root(binary)
        metadata = json.loads((root / 'codex-package.json').read_text())
        if metadata.get('version') != PIN['version'] or metadata.get('target') != PIN['target'] or metadata.get('entrypoint') != 'bin/codex':
            return False
        for path, digest in ((binary, PIN['binary_sha256']), (root / 'bin/codex-code-mode-host', PIN['code_mode_host_sha256'])):
            with path.open('rb') as source:
                if hashlib.file_digest(source, 'sha256').hexdigest() != digest:
                    return False
        return True
    except (OSError, ValueError):
        return False


def prefix(binary: Path, work: Path, auth: Path) -> list[str]:
    if not Path('/usr/bin/bwrap').is_file():
        raise ProcessingError('Linux sandbox bubblewrap не установлен; материалы не отправлены')
    root = package_root(binary).resolve()
    work, auth = work.resolve(), auth.resolve()
    if not auth.is_dir() or auth.stat().st_mode & 0o077 or work.stat().st_mode & 0o077:
        raise ProcessingError('Каталоги Codex должны быть закрыты (0700)')
    if work == auth or auth in work.parents or work in auth.parents:
        raise ProcessingError('Недопустимые каталоги изоляции Codex')
    args = ['/usr/bin/bwrap', '--die-with-parent', '--unshare-user', '--uid', '0', '--gid', '0',
            '--unshare-pid', '--unshare-ipc', '--unshare-uts',
            '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp']
    for path in ('/usr', '/lib', '/lib64', '/bin', '/etc/ssl', '/etc/resolv.conf', '/etc/hosts', '/etc/nsswitch.conf'):
        if Path(path).exists():
            args.extend(['--ro-bind', path, path])
    args.extend(['--ro-bind', str(root), str(root), '--bind', str(work), str(work),
                 '--bind', str(auth), str(auth), '--chdir', str(work), '--setenv', 'CODEX_HOME', str(auth), '--', str(binary),
                 '-c', 'forced_login_method="chatgpt"', '-c', 'cli_auth_credentials_store="file"'])
    return args


_CREDENTIALS = ('auth.json', 'installation_id', 'models_cache.json')


def _atomic(path: Path, data: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix='.refresh-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _copy_credentials(original: Path, view: Path) -> None:
    for name in _CREDENTIALS:
        source = original / name
        if not source.exists():continue
        if source.is_symlink() or not source.is_file():
            raise ProcessingError('Недопустимый файл авторизации Codex')
        data = source.read_bytes()
        if len(data) > 2_000_000:
            raise ProcessingError('Файл авторизации Codex превышает предел')
        _atomic(view / name, data)


def _commit_credentials(original: Path, view: Path, base: str) -> bool:
    source = view / 'auth.json'
    current = original / 'auth.json'
    if not source.is_file() or source.is_symlink() or not current.is_file() or current.is_symlink():
        return False
    updated, present = source.read_bytes(), current.read_bytes()
    if hashlib.sha256(present).hexdigest() != base and present != updated:
        return False  # A newer operator login always wins over a stale invocation.
    for name in _CREDENTIALS:
        candidate = view / name
        if not candidate.is_file() or candidate.is_symlink():continue
        data = candidate.read_bytes()
        if len(data) > 2_000_000:
            raise ProcessingError('Файл авторизации Codex превышает предел')
        if name == 'auth.json':
            value = json.loads(data)
            if value.get('auth_mode') != 'chatgpt' or value.get('OPENAI_API_KEY'):
                raise ProcessingError('Разрешена только авторизация ChatGPT')
        elif name == 'models_cache.json':json.loads(data)
        target = original / name
        if target.is_symlink():raise ProcessingError('Недопустимый файл авторизации Codex')
        if not target.exists() or target.read_bytes() != data:
            _atomic(target, data)
    return True


def _recover_credentials(original: Path) -> None:
    for pending in original.glob('.pending-*'):
        if len(pending.name) != len('.pending-') + 32 or pending.is_symlink() or not pending.is_dir():
            raise ProcessingError('Неизвестное состояние восстановления авторизации Codex')
        metadata = pending / 'recovery.json'
        # The recovery record is durable before any CLI can run. An unfinished
        # directory without it therefore cannot contain a refreshed credential.
        if metadata.exists():
            if metadata.is_symlink():raise ProcessingError('Недопустимое состояние авторизации Codex')
            value = json.loads(metadata.read_bytes())
            if value.get('version') != 1 or not isinstance(value.get('base'),str) or not re.fullmatch('[0-9a-f]{64}',value['base']):
                raise ProcessingError('Неизвестное состояние восстановления авторизации Codex')
            _commit_credentials(original, pending / 'home', value['base'])
        shutil.rmtree(pending)


@contextmanager
def credentials(original: Path) -> Iterator[Path]:
    """Credential-only view, atomic refresh and recovery after supervisor death.

    Pending views persist privately until commit/recovery so rotating refresh
    tokens survive SIGKILL. No ambient config, rules or skills enter the view.
    """
    if not (original / 'auth.json').is_file():
        with tempfile.TemporaryDirectory(prefix='oko-codex-auth-') as folder:
            yield Path(folder).resolve()
        return
    if original.stat().st_mode & 0o077:
        raise ProcessingError('Каталог авторизации Codex должен быть закрыт (0700)')
    with (original / '.credential.lock').open('a+') as lock:
        try:fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ProcessingError('Авторизация Codex обновляется другим запросом') from None
        _recover_credentials(original)
        pending = original / ('.pending-' + uuid.uuid4().hex)
        pending.mkdir(mode=0o700)
        view = pending / 'home'
        view.mkdir(mode=0o700)
        base = hashlib.sha256((original / 'auth.json').read_bytes()).hexdigest()
        _copy_credentials(original, view)
        _atomic(pending / 'recovery.json', json.dumps({'version':1,'base':base}).encode())
        parent = os.open(original, os.O_RDONLY)
        try:os.fsync(parent)
        finally:os.close(parent)
        try:
            yield view
        finally:
            committed = _commit_credentials(original, view, base)
            shutil.rmtree(pending)
            if not committed:
                raise ProcessingError('Авторизация Codex изменилась во время запроса')
