"""CodexClient adapter: failed creates are resolved by state, never replayed."""
import asyncio
import json
from pathlib import Path
import time
from typing import Any
import uuid

import httpx
from oko.processing.codex import UNAVAILABLE
from oko.processing.contracts import ProcessingError
from oko.processing.execution import CodexChoice
from .protocol import VERSION, JOB_SECONDS, MAX_REQUEST_BYTES, MAX_RESULT_BYTES, RENEW_SECONDS


class RemoteCodex:
    def __init__(self, url: str, token: str, *, socket_path: Path | None = None) -> None:
        self.socket_path = socket_path
        self.url = url.rstrip('/')
        self.token = token
        self.session_id = uuid.uuid4().hex

    async def catalog(self) -> list[dict[str, Any]]:
        value = await self._run('catalog')
        if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
            raise ProcessingError('Codex bridge returned invalid catalog')
        return value

    async def check(self) -> dict[str, Any]:
        try:
            value = await self._run('check')
            if not isinstance(value, dict):
                raise ProcessingError('Codex bridge returned invalid status')
            return value
        except ProcessingError:
            return dict(available=False, reason=UNAVAILABLE, limits_checked=False)

    async def complete(self, prompt: str, schema: dict[str, Any], directory: Path, choice: CodexChoice) -> str:
        value = await self._run('complete', prompt=prompt, schema=schema, choice=choice.model_dump())
        if not isinstance(value, str):
            raise ProcessingError('Codex bridge returned invalid result')
        return value

    async def _run(self, operation: str, **content: Any) -> Any:
        headers = {'Authorization': 'Bearer ' + self.token, 'X-Oko-Protocol': str(VERSION),
                   'X-Oko-Session': self.session_id}
        job_id = uuid.uuid4().hex
        path = '/v1/jobs/' + job_id
        boot: str | None = None
        created = False
        transport = httpx.AsyncHTTPTransport(uds=str(self.socket_path)) if self.socket_path is not None else None
        async with httpx.AsyncClient(base_url=self.url, headers=headers, timeout=3, trust_env=False, transport=transport) as client:
            async def request(method: str, route: str, **kwargs: Any) -> dict[str, Any]:
                async with client.stream(method, route, **kwargs) as response:
                    response.raise_for_status()
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > MAX_RESULT_BYTES + 4096:
                            raise ProcessingError('Codex bridge response exceeded limit')
                    value = json.loads(raw)
                    if not isinstance(value, dict):
                        raise ProcessingError('Codex bridge returned invalid protocol')
                    return value
            try:
                health = await request('GET', '/v1/health')
                if health.get('protocol') != VERSION:
                    raise ProcessingError('Codex bridge protocol mismatch')
                boot = str(health['boot_id'])
                client.headers['X-Oko-Boot'] = boot
                deadline = time.time() + JOB_SECONDS - 1
                payload = dict(boot_id=boot, session_id=self.session_id, operation=operation,
                               deadline=deadline, **content)
                raw = json.dumps(payload, ensure_ascii=False).encode()
                if len(raw) > MAX_REQUEST_BYTES:
                    raise ProcessingError('Codex request exceeded protocol limit')
                created = True  # From here, even a lost response requires cancellation.
                try:
                    state = await request('PUT', path, content=raw, headers={'Content-Type': 'application/json'})
                except httpx.TransportError:
                    # Unknown create outcome: only ask this boot about this same ID.
                    state = await request('GET', path)
                renewal = time.monotonic() + RENEW_SECONDS
                while state.get('state') == 'running':
                    if time.time() >= deadline:
                        raise ProcessingError('Codex bridge deadline exceeded')
                    await asyncio.sleep(.1)
                    if time.monotonic() >= renewal:
                        state = await request('POST', path + '/lease')
                        renewal = time.monotonic() + RENEW_SECONDS
                    else:
                        state = await request('GET', path)
                if state.get('state') != 'completed':
                    raise ProcessingError(str(state.get('error') or 'Codex bridge attempt interrupted'))
                return state['result']
            except (httpx.HTTPError, ValueError, KeyError):
                raise ProcessingError('Codex bridge unavailable; attempt interrupted') from None
            finally:
                if created and boot:
                    # Detached from the cancelled awaiting task. Lease is the fallback
                    # when the host is unreachable; no background renewer survives.
                    async def release() -> None:
                        try:
                            await request('DELETE', path)
                        except (httpx.HTTPError, ValueError, ProcessingError):
                            pass
                    cleanup = asyncio.create_task(release())
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        await cleanup
                        raise
