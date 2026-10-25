"""Bounded operational signals, without source contents or credential values."""
import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import stat
import sys
import time

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from starlette.types import ASGIApp, Message, Receive, Scope, Send

KINDS = frozenset(('discover', 'fetch', 'media', 'captions', 'filter', 'theses', 'delete', 'read', 'other'))
STATES = frozenset(('pending', 'running'))


def kind_label(kind: str) -> str:
    value = {'feed-delete': 'delete', 'download_media': 'media', 'download_captions': 'captions'}.get(kind, kind)
    return value if value in KINDS else 'other'


def private_metrics_token(path: Path) -> str:
    if not stat.S_ISREG(path.stat().st_mode) or path.stat().st_mode & 0o077:
        raise ValueError('Metrics credential must be a private regular file (0600)')
    with path.open() as source:
        value = source.read(1024).strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', value):
        raise ValueError('Invalid metrics credential')
    return value


def event(name: str, *, service: str = 'reader', level: str = 'info',
          job_id: str | int | None = None, kind: str | None = None,
          error: BaseException | None = None, elapsed: float | None = None) -> None:
    """Only controlled identifiers; never an exception message or arbitrary fields."""
    if (not re.fullmatch('[a-z_]{1,64}', name) or service not in ('reader', 'codex') or
        level not in ('debug', 'info', 'warning', 'error', 'fatal')):
        raise ValueError('Invalid operational event')
    value: dict[str, str | float] = dict(event=name, service=service, level=level)
    if job_id is not None and re.fullmatch(r'[a-zA-Z0-9:_.-]{1,128}', str(job_id)):
        value['job_id'] = str(job_id)
    if kind is not None:
        value['kind'] = kind if service == 'codex' and kind in ('check', 'catalog', 'complete') else kind_label(kind)
    if error is not None:
        value['error_type'] = type(error).__name__
    if elapsed is not None and math.isfinite(elapsed): value['elapsed_seconds'] = round(max(0., elapsed), 3)
    try:
        print(json.dumps(value, separators=(',', ':')), file=sys.stderr, flush=True)
    except (OSError, ValueError):
        pass  # A closed logging sink cannot change application work.


@dataclass(frozen=True)
class Snapshot:
    enabled: bool
    jobs: list[tuple[str, str, int, bool]]
    codex_available: bool
    codex_known: bool


class Metrics:
    def __init__(self, sample: Callable[[], Snapshot]) -> None:
        self.sample = sample
        self.codex_observation: tuple[bool, float] | None = None
        self.registry = CollectorRegistry()
        self.requests = Counter('oko_http_requests_total', 'Completed HTTP requests',
                                ('method', 'route', 'status_class'), registry=self.registry)
        self.duration = Histogram('oko_http_request_duration_seconds', 'Full HTTP response duration',
                                  ('method', 'route'), registry=self.registry,
                                  buckets=(.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10))
        self.store = Gauge('oko_store_available', 'Storage can be sampled', registry=self.registry)
        self.enabled = Gauge('oko_work_enabled', 'Collection and processing admission', registry=self.registry)
        self.jobs = Gauge('oko_jobs_current', 'Durable jobs by bounded kind/state',
                          ('kind', 'state'), registry=self.registry)
        self.errors = Gauge('oko_work_problem_groups_current', 'Durable work groups with a problem',
                            ('kind',), registry=self.registry)
        self.codex = Gauge('oko_codex_available', 'Cached processing availability; never probes on scrape', registry=self.registry)
        self.codex_known = Gauge('oko_codex_status_known', 'A worker or explicit UI check was observed within five minutes', registry=self.registry)
        self.attempts = Counter('oko_work_attempts_total', 'Observed worker attempts', ('kind', 'outcome'), registry=self.registry)
        self.work_duration = Histogram('oko_work_attempt_duration_seconds', 'Worker execution including external waits',
                                       ('kind',), registry=self.registry, buckets=(.01, .1, .5, 1, 2.5, 5, 10, 30, 60, 180))

    def attempt(self, kind: str, outcome: str, elapsed: float) -> None:
        if outcome not in ('returned', 'accepted', 'error', 'cancelled', 'stale'):
            raise ValueError('Invalid attempt outcome')
        self.attempts.labels(kind_label(kind), outcome).inc()
        self.work_duration.labels(kind_label(kind)).observe(max(0., elapsed))

    def observe_codex(self, available: bool) -> None:
        self.codex_observation = available, time.monotonic()

    def render(self) -> bytes:
        self.jobs.clear(); self.errors.clear()
        try:
            value = self.sample()
        except Exception as error:
            self.store.set(0); self.enabled.set(0); self.codex.set(0); self.codex_known.set(0)
            event('metrics_sample_failed', level='error', error=error)
        else:
            self.store.set(1); self.enabled.set(value.enabled)
            self.codex.set(value.codex_available); self.codex_known.set(value.codex_known)
            if self.codex_observation is not None:
                available, observed = self.codex_observation
                recent = time.monotonic()-observed < 300
                self.codex.set(available and recent); self.codex_known.set(recent)
            counts = {(kind, state): 0 for kind in KINDS for state in STATES}
            errors = {kind: 0 for kind in KINDS}
            for kind, state, count, failed in value.jobs:
                kind = kind_label(kind)
                if (kind, state) in counts: counts[kind, state] += max(0, count)
                if kind in errors and failed: errors[kind] += 1
            for (kind, state), count in counts.items(): self.jobs.labels(kind, state).set(count)
            for kind, count in errors.items(): self.errors.labels(kind).set(count)
        return generate_latest(self.registry)


class RequestMetrics:
    def __init__(self, app: ASGIApp, metrics: Metrics) -> None:
        self.app, self.metrics = app, metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http' or scope.get('path') == '/metrics':
            await self.app(scope, receive, send); return
        started, status, cancelled = time.monotonic(), 500, False
        async def response(message: Message) -> None:
            nonlocal status
            if message['type'] == 'http.response.start': status = message['status']
            await send(message)
        try:
            await self.app(scope, receive, response)
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            method = scope.get('method', 'OTHER')
            if method not in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'): method = 'OTHER'
            route = getattr(scope.get('route'), 'path', 'unmatched')
            if not isinstance(route, str): route = 'unmatched'
            self.metrics.requests.labels(method, route, 'cancelled' if cancelled else str(status // 100)+'xx').inc()
            self.metrics.duration.labels(method, route).observe(time.monotonic()-started)
            if status >= 500 and not cancelled: event('http_request_failed', level='error')
