"""Revocable host-only browser credentials, separate from process bearer tokens."""
from collections.abc import Callable
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading

COOKIE = 'oko_browser'
MAX_AGE = 30 * 24 * 60 * 60


class BrowserSessions:
    def __init__(self, root: Path, clock: Callable[[], float]) -> None:
        self.path = root / 'browser-sessions.json'
        self.clock = clock
        self.lock = threading.Lock()
        self.sessions: dict[str, float] = {}
        if self.path.exists():
            data = json.loads(self.path.read_text())
            if not isinstance(data, dict) or any(not isinstance(k, str) or len(k) != 64 or
                    not isinstance(v, (float, int)) for k, v in data.items()):
                raise ValueError('Invalid saved browser sessions')
            self.sessions = data

    def _key(self, value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    def valid(self, value: str) -> bool:
        with self.lock:
            return bool(value) and self.sessions.get(self._key(value), 0) > self.clock()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_suffix('.tmp')
        descriptor = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(self.sessions, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.path)

    def create(self) -> str:
        with self.lock:
            now = self.clock()
            self.sessions = {k: v for k, v in self.sessions.items() if v > now}
            if len(self.sessions) >= 32:
                raise ValueError('Browser session limit reached; revoke an existing session')
            value = secrets.token_urlsafe(32)
            self.sessions[self._key(value)] = now + MAX_AGE
            self._save()
            return value

    def revoke(self, value: str) -> None:
        with self.lock:
            self.sessions.pop(self._key(value), None)
            self._save()
