"""Versioned HTTP boundary; domain execution remains owned by Runtime.

Execution policy, neutral client presence and migrated domain families share
this boundary. Remaining operations migrate with their domain consumers.
"""
from collections.abc import Callable
from datetime import datetime, timezone
import secrets
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from oko.diagnostics import Journal
from oko.reader import projection_v3 as project
from oko.reader import snapshots as facts
from oko.reader.generated import models as wire
from oko.reader.lifetime import Lifetime
from oko.reader.runtime import Runtime


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


def register(app: FastAPI, runtime: Runtime, lifetime: Lifetime, wall_clock: Callable[[], float], journal: Journal) -> None:
    from oko.reader import settings_v3, commands_v3, processors_v3, diagnostics_v3, reading_v3, connections_v3
    from oko.reader import telegram_v3
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

    @app.get('/api/v3/status', response_model=wire.RuntimeStatus)
    async def status() -> wire.RuntimeStatus:
        old = facts.Status.model_validate(runtime.status())
        current = runtime._store if not runtime.stopped else None
        bindings = {feed['id']: wire.TelegramAccess.model_validate(binding) for feed in current.subscriptions.feeds() if (binding := current.subscriptions.telegram.binding(feed['id']))} if current else {}
        material_feeds = {row['id']:row['feed_id'] for row in current.db.execute('SELECT id,feed_id FROM items WHERE viewed=0')} if current else {}
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


def positive_version(value: str) -> int:
    # Bound before int conversion, including Python's very-large-integer limit.
    if not value.isascii() or not value.isdecimal() or value.startswith('0') or len(value) > 16:
        raise ApiError(422, invalid('path', 'number'))
    number = int(value)
    if number > 9007199254740991:
        raise ApiError(422, invalid('path', 'number'))
    return number


def no_query(request: Request) -> None:
    if request.query_params:
        raise ApiError(422, invalid('query', next(iter(request.query_params))))
