"""Shared-reader access: password credentials and revocable sessions, never reader data."""
import asyncio
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import time
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from oko.reader import api_v3

MAX_AGE = 30 * 24 * 60 * 60
ASSETS = Path(__file__).with_name('login')
HEADERS = {'Cache-Control': 'no-store', 'Referrer-Policy': 'same-origin',
           'X-Content-Type-Options': 'nosniff',
           'Content-Security-Policy': "default-src 'none'; img-src 'self'; style-src 'self'; script-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"}


def password_hash(password: str, salt: str) -> str:
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=131072, r=8, p=1,
                          maxmem=160 * 1024 * 1024, dklen=32).hex()


def user_record(password: str) -> dict[str, str]:
    if not 12 <= len(password) <= 256:
        raise ValueError('Password must contain 12 to 256 characters')
    salt = secrets.token_hex(16)
    return {'salt': salt, 'hash': password_hash(password, salt), 'revision': secrets.token_hex(16)}


class PasswordAccess:
    cookie = 'oko_access'

    def __init__(self, root: Path, clock: Callable[[], float] = time.time):
        self.root, self.clock = root, clock
        self.path = root / 'access.json'
        self.lock = threading.RLock()
        self.hash_slots = asyncio.Semaphore(1)

    @contextmanager
    def state(self, *, write: bool = False) -> Iterator[dict[str, Any]]:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.lock, (self.root / 'access.lock').open('a') as lock:
            os.chmod(lock.name, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                value = json.loads(self.path.read_text()) if self.path.exists() else {'version': 1, 'users': {}, 'sessions': {}, 'attempts': []}
                if value['version'] != 1 or not isinstance(value['users'], dict) or not isinstance(value['sessions'], dict) or not isinstance(value['attempts'], list):
                    raise ValueError('Invalid access configuration')
                for name, user in value['users'].items():
                    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', name) or any(not re.fullmatch(pattern, user[field]) for field, pattern in [('salt', r'[a-f0-9]{32}'), ('hash', r'[a-f0-9]{64}'), ('revision', r'[a-f0-9]{32}')]):
                        raise ValueError('Invalid access configuration')
                for session in value['sessions'].values():
                    if not isinstance(session['user'], str) or not isinstance(session['revision'], str) or not isinstance(session['expires'], (int, float)):
                        raise ValueError('Invalid access configuration')
                for attempt in value['attempts']:
                    if not isinstance(attempt['name'], str) or not isinstance(attempt['at'], (int, float)):
                        raise ValueError('Invalid access configuration')
            except (OSError, ValueError, KeyError, TypeError):
                if write:
                    raise ValueError('Access configuration is damaged; inspect it before changing credentials') from None
                value = {'users': {}, 'sessions': {}, 'attempts': []}
            yield value
            if write:
                fd, name = tempfile.mkstemp(dir=self.root, prefix='.access-')
                try:
                    with os.fdopen(fd, 'w') as stream:
                        json.dump(value, stream)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(name, self.path)
                finally:
                    Path(name).unlink(missing_ok=True)

    def set_user(self, name: str, password: str | None) -> None:
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', name):
            raise ValueError('Login must use 1 to 64 ASCII letters, digits, dots, underscores or hyphens')
        record = user_record(password) if password is not None else None
        with self.state(write=True) as state:
            if record:
                state['users'][name] = record
            else:
                state['users'].pop(name, None)
            state['sessions'] = {k: v for k, v in state['sessions'].items() if v['user'] != name}

    def valid(self, credential: str) -> bool:
        if not credential or len(credential) > 128:
            return False
        with self.state() as state:
            session = state['sessions'].get(hashlib.sha256(credential.encode()).hexdigest())
            user = state['users'].get(session['user']) if session else None
            return bool(session and user and session['revision'] == user['revision'] and session['expires'] > self.clock())

    def revoke(self, credential: str) -> None:
        try:
            with self.state(write=True) as state:
                state['sessions'].pop(hashlib.sha256(credential.encode()).hexdigest(), None)
        except ValueError:
            pass  # Damaged configuration already denies every session.

    def login(self, name: str, password: str) -> tuple[str | None, int]:
        try:
            with self.state(write=True) as state:
                attempts = [a for a in state['attempts'] if a['at'] > self.clock() - 60]
                if len(attempts) >= 30 or sum(a['name'] == name and not a.get('success', False) for a in attempts) >= 5:
                    return None, 429
                attempt = {'name': name[:64], 'at': self.clock(), 'id': secrets.token_hex(16)}
                state['attempts'] = attempts + [attempt]
                user = state['users'].get(name)
            # Unknown logins cost the same work; only one derivation runs at a time.
            digest = password_hash(password, user['salt'] if user else '0' * 32)
            if not user or not secrets.compare_digest(digest, user['hash']):
                return None, 401
            credential = secrets.token_urlsafe(32)
            with self.state(write=True) as state:
                if state['users'].get(name) != user:
                    return None, 401
                for saved in state['attempts']:
                    if saved.get('id') == attempt['id']:
                        saved['success'] = True
                        break
                sessions = {k: v for k, v in state['sessions'].items() if v['expires'] > self.clock()}
                sessions[hashlib.sha256(credential.encode()).hexdigest()] = {
                    'user': name, 'revision': user['revision'], 'expires': self.clock() + MAX_AGE}
                state['sessions'] = sessions
            return credential, 200
        except (ValueError, OSError):
            return None, 503


def login_page(status: int = 200) -> HTMLResponse:
    text = (ASSETS / 'index.html').read_text()
    message = ('Слишком много попыток. Повторите через минуту.' if status == 429 else
               'Вход временно недоступен.' if status == 503 else 'Неверный логин или пароль.' if status != 200 else '')
    return HTMLResponse(text.replace('<!-- error -->', message), status_code=status, headers=HEADERS)


def install_login(app: FastAPI, access: PasswordAccess, public_url: str | None) -> None:
    @app.get('/login', include_in_schema=False)
    async def page() -> HTMLResponse:
        return login_page()

    @app.get('/login/style.css', include_in_schema=False)
    async def style() -> Response:
        return Response((ASSETS / 'style.css').read_text(), media_type='text/css', headers=HEADERS)

    @app.get('/login/session', include_in_schema=False)
    async def session(request: Request) -> Response:
        return Response(status_code=204 if access.valid(request.cookies.get(access.cookie, '')) else 401, headers=HEADERS)

    @app.post('/login', include_in_schema=False)
    async def login(request: Request) -> Response:
        if request.headers.get('content-type', '').split(';')[0] != 'application/x-www-form-urlencoded':
            return login_page(401)
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > 8192:
                return login_page(401)
        try:
            fields = parse_qs(content.decode('utf-8'), max_num_fields=2, strict_parsing=True)
            name, password = fields['username'][0], fields['password'][0]
            if len(fields['username']) != 1 or len(fields['password']) != 1 or len(name) > 64 or len(password) > 256:
                return login_page(401)
        except (ValueError, KeyError, UnicodeError):
            return login_page(401)
        # Bound work before entering the thread pool; queued requests are rejected.
        if access.hash_slots.locked():
            return login_page(429)
        async with access.hash_slots:
            credential, status = await asyncio.to_thread(access.login, name, password)
        if credential is None:
            return login_page(status)
        access.revoke(request.cookies.get(access.cookie, ''))
        response = RedirectResponse('/', status_code=303, headers=HEADERS)
        response.set_cookie(access.cookie, credential, max_age=MAX_AGE, httponly=True,
                            secure=bool(public_url and public_url.startswith('https://')), samesite='strict', path='/')
        return response

    @app.post('/logout', include_in_schema=False)
    async def logout(request: Request) -> Response:
        access.revoke(request.cookies.get(access.cookie, ''))
        response = RedirectResponse('/login#', status_code=303, headers=HEADERS)
        response.delete_cookie(access.cookie, path='/')
        return response


async def access_response(access: PasswordAccess, request: Request,
                          call_next: Callable[[Request], Awaitable[Response]],
                          origin: str, service_bearer: bool) -> Response | None:
    path = request.url.path
    if path == '/apple-touch-icon.png' and request.method in ('GET', 'HEAD'):
        return await call_next(request)
    if path == '/login' or path == '/login/style.css' or path == '/login/session':
        if request.method not in ('GET', 'HEAD') and (request.headers.get('origin') != origin or request.headers.get('sec-fetch-site') == 'cross-site'):
            return JSONResponse({'detail': 'Посторонняя страница'}, status_code=403, headers=HEADERS)
        return await call_next(request)
    valid = access.valid(request.cookies.get(access.cookie, ''))
    api = path == '/api' or path.startswith('/api/')
    # Browser traffic can never bootstrap a session from a service bearer.
    service = api and service_bearer and not request.headers.get('sec-fetch-site') and request.headers.get('sec-fetch-mode', '') in ('', 'cors') and not request.headers.get('origin')
    if not valid and not service:
        if api or path == '/logout':
            result = api_v3.error_response(403, api_v3.security_error('session_required'))
            result.headers.update(HEADERS)
            result.headers['X-Oko-Login-Required'] = '1'
            return result
        if request.method in ('GET', 'HEAD'):
            return RedirectResponse('/login#', status_code=303, headers=HEADERS)
        return Response(status_code=403, headers=HEADERS)
    if valid and request.method not in ('GET', 'HEAD') and (request.headers.get('origin') != origin or request.headers.get('sec-fetch-site') == 'cross-site'):
        return Response(status_code=403, headers=HEADERS)
    return None
