"""Authenticated single-slot host service. Content lives only for its lease."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import hmac
import json
import multiprocessing
from multiprocessing.connection import Connection
import re
import shutil
import time
from typing import Any, AsyncIterator, Callable
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from oko.processing.codex import CodexClient, LocalCodex
from .protocol import (BridgeConfig, JobRequest, VERSION, LEASE_SECONDS, JOB_SECONDS,
                       MAX_REQUEST_BYTES, MAX_RECORDS, TOMBSTONE_SECONDS)
from .supervisor import supervise


@dataclass
class Job:
    session: str
    digest: str
    control: Connection | None
    process: Any
    lease: float
    mono_lease: float
    deadline: float
    state: str = 'running'
    result: Any = None
    error: str = ''
    finished: float = 0

    def view(self) -> dict[str, Any]:
        value: dict[str, Any] = {'state': self.state}
        if self.state == 'completed':
            value['result'] = self.result
        if self.error:
            value['error'] = self.error
        return value


class Bridge:
    def __init__(self, config: BridgeConfig, factory: Callable[[], CodexClient], on_fatal: Callable[[], None] | None = None) -> None:
        self.on_fatal = on_fatal
        self.supervisor_lost = False
        self.config = config
        token_path = config.token_file
        if token_path.stat().st_mode & 0o077:
            raise ValueError('bridge token file must be private (0600)')
        self.token = token_path.read_text().strip()
        if len(self.token) < 32:
            raise ValueError('bridge token must contain at least 32 characters')
        config.scratch_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if config.scratch_root.stat().st_mode & 0o077:
            raise ValueError('bridge scratch directory must be private (0700)')
        # Remove only our abandoned scratch names, under the same cross-boot
        # lock as supervisors. A surviving marker demands operator recovery.
        with (config.scratch_root / 'execution.lock').open('a+') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                if not (config.scratch_root / 'active.json').exists():
                    for stale in config.scratch_root.iterdir():
                        if re.fullmatch(r'job-[0-9]+', stale.name) and stale.is_dir() and not stale.is_symlink():
                            shutil.rmtree(stale)
        self.factory = factory
        self.boot_id = uuid.uuid4().hex
        self.jobs: dict[str, Job] = {}

    def cancel(self, job: Job) -> None:
        if job.control is not None:
            try:
                job.control.send('cancel')
            except (OSError, EOFError):
                pass
        job.lease = 0
        job.mono_lease = 0
        # Clear delivered/unclaimed content immediately; keep ID tombstone.
        job.result = None
        if job.state != 'running':
            job.state = 'released'
            job.finished = time.time()

    def update(self) -> None:
        now, mono = time.time(), time.monotonic()
        for job in self.jobs.values():
            if job.state == 'running' and job.control is not None:
                try:
                    if job.control.poll():
                        message = job.control.recv()
                        job.state = message['state']
                        job.result = message.get('result') if job.lease else None
                        if not job.lease:
                            job.state = 'released'
                        job.error = message.get('error', '')
                        if message['state'] == 'error' and (self.config.scratch_root / 'active.json').exists():
                            self.supervisor_lost = True
                        job.finished = now
                        job.control.close()
                        job.control = None
                    elif not job.process.is_alive():
                        job.state = 'error'
                        job.error = 'Codex supervisor stopped; recovery required'
                        self.supervisor_lost = True
                        job.finished = now
                        job.control.close()
                        job.control = None
                except (EOFError, OSError):
                    job.state = 'error'
                    job.error = 'Codex supervisor disconnected; recovery required'
                    self.supervisor_lost = True
                    job.finished = now
                    job.control = None
            if job.lease and (now >= min(job.lease, job.deadline) or mono >= job.mono_lease):
                self.cancel(job)
            if job.process is not None and not job.process.is_alive():
                job.process.join(timeout=0)
        self.jobs = {key: job for key, job in self.jobs.items()
                     if not job.finished or now - job.finished < TOMBSTONE_SECONDS}

    async def watch(self) -> None:
        while True:
            self.update()
            if self.supervisor_lost and self.on_fatal is not None:
                self.on_fatal()
                return
            await asyncio.sleep(.1)

    async def close(self) -> None:
        for job in self.jobs.values():
            self.cancel(job)
            if job.control:
                job.control.close()  # EOF is the supervisor's parent-death signal.
        for job in self.jobs.values():
            await asyncio.to_thread(job.process.join, 10)


def create_app(config: BridgeConfig, *, adapter_factory: Callable[[], CodexClient] = LocalCodex, on_fatal: Callable[[], None] | None = None) -> FastAPI:
    bridge = Bridge(config, adapter_factory, on_fatal)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        watcher = asyncio.create_task(bridge.watch())
        try:
            yield
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            await bridge.close()

    app = FastAPI(title='Oko private Codex bridge', version=str(VERSION), lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware('http')
    async def authenticate(request: Request, call_next: Any) -> Any:
        if request.headers.get('origin') is not None:
            return JSONResponse({'detail': 'browser origins forbidden'}, status_code=403)
        expected = 'Bearer ' + bridge.token
        if not hmac.compare_digest(request.headers.get('authorization', '').encode(), expected.encode()):
            return JSONResponse({'detail': 'unauthorized'}, status_code=401)
        if request.headers.get('x-oko-protocol') != str(VERSION):
            return JSONResponse({'detail': 'unsupported protocol'}, status_code=409)
        length = request.headers.get('content-length')
        if length and (not length.isdecimal() or int(length) > MAX_REQUEST_BYTES):
            return JSONResponse({'detail': 'request exceeds limit'}, status_code=413)
        # Bound chunked requests before Starlette's body collector sees them.
        size = 0
        original_receive = request._receive
        async def receive() -> Any:
            nonlocal size
            message = await original_receive()
            size += len(message.get('body', b''))
            if size > MAX_REQUEST_BYTES:
                raise HTTPException(413, 'request exceeds limit')
            return message
        request._receive = receive
        bridge.update()
        if bridge.supervisor_lost and bridge.on_fatal is not None:
            return JSONResponse({'detail': 'helper generation requires restart'}, status_code=503)
        return await call_next(request)

    def identity(request: Request) -> None:
        if request.headers.get('x-oko-boot') != bridge.boot_id:
            raise HTTPException(409, 'helper boot changed')

    def owned(job_id: str, request: Request) -> Job:
        identity(request)
        job = bridge.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, 'unknown attempt; do not recreate it')
        if request.headers.get('x-oko-session') != job.session:
            raise HTTPException(403, 'attempt belongs to another session')
        return job

    @app.get('/v1/health')
    async def health() -> dict[str, Any]:
        return {'protocol': VERSION, 'boot_id': bridge.boot_id, 'release': config.release,
                'lease_seconds': LEASE_SECONDS, 'max_request_bytes': MAX_REQUEST_BYTES}

    @app.put('/v1/jobs/{job_id}')
    async def create(job_id: str, request: Request) -> dict[str, Any]:
        identity(request)
        if not re.fullmatch('[0-9a-f]{32}', job_id):
            raise HTTPException(422, 'invalid attempt ID')
        try:
            data = await request.json()
            body = JobRequest.model_validate(data)
        except (ValueError, ValidationError, RecursionError):
            raise HTTPException(422, 'invalid bounded job request') from None
        if body.boot_id != bridge.boot_id or request.headers.get('x-oko-session') != body.session_id:
            raise HTTPException(409, 'identity mismatch')
        if body.operation == 'complete' and body.choice is None:
            raise HTTPException(422, 'choice required')
        if body.operation != 'complete' and (body.prompt or body.choice is not None or body.schema_):
            raise HTTPException(422, 'unexpected content for control operation')
        digest = hashlib.sha256(json.dumps(body.model_dump(by_alias=True), sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        previous = bridge.jobs.get(job_id)
        if previous:
            if previous.digest != digest or previous.session != body.session_id:
                raise HTTPException(409, 'attempt ID reused with different request')
            return previous.view()
        now = time.time()
        if not now < body.deadline <= now + JOB_SECONDS:
            raise HTTPException(422, 'deadline outside allowed interval')
        if any(job.state == 'running' or job.process.is_alive() for job in bridge.jobs.values()):
            raise HTTPException(409, 'Codex busy')
        if len(bridge.jobs) >= MAX_RECORDS:
            raise HTTPException(429, 'attempt history full; retry later with new ID')
        if (config.scratch_root / 'active.json').exists():
            raise HTTPException(503, 'supervisor recovery requires verified process cleanup')
        parent, child = multiprocessing.get_context('spawn').Pipe()
        process = multiprocessing.get_context('spawn').Process(target=supervise,
            args=(child, str(config.scratch_root), body.model_dump(by_alias=True), bridge.factory))
        process.start()
        child.close()
        job = Job(body.session_id, digest, parent, process, now + LEASE_SECONDS,
                  time.monotonic() + LEASE_SECONDS, body.deadline)
        bridge.jobs[job_id] = job
        return job.view()

    @app.get('/v1/jobs/{job_id}')
    async def state(job_id: str, request: Request) -> dict[str, Any]:
        return owned(job_id, request).view()

    @app.post('/v1/jobs/{job_id}/lease')
    async def renew(job_id: str, request: Request) -> dict[str, Any]:
        job = owned(job_id, request)
        if job.state == 'released' or not job.lease:
            raise HTTPException(409, 'attempt expired or released')
        job.lease = time.time() + LEASE_SECONDS
        job.mono_lease = time.monotonic() + LEASE_SECONDS
        if job.state == 'running' and job.control is not None:
            try:
                job.control.send('renew')
            except (OSError, EOFError):
                raise HTTPException(503, 'supervisor unavailable') from None
        return job.view()

    @app.delete('/v1/jobs/{job_id}')
    async def cancel(job_id: str, request: Request) -> dict[str, Any]:
        job = owned(job_id, request)
        bridge.cancel(job)
        # Acknowledge cancellation only once the owned supervisor confirms cleanup.
        until = time.monotonic() + 5
        while (job.state == 'running' or job.process.is_alive()) and time.monotonic() < until:
            await asyncio.sleep(.02)
            bridge.update()
        return job.view()

    return app
