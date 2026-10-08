"""Oko-owned Telethon setup; references existing credentials without copying them.

Session identity/lock compatibility adapted from chigwell/telegram-mcp
telegram_mcp/singleton.py (Apache-2.0). Modified to a minimal POSIX lock.
See docs/licenses/telegram-mcp-Apache-2.0.txt.
"""
import fcntl
import hashlib
import os
from pathlib import Path
import tempfile
from typing import Any, TextIO

from dotenv import dotenv_values
from telethon import TelegramClient  # type: ignore[import-untyped]
from telethon.sessions import StringSession  # type: ignore[import-untyped]


class SessionLockError(Exception):
    pass


class SessionLock:
    def __init__(self, label: str, identity: str) -> None:
        directory = Path(tempfile.gettempdir()) / 'telegram-mcp-locks'
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / ('session-' + hashlib.sha256(identity.encode()).hexdigest()[:16] + '.lock')
        self.file: TextIO | None = None

    def acquire(self, *, grace_seconds: float = 0) -> None:
        file = self.path.open('a+')
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            file.close()
            raise SessionLockError() from None
        self.file = file
        file.seek(0)
        file.truncate()
        file.write(str(os.getpid()))
        file.flush()

    def release(self) -> None:
        if self.file:
            self.file.seek(0)
            self.file.truncate()
            fcntl.flock(self.file, fcntl.LOCK_UN)
            self.file.close()
            self.file = None


def session_identity(client: Any) -> str:
    filename = getattr(client.session, 'filename', None)
    return 'file:' + os.path.abspath(filename) if filename else 'string:' + str(client.session.save())


class ClientRegistry:
    def __init__(self, env_file: Path) -> None:
        if not env_file.is_file():
            raise ValueError('configuration')
        self.env_file = env_file.resolve()
        self.values = {key: value for key, value in dotenv_values(env_file).items() if value}
        self.config: dict[str, tuple[str, str]] = {}
        for kind in ('NAME', 'STRING'):
            prefix = 'TELEGRAM_SESSION_' + kind
            for name, value in self.values.items():
                if name == prefix:
                    self.config.setdefault('default', (kind, value))
                elif name.startswith(prefix + '_') and name != 'TELEGRAM_SESSION_STRINGS':
                    self.config[name[len(prefix)+1:].lower()] = (kind, value)
        if self.values.get('TELEGRAM_SESSION_STRINGS'):
            # Never silently pick a pool slot already used by another process.
            raise ValueError('session pools are not supported')
        if not self.config:
            raise ValueError('sessions')
        self.clients: dict[str, Any] = {}

    def accounts(self) -> list[str]:
        return sorted(self.config)

    def client(self, account: str) -> Any:
        if account not in self.config:
            raise ValueError('account')
        if account not in self.clients:
            kind, value = self.config[account]
            session: Any
            if kind == 'STRING':
                session = StringSession(value)
            else:
                path = Path(value)
                if not path.is_absolute():
                    path = self.env_file.parent / path
                if path.suffix != '.session':
                    path = Path(str(path) + '.session')
                if not path.is_file():
                    raise PermissionError('existing session is missing')
                session = str(path)
            kwargs: dict[str, Any] = dict(flood_sleep_threshold=0, request_retries=1, connection_retries=1,
                                          timeout=10, receive_updates=False)
            for field in ('device_model', 'system_version', 'app_version'):
                configured = self.values.get('TELEGRAM_' + field.upper())
                if configured:
                    kwargs[field] = configured
            def proxy(name: str) -> str | None:
                return self.values.get('TELEGRAM_PROXY_' + name + '_' + account.upper()) or self.values.get('TELEGRAM_PROXY_' + name)
            proxy_type = proxy('TYPE')
            if proxy_type:
                host, port = proxy('HOST'), int(proxy('PORT') or '0')
                if not host or not 1 <= port <= 65535:
                    raise ValueError('proxy')
                if proxy_type == 'mtproxy':
                    from telethon.network import ConnectionTcpMTProxyRandomizedIntermediate  # type: ignore[import-untyped]
                    secret = proxy('SECRET')
                    if not secret:
                        raise ValueError('proxy')
                    kwargs.update(proxy=(host, port, secret), connection=ConnectionTcpMTProxyRandomizedIntermediate)
                elif proxy_type in ('socks4', 'socks5', 'http'):
                    kwargs['proxy'] = dict(proxy_type=proxy_type, addr=host, port=port,
                                           username=proxy('USERNAME'), password=proxy('PASSWORD'),
                                           rdns=(proxy('RDNS') or 'true').lower() in ('1', 'true', 'yes', 'on'))
                else:
                    raise ValueError('proxy')
            self.clients[account] = TelegramClient(session, int(self.values['TELEGRAM_API_ID']), self.values['TELEGRAM_API_HASH'], **kwargs)
        return self.clients[account]
