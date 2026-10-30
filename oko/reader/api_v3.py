from oko.execution import read_locked
"""Versioned HTTP boundary; domain execution remains owned by ReaderApplication.

Execution policy, neutral client presence and migrated domain families share
this boundary. Remaining operations migrate with their domain consumers.
"""
from collections.abc import Callable
from datetime import datetime, timezone
import secrets
import hashlib
import json
from typing import Literal

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response

from oko.diagnostics import Journal
from oko.reader import projection_v3 as project
from oko.reader import snapshots as facts
from oko.reader.generated import models as wire
from oko.reader.lifetime import Lifetime
from oko.application.reader import ReaderApplication
from oko.storage.reading import stable_reader_time


class ApiError(Exception):
    def __init__(self, status: int, value: wire.ErrorEnvelope) -> None:
        self.status, self.value = status, value


def timestamp(value: float) -> wire.Timestamp:
    return wire.Timestamp(datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z'))


def invalid(location: Literal['header', 'path', 'body', 'query'], segment: str) -> wire.ErrorEnvelope:
    # Do not echo values, pydantic input/ctx, credentials or header contents.
    return wire.ErrorEnvelope(wire.RequestInvalidError(
        code='request_invalid', message='Некорректный запрос', diagnostic_id=None, command_id=None,
        details=wire.RequestInvalidErrorDetails(violations=[wire.FieldViolation(
            location=location, segments=[segment] if segment else [],
            code='invalid_value', message='Недопустимое значение')]),
    ))


def security_error(code: str) -> wire.ErrorEnvelope:
    if code == 'host_not_allowed':
        return wire.ErrorEnvelope(wire.HostNotAllowedError(code='host_not_allowed', message='Недопустимый адрес', details=None, diagnostic_id=None, command_id=None))
    if code == 'origin_not_allowed':
        return wire.ErrorEnvelope(wire.OriginNotAllowedError(code='origin_not_allowed', message='Посторонняя страница', details=None, diagnostic_id=None, command_id=None))
    return wire.ErrorEnvelope(wire.SessionRequiredError(code='session_required', message='Требуется локальная сессия', details=None, diagnostic_id=None, command_id=None))


def disabled_error(command_id: str | None = None) -> ApiError:
    return ApiError(409, wire.ErrorEnvelope(wire.RuntimeDisabledError(code='runtime_disabled', message='Око выключено; сначала включите Око', details=None, diagnostic_id=None, command_id=wire.CommandId(command_id) if command_id else None)))


def error_response(status: int, value: wire.ErrorEnvelope) -> JSONResponse:
    return JSONResponse(value.model_dump(mode='json'), status_code=status, headers={'Cache-Control': 'no-store'})


def register(app: FastAPI, runtime: ReaderApplication, lifetime: Lifetime, wall_clock: Callable[[], float], journal: Journal) -> None:
    from oko.reader import settings_v3, commands_v3, processors_v3, diagnostics_v3, reading_v3, connections_v3
    from oko.reader import telegram_v3
    from oko.reader import collection_v3
    collection_v3.register(app, runtime)
    telegram_v3.register(app, runtime)
    connections_v3.register(app, runtime)
    reading_v3.register(app, runtime)
    settings_v3.register(app, runtime)
    commands_v3.register(app, runtime)
    processors_v3.register(app, runtime)
    diagnostics_v3.register(app, runtime, journal)

    @app.exception_handler(ApiError)
    async def api_error(request: Request, error: ApiError) -> JSONResponse:
        return error_response(error.status, error.value)

    def policy() -> None:
        if lifetime.mode == 'explicit':
            raise ApiError(409, wire.ErrorEnvelope(wire.LifetimePolicyIncompatibleError(
                code='lifetime_policy_incompatible', message='Клиентские сессии не используются при явной остановке', details=None, diagnostic_id=None, command_id=None)))

    def running() -> None:
        if runtime.stopped:
            raise ApiError(409, wire.ErrorEnvelope(wire.RuntimeStoppedError(
                code='runtime_stopped', message='Око остановлено', details=None, diagnostic_id=None, command_id=None)))

    def validate_identity(value: str) -> wire.ClientSessionId:
        try:
            return wire.ClientSessionId(value)
        except ValueError:
            raise ApiError(422, invalid('path', 'identity')) from None

    def session(value: str) -> wire.ClientSession:
        return wire.ClientSession(id=wire.ClientSessionId(value),
            expires_at=timestamp(wall_clock() + lifetime.remaining(value)),
            renew_after_seconds=lifetime.renew_after_seconds)

    status_storage = ''
    final_status: wire.RuntimeStatus | None = None
    status_version: object = 0
    status_changed: float | None = None

    def status_tag(details: bool) -> str:
        nonlocal status_storage,status_version,status_changed
        queries=runtime.final_queries if runtime.has_storage and runtime._error is None else None
        boundaries = None
        source_revision = None
        if queries is not None:
            status_storage=queries.versions.identity
            version=queries.versions.version('status','runtime')
            if version is not None:status_version=version['version'];status_changed=version['changed']
            boundaries = queries.versions.time_boundaries()
            source_revision = queries.versions.source_revision()
        stamp=json.dumps([status_storage,status_version,details,metadata(),boundaries,source_revision],sort_keys=True,separators=(',',':')).encode()
        return '"'+hashlib.sha256(stamp).hexdigest()+'"'

    def status_response(request: Request, details: bool) -> Response:
        nonlocal status_storage
        etag=status_tag(details)
        headers={'ETag':etag,'X-Oko-Storage-Id':status_storage}
        if request.headers.get('if-none-match')==etag:return Response(status_code=304,headers=headers)
        value = status_data().model_dump(mode='json')
        # Reconciliation can change the saved status version during projection.
        etag=status_tag(details)
        if status_changed is not None:value['problems']['updated_at']=timestamp(status_changed).model_dump(mode='json')
        if not details:
            def progress(phase: str) -> dict[str, object]:
                entries = [entry['progress'] for entry in value['work'] if entry['phase']==phase] if value['work_allowed'] else []
                return dict(state='running' if entries else 'idle',
                    completed=None if entries else 0,
                    total=None if entries else 0)
            summary = {key:value[key] for key in ('api_version','runtime_state','lifetime','work_allowed','active_client_sessions',
                'sources','diagnostic_count','diagnostic_persistence_error','viewed_ids')}
            summary.update(enabled=runtime.enabled,collection=progress('collecting'),processing=progress('processing'),
                problems=[dict(id=problem['id'],message=problem['message'],
                    feed_id=next((ref['feed_id'] for ref in problem['affected'] if ref['kind']=='feed'),None))
                    for problem in value['problems']['current']])
            value = wire.StatusSummary.model_validate(summary).model_dump(mode='json')
        encoded=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
        headers={'ETag':etag,'X-Oko-Storage-Id':status_storage}
        if request.headers.get('if-none-match')==etag:return Response(status_code=304,headers=headers)
        return Response(encoded,media_type='application/json',headers=headers)

    @app.get('/api/v3/status', dependencies=[Depends(no_query)])
    @read_locked(runtime.execution)
    async def status(request: Request) -> Response:
        now, wall = runtime._clock(), runtime._wall_clock()
        with stable_reader_time(wall-now, now, wall):
            return status_response(request,False)

    @app.get('/api/v3/status/details', dependencies=[Depends(no_query)])
    @read_locked(runtime.execution)
    async def status_details(request: Request) -> Response:
        now, wall = runtime._clock(), runtime._wall_clock()
        with stable_reader_time(wall-now, now, wall):
            return status_response(request,True)

    def status_data() -> wire.RuntimeStatus:
        if final_status is not None and runtime.stopped:return final_status
        snapshot = runtime.status_snapshot()
        old = facts.Status.model_validate(snapshot.state)
        current = runtime.queries if runtime.has_storage and not runtime.stopped else None
        bindings = {identity: wire.TelegramAccess.model_validate(binding) for identity, binding in snapshot.bindings.items()}
        identities = {obj.id for p in [*old.problems.current, *old.problems.recent] for obj in p.objects if obj.kind == 'material'} if isinstance(old.problems, facts.ProblemSnapshot) else set()
        material_feeds = current.material_feeds(identities) if current else {}
        policy: wire.ExplicitLifetimePolicy | wire.ClientPresenceLifetimePolicy
        if lifetime.mode == 'explicit':
            policy = wire.ExplicitLifetimePolicy(kind='explicit')
        else:
            policy = wire.ClientPresenceLifetimePolicy(kind='client_presence', lease_seconds=lifetime.lease_seconds,
                grace_seconds=lifetime.grace_seconds, renew_after_seconds=lifetime.renew_after_seconds)
        return wire.RuntimeStatus(api_version='3.0.0', runtime_state='stopped' if runtime.stopped else 'unavailable' if old.error else 'disabled' if not runtime.enabled else 'running',
            lifetime=wire.LifetimePolicy(policy), work_allowed=runtime._live(), active_client_sessions=lifetime.active_clients,
            work=[wire.WorkEntry(wire.WorkWaiting(phase='waiting',reason=wire.WaitingReason('storage_unavailable'),retry_at=None))] if old.error else [wire.WorkEntry(wire.WorkWaiting(phase='waiting',reason=wire.WaitingReason('client_presence_required'),retry_at=None))] if not lifetime.admitted() and not runtime.stopped else project.work(current.work_summary() if current else []),
            sources=[wire.SourceState(feed_id=wire.FeedId(value.feed_id), title=value.title,
                state='unavailable' if isinstance(value.error, str) and value.error else 'ready',
                reason=wire.WaitingReason('source_unavailable') if isinstance(value.error, str) and value.error else None,
                message=value.error if isinstance(value.error, str) else None,
                gap=value.gap if isinstance(value.gap, str) else None, changes=value.changes if isinstance(value.changes, str) else None) for value in old.sources],
            ready_sources=old.ready_sources, total_sources=old.total_sources,
            youtube=project.youtube(old.youtube if isinstance(old.youtube, facts.YouTube) else None),
            initial_histories=[project.history(value) for value in old.initial_histories] if isinstance(old.initial_histories, list) else [],
            telegram_reads=[wire.TelegramReadState(feed_id=wire.FeedId(value.feed_id),
                initial=wire.TelegramMessageId(str(value.initial)) if value.initial else None,
                confirmed=wire.TelegramMessageId(str(value.confirmed)) if value.confirmed else None, pending=value.pending,
                waiting=wire.WaitingReason('telegram_read_frontier') if value.waiting else None, error=value.error or None) for value in old.telegram_reads] if isinstance(old.telegram_reads, list) else [],
            problems=project.problems(old.problems,bindings,material_feeds,set(old.viewed_ids) if isinstance(old.viewed_ids,list) else set()) if isinstance(old.problems, facts.ProblemSnapshot) else wire.ProblemSnapshot(current=[], recent=[], count=0, updated_at=timestamp(wall_clock())),
            diagnostic_count=len(journal.records), diagnostic_persistence_error=journal.persistence_error or None,
            viewed_ids=[wire.MaterialId(value) for value in old.viewed_ids] if isinstance(old.viewed_ids, list) else [])

    from oko.reader import publication_v3
    publication_v3.register(app,runtime)
    def metadata() -> dict[str, object]:
        return dict(
        enabled=runtime.enabled,stopped=runtime.stopped,work_allowed=runtime._live(),
        clients=lifetime.active_clients,sources=sorted(runtime._sources),
        diagnostics=[(r['id'],r['count'],r['last_time']) for r in journal.records],diagnostic_error=journal.persistence_error,
        error=runtime._error,lifetime=lifetime.mode,
        lease=lifetime.lease_seconds if lifetime.mode!='explicit' else None,
        grace=lifetime.grace_seconds if lifetime.mode!='explicit' else None,
        renew=lifetime.renew_after_seconds if lifetime.mode!='explicit' else None)
    runtime.reader_advance=lambda: runtime.queries.versions.advance()

    async def finish_status() -> None:
        nonlocal final_status,status_storage
        if runtime.has_storage:
            queries=runtime.final_queries
            status_storage=queries.versions.identity
            queries.versions.advance()
            final_status=status_data()
            version=queries.versions.version('status','runtime')
            if version is not None:final_status.problems.updated_at=timestamp(version['changed'])
    runtime.reader_finish=finish_status

    @app.post('/api/v3/client-sessions', status_code=201, response_model=wire.ClientSession)
    async def create_client_session() -> wire.ClientSession:
        policy()
        running()
        value = secrets.token_urlsafe(24)
        lifetime.register(value)
        await runtime.admission_changed()
        return session(value)

    @app.put('/api/v3/client-sessions/{identity}', response_model=wire.ClientSession)
    async def renew_client_session(identity: str) -> wire.ClientSession:
        policy()
        running()
        validated = validate_identity(identity)
        if not lifetime.renew(validated.root):
            raise ApiError(410, wire.ErrorEnvelope(wire.ClientSessionExpiredError(
                code='client_session_expired', message='Клиентская сессия истекла',
                details=wire.ClientSessionExpiredErrorDetails(id=validated), diagnostic_id=None, command_id=None)))
        await runtime.admission_changed()
        return session(validated.root)

    @app.delete('/api/v3/client-sessions/{identity}', response_model=wire.ClientSessionClosed)
    async def close_client_session(identity: str) -> wire.ClientSessionClosed:
        policy()
        validated = validate_identity(identity)
        lifetime.close(validated.root)
        await runtime.admission_changed()
        return wire.ClientSessionClosed(id=validated, closed=True)

    @app.put('/api/v3/power', response_model=wire.CommandAcknowledgement)
    async def power(request: Request, body: wire.PowerRequest) -> wire.CommandAcknowledgement:
        from oko.reader.commands_v3 import track
        no_query(request)
        async with track(runtime, request, wire.CommandContext(wire.CommandPower(operation='power', enabled=body.enabled))):
            await runtime.set_power(body.enabled)
        return wire.CommandAcknowledgement(ok=True, command_id=wire.CommandId(request.state.command_id))

    @app.post('/api/v3/stop', response_model=wire.CommandAcknowledgement)
    async def stop(request: Request) -> wire.CommandAcknowledgement:
        await runtime.stop()
        return wire.CommandAcknowledgement(ok=True, command_id=wire.CommandId(request.state.command_id))


def scalar_boolean(request: Request, name: str, *, default: bool = False) -> bool:
    """Parse textual query lexemes, independently of strict JSON booleans."""
    values = request.query_params.getlist(name)
    if not values:
        return default
    if len(values) != 1 or values[0] not in ('true', 'false'):
        raise ApiError(422, invalid('query', name))
    return values[0] == 'true'


def no_query(request: Request) -> None:
    if request.query_params:
        raise ApiError(422, invalid('query', next(iter(request.query_params))))
