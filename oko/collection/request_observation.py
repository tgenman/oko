"""Attempt-local observations at real external requests; no scheduling or storage."""
from contextlib import contextmanager
from contextvars import ContextVar
import time
import hashlib
import json
from typing import Any, Iterator

import httpx
import httpcore

from oko.diagnostics import exception_details, redact


active_capture: ContextVar['Capture | None'] = ContextVar('oko_collection_capture', default=None)
active_response: ContextVar[httpx.Response | None] = ContextVar('oko_collection_response', default=None)


class Capture:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.request_count = 0
        self.stage = ''
        self.error: dict[str, Any] | None = None
        self._seen: dict[str, tuple[int, float]] = {}
        self._groups: dict[str, dict[str, Any]] = {}

    @contextmanager
    def scope(self) -> Iterator['Capture']:
        token = active_capture.set(self)
        try:
            yield self
        finally:
            active_capture.reset(token)

    def failed(self, url: str, error: BaseException, *, purpose: str,
               method: str = 'GET', original_url: str | None = None,
               http_status: int | None = None) -> None:
        raw_url, raw_original = url, original_url
        observed: set[int] = getattr(error, '_oko_request_captures', set())
        if id(self) in observed:
            return
        setattr(error, '_oko_request_captures', {*observed, id(self)})
        url = redact(url)
        original_url = redact(original_url) if original_url else None
        details = exception_details(error)
        now = time.time()
        key = hashlib.sha256(json.dumps([method, url, details['error_type'], details['message']], ensure_ascii=False).encode()).hexdigest()
        message_bytes = details['message'].encode()
        message = message_bytes[:16384].decode('utf-8', errors='ignore')
        previous_count, first_at = self._seen.get(key, (0, now))
        if previous_count == 0:
            self.request_count += 1
        self._seen[key] = (previous_count + 1, first_at)
        old = self._groups.get(key)
        if old:
            old.update(count=previous_count + 1, occurred_at=now, last_at=now)
            self.requests.remove(old)
        else:
            old = dict(url=url, original_url=original_url if original_url != url else None,
                       method=method, purpose=purpose, occurred_at=now, http_status=http_status,
                       error_type=details['error_type'], message=message, message_truncated=len(message_bytes) > 16384,
                       count=previous_count + 1, first_at=first_at, last_at=now,
                       redacted=url != raw_url or original_url != raw_original)
        self._groups[key] = old
        self.requests.insert(0, old)
        del self.requests[100:]
        kept = {id(row) for row in self.requests}
        self._groups = {key: row for key, row in self._groups.items() if id(row) in kept}
        self.error = dict(details, occurred_at=now, origin='server')


def stage(value: str) -> None:
    if capture := active_capture.get():
        capture.stage = value


def failure(url: str, error: BaseException, *, purpose: str, method: str = 'GET',
            original_url: str | None = None, http_status: int | None = None) -> None:
    if capture := active_capture.get():
        capture.failed(url, error, purpose=purpose, method=method,
                       original_url=original_url, http_status=http_status)


def remote(details: dict[str, Any]) -> None:
    """Accept sanitized, bounded observations returned by an owned worker."""
    if capture := active_capture.get():
        for row in reversed(details.get('request_failures') or []):
            safe = {k: redact(v) if isinstance(v, str) else v for k, v in row.items()}
            capture.requests.insert(0, safe)
        del capture.requests[100:]
        capture.request_count += int(details.get('request_failure_count', len(details.get('request_failures') or [])))
        if details.get('error_type'):
            capture.error = {k: redact(str(details.get(k, ''))) for k in ('error_type', 'message', 'traceback', 'location')}
            capture.error.update(occurred_at=time.time(), origin='worker')


def worker_details(capture: Capture) -> dict[str, Any]:
    return dict(request_failures=capture.requests, request_failure_count=capture.request_count)


@contextmanager
def request(url: str, *, purpose: str, original_url: str | None = None,
            method: str = 'GET') -> Iterator[None]:
    stage({'feed': 'Получение публикаций', 'publication': 'Загрузка содержимого',
           'attachment': 'Загрузка вложения', 'worker': 'Ожидание worker'}.get(purpose, purpose))
    token = active_response.set(None)
    try:
        yield
    except (httpx.HTTPError, httpcore.NetworkError, httpcore.TimeoutException, TimeoutError, OSError) as error:
        response = getattr(error, 'response', None) or active_response.get()
        try:
            actual = str(getattr(getattr(error, 'request', None), 'url', '') or url)
        except RuntimeError:
            actual = url
        failure(actual, error, purpose=purpose, method=method, original_url=original_url or url,
                http_status=getattr(response, 'status_code', None))
        raise
    finally:
        active_response.reset(token)


def response_received(response: httpx.Response) -> None:
    active_response.set(response)


def response_failure(response: httpx.Response, *, purpose: str, original_url: str | None = None) -> None:
    response_received(response)
    if response.status_code < 400:
        return
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        failure(str(response.url), error, purpose=purpose, original_url=original_url,
                http_status=response.status_code, method=response.request.method)
